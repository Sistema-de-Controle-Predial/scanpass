from Utilitarios import *
from RegistroAcesso import *
from typing import List

# ======================================================================
# AppPortaria
# ======================================================================
class AppPortaria:
    _RESET = "\033[0m"
    _APARENCIA = {
        StatusAcesso.AUTORIZADO: ("\033[92m", "ACESSO LIBERADO"),
        StatusAcesso.VEICULO_INATIVO: ("\033[93m", "VEÍCULO INATIVO - verificar com a administração"),
        StatusAcesso.NAO_CADASTRADO: ("\033[91m", "VEÍCULO NÃO CADASTRADO - acesso negado"),
        StatusAcesso.VISITANTE_MANUAL: ("\033[94m", "VISITANTE REGISTRADO MANUALMENTE"),
    }

    def __init__(self, id_porteiro: int, nome_operador: str) -> None:
        self._id_porteiro = id_porteiro
        self._nome_operador = nome_operador

    id_porteiro = Utilitarios._somente_leitura("_id_porteiro")
    nome_operador = Utilitarios._somente_leitura("_nome_operador")

    def exibir_alerta_visual(self, status: StatusAcesso) -> str:
        """Mostra no terminal um alerta colorido e devolve o texto exibido."""
        cor, mensagem = self._APARENCIA[status]
        print(f"{cor}[{status.value}] {mensagem}{self._RESET}")
        return mensagem

    def registrar_visitante_manual(self, placa: str, motivo: str) -> RegistroAcesso:
        registro = RegistroAcesso(
            tipo_movimento="ENTRADA",
            status_autorizacao=StatusAcesso.VISITANTE_MANUAL.value,
            placa=Utilitarios._normalizar_placa(placa),
            observacao=f"{motivo} (operador: {self._nome_operador})",
        )
        if not registro.salvar_no_banco():
            raise RuntimeError("Falha ao persistir o registro do visitante")
        return registro

    def consultar_ultimos_acessos(self, limite: int = 10) -> List[RegistroAcesso]:
        return RegistroAcesso.buscar(limite=limite)
