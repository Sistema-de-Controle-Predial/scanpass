from Utilitarios import Utilitarios
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import threading
import atexit
import logging

import os


# ======================================================================
# RegistroAcesso
# ======================================================================
class RegistroAcesso:
    """Um evento de entrada/saída, gravado em planilhas Excel (.xlsx).

    Como o volume de dados cresce sem parar, os registros são particionados
    em vários arquivos, em vez de uma planilha única que só aumenta:

        <pasta>/registros_acesso_2026-11-21.xlsx        (um arquivo por dia ou mês)
        <pasta>/registros_acesso_2026-11-21_p02.xlsx    (se a planilha encher)

    Ajustes, via RegistroAcesso.configurar_planilha(pasta, lote, particao):
      pasta     Onde ficam os arquivos. None = só memória (útil em testes).
      lote      Quantos registros acumular antes de gravar. 1 = grava a cada
                registro (mais seguro). Valores maiores gravam bem mais rápido,
                mas o que ainda está no lote se perde se o programa cair; o que
                sobrar é gravado ao encerrar o programa ou em descarregar().
      particao  "dia" (padrão) ou "mes".

    Limitações: use um único processo gravando na mesma pasta e mantenha os
    arquivos fechados no Excel durante a gravação (se a gravação falhar,
    salvar_no_banco() retorna False e o registro continua pendente, sendo
    gravado na próxima tentativa).

    Observação: o atributo `placa` não aparece no diagrama, mas é necessário
    para ligar o registro ao veículo (histórico do morador, deduplicação).
    """

    _COLUNAS = ["id", "data_hora", "tipo_movimento", "status_autorizacao", "placa", "observacao"]
    _ABA = "acessos"
    _LIMITE_LINHAS = 1_048_576  # máximo de linhas de uma planilha do Excel (com o cabeçalho)

    _pasta: Optional[Path] = Path("registros_acesso")
    _lote = 1
    _particao = "dia"
    _memoria: List["RegistroAcesso"] = []      # usado apenas quando pasta=None
    _pendentes: List["RegistroAcesso"] = []    # aguardando gravação (lote)
    _contador_id = 0
    _contador_pronto = False
    _trava = threading.RLock()
    _banco: Optional[Any] = None               # BancoDados; se definido, substitui as planilhas

    def __init__(
        self,
        tipo_movimento: str,
        status_autorizacao: str,
        placa: Optional[str] = None,
        observacao: str = "",
        data_hora: Optional[datetime] = None,
        id_registro: Optional[int] = None,
    ) -> None:
        self._id = id_registro
        self._data_hora = data_hora or datetime.now()
        self._tipo_movimento = tipo_movimento
        self._status_autorizacao = status_autorizacao
        self._placa = placa
        self._observacao = observacao

    id = Utilitarios._somente_leitura("_id")
    data_hora = Utilitarios._somente_leitura("_data_hora")
    tipo_movimento = Utilitarios._somente_leitura("_tipo_movimento")
    status_autorizacao = Utilitarios._somente_leitura("_status_autorizacao")
    placa = Utilitarios._somente_leitura("_placa")
    observacao = Utilitarios._somente_leitura("_observacao")

    # ---------------- configuração ----------------
    @classmethod
    def configurar_banco(cls, banco: Optional[Any]) -> None:
        """Passa a gravar/consultar no PostgreSQL (BancoDados). None volta para as planilhas."""
        with cls._trava:
            cls._banco = banco

    @classmethod
    def configurar_planilha(
        cls,
        pasta: Optional[str] = "registros_acesso",
        lote: int = 1,
        particao: str = "dia",
    ) -> None:
        """Define onde e como os registros são gravados (veja a docstring da classe)."""
        if particao not in ("dia", "mes"):
            raise ValueError("particao deve ser 'dia' ou 'mes'")
        if lote < 1:
            raise ValueError("lote deve ser >= 1")
        with cls._trava:
            cls.descarregar()  # não perde o que estava pendente na configuração anterior
            cls._pasta = Path(pasta) if pasta is not None else None
            cls._lote = lote
            cls._particao = particao
            cls._memoria = []
            cls._contador_id = 0
            cls._contador_pronto = False

    # ---------------- gravação ----------------
    def salvar_no_banco(self) -> bool:
        """Grava o registro na planilha. Retorna True em caso de sucesso.

        Com lote > 1, o registro pode ficar na fila (True) e ser gravado quando o
        lote encher. Se a gravação falhar, retorna False e o registro continua na
        fila para uma nova tentativa (chame de novo ou use descarregar()).
        """
        cls = RegistroAcesso
        if cls._banco is not None:
            if self._id is not None:      # já foi gravado
                return True
            try:
                self._id = cls._banco.salvar_registro(self)
                return True
            except Exception:
                logging.getLogger(__name__).exception("Falha ao gravar registro no banco")
                return False
        try:
            with cls._trava:
                if self._id is None:
                    cls._iniciar_contador()
                    cls._contador_id += 1
                    self._id = cls._contador_id
                    if cls._pasta is None:
                        cls._memoria.append(self)
                        return True
                    cls._pendentes.append(self)
                if cls._pasta is not None and len(cls._pendentes) >= cls._lote:
                    cls._gravar_pendentes()
                return True
        except Exception:
            return False

    @classmethod
    def descarregar(cls) -> bool:
        """Grava os registros que ainda estão no lote. True se nada ficou pendente."""
        with cls._trava:
            try:
                if cls._pendentes and cls._pasta is not None:
                    cls._gravar_pendentes()
                return not cls._pendentes
            except Exception:
                return False

    @classmethod
    def _iniciar_contador(cls) -> None:
        """Continua a numeração a partir do maior id já gravado nas planilhas."""
        if cls._contador_pronto:
            return
        if cls._pasta is not None:
            from openpyxl import load_workbook

            for arquivo in reversed(cls._arquivos()):
                wb = load_workbook(arquivo, read_only=True)
                try:
                    ids = [
                        linha[0]
                        for linha in wb[cls._ABA].iter_rows(min_row=2, max_col=1, values_only=True)
                        if linha[0] is not None
                    ]
                finally:
                    wb.close()
                if ids:
                    cls._contador_id = max(ids)
                    break
        cls._contador_pronto = True

    @classmethod
    def _chave(cls, data_hora: datetime) -> str:
        return data_hora.strftime("%Y-%m-%d" if cls._particao == "dia" else "%Y-%m")

    @classmethod
    def _caminho(cls, chave: str, parte: int = 1) -> Path:
        sufixo = f"_p{parte:02d}" if parte > 1 else ""
        return cls._pasta / f"registros_acesso_{chave}{sufixo}.xlsx"

    @classmethod
    def _arquivos(cls) -> List[Path]:
        """Arquivos de dados em ordem cronológica (ignora os temporários do Excel '~$')."""
        if cls._pasta is None or not cls._pasta.exists():
            return []
        return sorted(cls._pasta.glob("registros_acesso_*.xlsx"))

    @classmethod
    def _gravar_pendentes(cls) -> None:
        """Grava a fila agrupando por arquivo. Em caso de erro, o que não foi gravado permanece na fila."""
        grupos: Dict[str, List["RegistroAcesso"]] = {}
        for registro in cls._pendentes:
            grupos.setdefault(cls._chave(registro.data_hora), []).append(registro)
        for chave, registros in grupos.items():
            cls._gravar_lote(chave, registros)
            gravados = {id(r) for r in registros}
            cls._pendentes = [r for r in cls._pendentes if id(r) not in gravados]

    @classmethod
    def _gravar_lote(cls, chave: str, registros: List["RegistroAcesso"]) -> None:
        """Acrescenta os registros ao arquivo da partição (abrindo novas partes se encher)."""
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font

        cls._pasta.mkdir(parents=True, exist_ok=True)
        parte = 1
        while registros:
            caminho = cls._caminho(chave, parte)
            if caminho.exists():
                wb = load_workbook(caminho)
                ws = wb[cls._ABA]
            else:
                wb = Workbook()
                ws = wb.active
                ws.title = cls._ABA
                ws.append(cls._COLUNAS)
                for celula in ws[1]:
                    celula.font = Font(bold=True)
                for letra, largura in zip("ABCDEF", (10, 22, 16, 22, 12, 50)):
                    ws.column_dimensions[letra].width = largura
                ws.freeze_panes = "A2"

            livres = cls._LIMITE_LINHAS - ws.max_row
            if livres > 0:
                for registro in registros[:livres]:
                    ws.append(list(registro.para_dict().values()))
                    obs = ws.cell(row=ws.max_row, column=len(cls._COLUNAS))
                    if isinstance(obs.value, str) and obs.value.startswith("="):
                        obs.data_type = "s"  # texto digitado nunca vira fórmula no Excel
                registros = registros[livres:]
                temporario = caminho.with_name(caminho.name + ".tmp")
                wb.save(temporario)                # grava em arquivo temporário e troca,
                os.replace(temporario, caminho)    # para não corromper o original se falhar
            wb.close()
            parte += 1

    # ---------------- consulta ----------------
    @classmethod
    def buscar(
        cls,
        placas: Optional[Iterable[str]] = None,
        limite: Optional[int] = None,
    ) -> List["RegistroAcesso"]:
        """Busca registros (mais recentes primeiro), opcionalmente filtrando por placas.

        Lê os arquivos do mais novo para o mais antigo e para assim que reúne
        `limite` registros, então consultar os últimos acessos não abre o histórico inteiro.
        """
        if cls._banco is not None:
            return cls._banco.buscar_registros(placas=placas, limite=limite)
        filtro = set(placas) if placas is not None else None
        with cls._trava:
            itens = [r for r in cls._memoria + cls._pendentes if filtro is None or r.placa in filtro]
            for arquivo in reversed(cls._arquivos()):
                if limite is not None and len(itens) >= limite:
                    break
                itens += cls._ler_arquivo(arquivo, filtro)
        itens.sort(key=lambda r: r.data_hora, reverse=True)
        return itens if limite is None else itens[:limite]

    @classmethod
    def _ler_arquivo(cls, arquivo: Path, filtro: Optional[set]) -> List["RegistroAcesso"]:
        from openpyxl import load_workbook

        registros = []
        wb = load_workbook(arquivo, read_only=True)
        try:
            linhas = wb[cls._ABA].iter_rows(min_row=2, min_col=1, max_col=len(cls._COLUNAS), values_only=True)
            for id_, data_hora, tipo, status, placa, obs in linhas:
                if id_ is None or (filtro is not None and placa not in filtro):
                    continue
                registros.append(
                    cls(tipo, status, placa=placa, observacao=obs or "", data_hora=data_hora, id_registro=id_)
                )
        finally:
            wb.close()
        return registros

    # ---------------- utilidades ----------------
    def para_dict(self) -> Dict[str, Any]:
        return {
            "id": self._id,
            "data_hora": self._data_hora,
            "tipo_movimento": self._tipo_movimento,
            "status_autorizacao": self._status_autorizacao,
            "placa": self._placa,
            "observacao": self._observacao,
        }

    def __repr__(self) -> str:
        return (
            f"RegistroAcesso(id={self._id}, {self._data_hora:%d/%m/%Y %H:%M:%S}, "
            f"{self._tipo_movimento}, {self._status_autorizacao}, placa={self._placa})"
        )

# Garante que o lote pendente seja gravado quando o programa terminar normalmente.
atexit.register(RegistroAcesso.descarregar)
