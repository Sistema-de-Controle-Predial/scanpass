from Utilitarios import Utilitarios
from copy import copy
from datetime import date, datetime, time, timedelta
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
    """Um evento de entrada/saída, lido e gravado na planilha `passagens.xlsx`.

    A planilha (aba "Passagens") é a tabela de dados. Cada linha é uma passagem:

        Semana | Dia | Hora | Tipo | Bloco | Apartamento | Nome | Parentesco |
        Código de acesso | Placa | Modelo do veículo

    As colunas são localizadas pelo nome do cabeçalho (a ordem não importa) e as
    linhas existentes nunca são alteradas: novos registros são sempre acrescentados
    no fim, com a mesma formatação das linhas anteriores. O filtro automático da
    planilha é estendido para cobrir as linhas novas.

    Correspondência com os atributos da classe:

        Tipo ........... tipo_movimento  ("chegada" ou "saida"; "entrada" é aceito e vira "chegada")
        Placa .......... placa
        Semana/Dia/Hora  semana, dia, hora (e `data_hora`, veja abaixo)
        demais colunas . bloco, apartamento, nome, parentesco, codigo_acesso, modelo_veiculo

    A planilha não tem colunas para `status_autorizacao` e `observacao`, então
    duas colunas opcionais são criadas no fim da tabela na primeira gravação:
    "Status da autorização" e "Observação" (esta só se algum registro tiver
    observação). Se elas não existirem, os registros antigos ficam com status None.

    Semana e data/hora: a planilha guarda "Semana" (0, 1, 2...), dia da semana e
    hora (HH:MM), sem data de calendário. Para converter de/para datetime, informe
    em configurar_planilha() uma `data_inicio` (qualquer data da semana 0).
    Sem ela, `data_hora` só existe para registros criados com um datetime, e
    registros novos são gravados na última semana presente na planilha.
    A hora é gravada sem segundos.

    Ajustes, via RegistroAcesso.configurar_planilha(arquivo, lote, data_inicio, aba):
      arquivo      Caminho do .xlsx. None = só memória (útil em testes). Se o arquivo
                   não existir, é criado na primeira gravação.
      lote         Quantos registros acumular antes de gravar. 1 = grava a cada
                   registro (mais seguro, porém lento: o Excel é reescrito por
                   inteiro a cada gravação). Valores maiores gravam bem mais rápido,
                   mas o que ainda está no lote se perde se o programa cair; o que
                   sobrar é gravado ao encerrar o programa ou em descarregar().
      data_inicio  Data (date/datetime) de qualquer dia da semana 0.
      aba          Nome da aba. Padrão "Passagens".

    Limitações: use um único processo gravando na planilha e mantenha o arquivo
    fechado no Excel durante a gravação (se a gravação falhar, salvar_no_banco()
    retorna False e o registro continua pendente, sendo gravado na próxima tentativa).

    O `id` de um registro é o número da sua linha de dados (linha 2 do Excel = id 1).
    """

    _COLUNAS = [
        "Semana", "Dia", "Hora", "Tipo", "Bloco", "Apartamento", "Nome",
        "Parentesco", "Código de acesso", "Placa", "Modelo do veículo",
    ]
    _COL_STATUS = "Status da autorização"
    _COL_OBS = "Observação"
    _DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    _TIPOS = {"entrada": "chegada", "saída": "saida"}
    _LIMITE_LINHAS = 1_048_576  # máximo de linhas de uma planilha do Excel (com o cabeçalho)

    _arquivo: Optional[Path] = Path("passagens.xlsx")
    _aba = "Passagens"
    _lote = 1
    _data_inicio: Optional[date] = None        # segunda-feira da semana 0
    _memoria: List["RegistroAcesso"] = []      # usado apenas quando arquivo=None
    _pendentes: List["RegistroAcesso"] = []    # aguardando gravação (lote)
    _contador_id = 0
    _ultima_semana = 0
    _contador_pronto = False
    _trava = threading.RLock()
    _banco: Optional[Any] = None               # BancoDados; se definido, substitui a planilha

    def __init__(
        self,
        tipo_movimento: str,
        status_autorizacao: str,
        placa: Optional[str] = None,
        observacao: str = "",
        data_hora: Optional[datetime] = None,
        id_registro: Optional[int] = None,
        *,
        bloco: Optional[str] = None,
        apartamento: Optional[str] = None,
        nome: Optional[str] = None,
        parentesco: Optional[str] = None,
        codigo_acesso: Optional[str] = None,
        modelo_veiculo: Optional[str] = None,
        semana: Optional[int] = None,
        dia: Optional[str] = None,
        hora: Optional[Any] = None,
    ) -> None:
        self._id = id_registro
        self._tipo_movimento = self._TIPOS.get(str(tipo_movimento).lower(), tipo_movimento)
        self._status_autorizacao = status_autorizacao
        self._placa = placa
        self._observacao = observacao
        self._bloco = bloco
        self._apartamento = apartamento
        self._nome = nome
        self._parentesco = parentesco
        self._codigo_acesso = codigo_acesso
        self._modelo_veiculo = modelo_veiculo

        # Semana/dia/hora: informados diretamente (leitura da planilha) ou derivados de data_hora.
        self._data_hora = data_hora
        if semana is None and dia is None and hora is None:
            self._data_hora = data_hora or datetime.now()
        if dia is None and self._data_hora is not None:
            dia = self._DIAS[self._data_hora.weekday()]
        if hora is None and self._data_hora is not None:
            hora = self._data_hora
        if semana is None and self._data_hora is not None and RegistroAcesso._data_inicio is not None:
            semana = (self._data_hora.date() - RegistroAcesso._data_inicio).days // 7
            if semana < 0:
                raise ValueError("data_hora anterior à data_inicio configurada")
        self._semana = semana
        self._dia = dia
        self._hora = hora.strftime("%H:%M") if isinstance(hora, (datetime, time)) else hora

    id = Utilitarios._somente_leitura("_id")
    semana = Utilitarios._somente_leitura("_semana")
    dia = Utilitarios._somente_leitura("_dia")
    hora = Utilitarios._somente_leitura("_hora")
    tipo_movimento = Utilitarios._somente_leitura("_tipo_movimento")
    bloco = Utilitarios._somente_leitura("_bloco")
    apartamento = Utilitarios._somente_leitura("_apartamento")
    nome = Utilitarios._somente_leitura("_nome")
    parentesco = Utilitarios._somente_leitura("_parentesco")
    codigo_acesso = Utilitarios._somente_leitura("_codigo_acesso")
    placa = Utilitarios._somente_leitura("_placa")
    modelo_veiculo = Utilitarios._somente_leitura("_modelo_veiculo")
    status_autorizacao = Utilitarios._somente_leitura("_status_autorizacao")
    observacao = Utilitarios._somente_leitura("_observacao")

    @property
    def data_hora(self) -> Optional[datetime]:
        """Data e hora do evento (None se a planilha não tem data_inicio configurada)."""
        if self._data_hora is not None:
            return self._data_hora
        inicio = RegistroAcesso._data_inicio
        if inicio is None or self._semana is None or self._dia not in self._DIAS:
            return None
        try:
            h, m = (int(p) for p in str(self._hora).split(":")[:2])
        except ValueError:
            return None
        dia = inicio + timedelta(weeks=self._semana, days=self._DIAS.index(self._dia))
        return datetime(dia.year, dia.month, dia.day, h, m)

    def _chave_ordem(self) -> tuple:
        """Ordem cronológica baseada nas colunas da planilha (não depende de data_inicio)."""
        idx_dia = self._DIAS.index(self._dia) if self._dia in self._DIAS else 0
        return (self._semana or 0, idx_dia, str(self._hora or ""), self._id or 0)

    # ---------------- configuração ----------------
    @classmethod
    def configurar_banco(cls, banco: Optional[Any]) -> None:
        """Passa a gravar/consultar no PostgreSQL (BancoDados). None volta para a planilha."""
        with cls._trava:
            cls._banco = banco

    @classmethod
    def configurar_planilha(
        cls,
        arquivo: Optional[str] = "passagens.xlsx",
        lote: int = 1,
        data_inicio: Optional[date] = None,
        aba: str = "Passagens",
    ) -> None:
        """Define onde e como os registros são gravados (veja a docstring da classe)."""
        if lote < 1:
            raise ValueError("lote deve ser >= 1")
        with cls._trava:
            cls.descarregar()  # não perde o que estava pendente na configuração anterior
            cls._arquivo = Path(arquivo) if arquivo is not None else None
            cls._aba = aba
            cls._lote = lote
            if isinstance(data_inicio, datetime):
                data_inicio = data_inicio.date()
            if data_inicio is not None:
                data_inicio -= timedelta(days=data_inicio.weekday())  # segunda-feira da semana 0
            cls._data_inicio = data_inicio
            cls._memoria = []
            cls._contador_id = 0
            cls._ultima_semana = 0
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
                    self._completar_dados()
                    if self._semana is None:
                        self._semana = cls._ultima_semana
                    cls._contador_id += 1
                    self._id = cls._contador_id
                    if cls._arquivo is None:
                        cls._memoria.append(self)
                        return True
                    cls._pendentes.append(self)
                if cls._arquivo is not None and len(cls._pendentes) >= cls._lote:
                    cls._gravar_pendentes()
                return True
        except Exception:
            logging.getLogger(__name__).exception("Falha ao gravar registro na planilha")
            return False

    @classmethod
    def descarregar(cls) -> bool:
        """Grava os registros que ainda estão no lote. True se nada ficou pendente."""
        with cls._trava:
            try:
                if cls._pendentes and cls._arquivo is not None:
                    cls._gravar_pendentes()
                return not cls._pendentes
            except Exception:
                logging.getLogger(__name__).exception("Falha ao gravar o lote na planilha")
                return False

    def _completar_dados(self) -> None:
        """Preenche bloco, apartamento, nome etc. a partir do último registro com o mesmo
        código de acesso (ou, na falta dele, a mesma placa). Só preenche o que estiver vazio."""
        if self._nome is not None or not (self._codigo_acesso or self._placa):
            return
        try:
            pessoa = RegistroAcesso.buscar_pessoa(self._codigo_acesso, self._placa)
        except Exception:
            logging.getLogger(__name__).exception("Não foi possível consultar os dados do morador")
            return
        for campo, valor in (pessoa or {}).items():
            if getattr(self, "_" + campo) is None:
                setattr(self, "_" + campo, valor)

    @classmethod
    def _iniciar_contador(cls) -> None:
        """Continua a numeração a partir da última linha de dados da planilha."""
        if cls._contador_pronto:
            return
        if cls._arquivo is not None and cls._arquivo.exists():
            registros = cls._ler_arquivo(cls._arquivo, None)
            if registros:
                cls._contador_id = max(r._id for r in registros)
                cls._ultima_semana = max(r._semana or 0 for r in registros)
        cls._contador_pronto = True

    @classmethod
    def _gravar_pendentes(cls) -> None:
        """Grava a fila de uma vez. Em caso de erro, tudo permanece na fila."""
        cls._gravar_lote(cls._pendentes)
        cls._pendentes = []

    @staticmethod
    def _ultima_linha(ws) -> int:
        for n in range(ws.max_row, 1, -1):
            if any(c.value is not None for c in ws[n]):
                return n
        return 1

    @classmethod
    def _gravar_lote(cls, registros: List["RegistroAcesso"]) -> None:
        """Acrescenta os registros ao fim da aba, copiando a formatação da última linha."""
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        if cls._arquivo.exists():
            wb = load_workbook(cls._arquivo)
            if cls._aba in wb.sheetnames:
                ws = wb[cls._aba]
            else:
                ws = wb.create_sheet(cls._aba)
        else:
            cls._arquivo.parent.mkdir(parents=True, exist_ok=True)
            wb = Workbook()
            ws = wb.active
            ws.title = cls._aba
        try:
            if ws.max_row == 1 and ws["A1"].value is None:  # aba nova: cria o cabeçalho
                for i, nome in enumerate(cls._COLUNAS, start=1):
                    celula = ws.cell(row=1, column=i, value=nome)
                    celula.font = Font(bold=True, color="FFFFFF")
                    celula.fill = PatternFill("solid", fgColor="1F4E78")
                for i, largura in enumerate((9, 10, 8, 10, 8, 14, 31, 13, 19, 10, 21), start=1):
                    ws.column_dimensions[get_column_letter(i)].width = largura
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = f"A1:{get_column_letter(len(cls._COLUNAS))}1"

            colunas = {c.value: c.column for c in ws[1] if c.value is not None}
            extras = [cls._COL_STATUS]
            if any(r._observacao for r in registros):
                extras.append(cls._COL_OBS)
            for nome in extras:
                if nome not in colunas:
                    nova = ws.max_column + 1
                    cab = ws.cell(row=1, column=nova, value=nome)
                    cab._style = copy(ws.cell(row=1, column=nova - 1)._style)
                    ws.column_dimensions[get_column_letter(nova)].width = 22 if nome == cls._COL_STATUS else 50
                    colunas[nome] = nova
            faltando = [n for n in cls._COLUNAS if n not in colunas]
            if faltando:
                raise ValueError(f"Colunas ausentes na aba '{cls._aba}': {faltando}")

            ultima = cls._ultima_linha(ws)
            if ultima + len(registros) > cls._LIMITE_LINHAS:
                raise ValueError("A planilha atingiu o limite de linhas do Excel")
            modelo = ultima if ultima > 1 else None
            novos_ids = []
            for registro in registros:
                ultima += 1
                for nome, valor in registro._valores_por_coluna().items():
                    if nome not in colunas:
                        continue
                    celula = ws.cell(row=ultima, column=colunas[nome], value=valor)
                    if modelo is not None:
                        celula._style = copy(ws.cell(row=modelo, column=colunas[nome])._style)
                    if isinstance(valor, str) and valor.startswith("="):
                        celula.data_type = "s"  # texto digitado nunca vira fórmula no Excel
                novos_ids.append(ultima - 1)
            if ws.auto_filter.ref:  # estende o filtro para cobrir as linhas novas
                ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ultima}"

            temporario = cls._arquivo.with_name(cls._arquivo.name + ".tmp")
            wb.save(temporario)                     # grava em arquivo temporário e troca,
            os.replace(temporario, cls._arquivo)    # para não corromper o original se falhar
        finally:
            wb.close()
        for registro, id_ in zip(registros, novos_ids):
            registro._id = id_

    # ---------------- consulta ----------------
    @classmethod
    def buscar(
        cls,
        placas: Optional[Iterable[str]] = None,
        limite: Optional[int] = None,
        codigo_acesso: Optional[str] = None,
    ) -> List["RegistroAcesso"]:
        """Busca registros (mais recentes primeiro), opcionalmente filtrando por placas
        e/ou por código de acesso."""
        if cls._banco is not None:
            return cls._banco.buscar_registros(placas=placas, limite=limite)
        filtro = set(placas) if placas is not None else None

        def passa(r: "RegistroAcesso") -> bool:
            return (filtro is None or r.placa in filtro) and (
                codigo_acesso is None or r.codigo_acesso == str(codigo_acesso)
            )

        with cls._trava:
            itens = [r for r in cls._memoria + cls._pendentes if passa(r)]
            if cls._arquivo is not None and cls._arquivo.exists():
                itens += cls._ler_arquivo(cls._arquivo, passa)
        itens.sort(key=lambda r: r._chave_ordem(), reverse=True)
        return itens if limite is None else itens[:limite]

    @classmethod
    def buscar_pessoa(
        cls, codigo_acesso: Optional[str] = None, placa: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Dados do morador (bloco, apartamento, nome, parentesco, código, placa, modelo)
        a partir do último registro com esse código de acesso ou, sem código, essa placa."""
        if codigo_acesso:
            achados = cls.buscar(limite=1, codigo_acesso=codigo_acesso)
        elif placa:
            achados = cls.buscar(placas=[placa], limite=1)
        else:
            return None
        if not achados:
            return None
        r = achados[0]
        return {
            "bloco": r.bloco, "apartamento": r.apartamento, "nome": r.nome,
            "parentesco": r.parentesco, "codigo_acesso": r.codigo_acesso,
            "placa": r.placa, "modelo_veiculo": r.modelo_veiculo,
        }

    @classmethod
    def _ler_arquivo(cls, arquivo: Path, filtro: Optional[Any]) -> List["RegistroAcesso"]:
        """Lê a aba (somente leitura). `filtro` é uma função registro -> bool, ou None."""
        from openpyxl import load_workbook

        def texto(valor: Any) -> Optional[str]:
            return None if valor is None else str(valor)

        registros = []
        wb = load_workbook(arquivo, read_only=True)
        try:
            if cls._aba not in wb.sheetnames:
                return registros
            linhas = wb[cls._aba].iter_rows(values_only=True)
            cabecalho = next(linhas, None)
            if cabecalho is None:
                return registros
            pos = {nome: i for i, nome in enumerate(cabecalho) if nome is not None}

            def pega(linha: tuple, nome: str) -> Any:
                i = pos.get(nome)
                return linha[i] if i is not None and i < len(linha) else None

            for numero, linha in enumerate(linhas, start=1):  # numero = id (linha do Excel - 1)
                if all(v is None for v in linha):
                    continue
                semana = pega(linha, "Semana")
                registro = cls(
                    pega(linha, "Tipo"),
                    pega(linha, cls._COL_STATUS),
                    placa=texto(pega(linha, "Placa")),
                    observacao=pega(linha, cls._COL_OBS) or "",
                    id_registro=numero,
                    bloco=texto(pega(linha, "Bloco")),
                    apartamento=texto(pega(linha, "Apartamento")),
                    nome=texto(pega(linha, "Nome")),
                    parentesco=texto(pega(linha, "Parentesco")),
                    codigo_acesso=texto(pega(linha, "Código de acesso")),
                    modelo_veiculo=texto(pega(linha, "Modelo do veículo")),
                    semana=int(semana) if semana is not None else 0,
                    dia=texto(pega(linha, "Dia")),
                    hora=texto(pega(linha, "Hora")),
                )
                if filtro is None or filtro(registro):
                    registros.append(registro)
        finally:
            wb.close()
        return registros

    # ---------------- utilidades ----------------
    def _valores_por_coluna(self) -> Dict[str, Any]:
        """Valores na forma da planilha (texto para tudo, exceto Semana)."""
        return {
            "Semana": self._semana,
            "Dia": self._dia,
            "Hora": self._hora,
            "Tipo": self._tipo_movimento,
            "Bloco": self._bloco,
            "Apartamento": self._apartamento,
            "Nome": self._nome,
            "Parentesco": self._parentesco,
            "Código de acesso": self._codigo_acesso,
            "Placa": self._placa,
            "Modelo do veículo": self._modelo_veiculo,
            self._COL_STATUS: self._status_autorizacao,
            self._COL_OBS: self._observacao or None,
        }

    def para_dict(self) -> Dict[str, Any]:
        return {
            "id": self._id,
            "semana": self._semana,
            "dia": self._dia,
            "hora": self._hora,
            "data_hora": self.data_hora,
            "tipo_movimento": self._tipo_movimento,
            "status_autorizacao": self._status_autorizacao,
            "bloco": self._bloco,
            "apartamento": self._apartamento,
            "nome": self._nome,
            "parentesco": self._parentesco,
            "codigo_acesso": self._codigo_acesso,
            "placa": self._placa,
            "modelo_veiculo": self._modelo_veiculo,
            "observacao": self._observacao,
        }

    def __repr__(self) -> str:
        return (
            f"RegistroAcesso(id={self._id}, semana={self._semana}, {self._dia} {self._hora}, "
            f"{self._tipo_movimento}, {self._status_autorizacao}, nome={self._nome}, placa={self._placa})"
        )

# Garante que o lote pendente seja gravado quando o programa terminar normalmente.
atexit.register(RegistroAcesso.descarregar)
