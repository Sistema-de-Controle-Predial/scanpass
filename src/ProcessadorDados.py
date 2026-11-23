from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Iterable, Dict, List
from Utilitarios import StatusAcesso
from Veiculo import *
from RegistroAcesso import *

if TYPE_CHECKING:  # apenas para anotações de tipo
    import pandas as pd

class ProcessadorDados:
    COLUNAS = ["id", "data_hora", "tipo_movimento", "status_autorizacao", "placa", "observacao"]
    TIPOS_MOVIMENTO = ("ENTRADA", "SAIDA")

    def __init__(
        self,
        janela_deduplicacao_segundos: int = 10,
        veiculos: Optional[Iterable[Veiculo]] = None,
    ) -> None:
        self._janela_deduplicacao_segundos = janela_deduplicacao_segundos
        self._veiculos_por_hash: Dict[str, Veiculo] = {}
        if veiculos:
            self.carregar_veiculos(veiculos)

    @property
    def janela_deduplicacao_segundos(self) -> int:
        return self._janela_deduplicacao_segundos

    @janela_deduplicacao_segundos.setter
    def janela_deduplicacao_segundos(self, valor: int) -> None:
        if valor < 0:
            raise ValueError("A janela de deduplicação não pode ser negativa")
        self._janela_deduplicacao_segundos = valor

    # Métodos de apoio (não aparecem no diagrama): indexam os veículos pelo hash
    # do QR Code. Em produção, isso seria uma consulta ao banco de dados.
    def carregar_veiculos(self, veiculos: Iterable[Veiculo]) -> None:
        for v in veiculos:
            if v.qr_code_hash:
                self._veiculos_por_hash[v.qr_code_hash] = v

    def buscar_veiculo(self, hash_qr: str) -> Optional[Veiculo]:
        return self._veiculos_por_hash.get(hash_qr)

    # Métodos do diagrama
    def validar_acesso(self, hash: str) -> StatusAcesso:  # noqa: A002 (nome do diagrama)
        veiculo = self._veiculos_por_hash.get(hash)
        if veiculo is None:
            return StatusAcesso.NAO_CADASTRADO
        if not veiculo.validar_status():
            return StatusAcesso.VEICULO_INATIVO
        return StatusAcesso.AUTORIZADO

    def registrar_log(self, v: Veiculo, tipo: str) -> RegistroAcesso:
        tipo = tipo.upper()
        if tipo not in self.TIPOS_MOVIMENTO:
            raise ValueError(f"Tipo de movimento deve ser um de {self.TIPOS_MOVIMENTO}")

        status = StatusAcesso.AUTORIZADO if v.validar_status() else StatusAcesso.VEICULO_INATIVO
        registro = RegistroAcesso(tipo, status.value, placa=v.placa)
        if not registro.salvar_no_banco():
            raise RuntimeError("Falha ao persistir o registro de acesso")
        return registro

    def filtrar_duplicidades(self, logs: List[RegistroAcesso]) -> "pd.DataFrame":
        """Remove leituras repetidas (mesma placa e mesmo movimento) dentro da janela.

        Mantém a primeira leitura e descarta as seguintes que ocorram em menos de
        `janela_deduplicacao_segundos` segundos da última leitura mantida.
        """
        import pandas as pd

        df = pd.DataFrame([r.para_dict() for r in logs], columns=self.COLUNAS)
        if df.empty:
            return df

        df = df.sort_values("data_hora", kind="stable")
        ultima_mantida: Dict[tuple, Any] = {}
        manter = []
        for linha in df.itertuples():
            chave = (linha.placa, linha.tipo_movimento)
            anterior = ultima_mantida.get(chave)
            if anterior is None or (linha.data_hora - anterior).total_seconds() >= self._janela_deduplicacao_segundos:
                manter.append(linha.Index)
                ultima_mantida[chave] = linha.data_hora
        return df.loc[manter].reset_index(drop=True)
