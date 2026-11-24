"""
api.py - API REST do ScanPass (Flask).

Liga o front-end (React Native Web) às classes do projeto:
Morador, Veiculo, ProcessadorDados, RegistroAcesso, AppPortaria e DashboardAnalytics.

Instalação:
    pip install flask pandas openpyxl plotly

Executar:
    python api.py

Variáveis de ambiente (todas opcionais):
    SCANPASS_API_KEY           chave exigida no cabeçalho X-API-Key (RECOMENDADO definir)
    SCANPASS_CORS_ORIGEM       origem do front (ex.: http://localhost:8081). Padrão: *
    SCANPASS_JANELA_SEGUNDOS   janela anti-leitura-repetida do QR Code. Padrão: 10
    SCANPASS_PASTA_DADOS       pasta de dados. Padrão: ./dados
    SCANPASS_HOST / SCANPASS_PORT   Padrão: 127.0.0.1 / 5000

IMPORTANTE: rode UM único processo (sem gunicorn com vários workers). A gravação
nas planilhas e o cadastro em JSON não são seguros com vários processos.

Endpoints (todos em JSON; envie o cabeçalho X-API-Key se a chave estiver definida):
    GET   /api/saude
    GET   /api/moradores                      ?q=&bloco=&apartamento=
    POST  /api/moradores                      {nome, apartamento, bloco, telefone?}
    GET   /api/moradores/<id>
    GET   /api/moradores/<id>/historico       ?limite=50
    POST  /api/moradores/<id>/veiculos        {placa, modelo, cor}   -> devolve o qr_code
    GET   /api/veiculos                       ?placa=
    PATCH /api/veiculos/<id>                  {ativo: true|false}
    GET   /api/veiculos/<id>/qrcode
    POST  /api/veiculos/<id>/qrcode           (gera um QR Code novo; o antigo deixa de valer)
    POST  /api/acessos/validar                {qr_code, tipo?: ENTRADA|SAIDA, operador?}
    POST  /api/acessos/visitante              {placa, motivo, operador?}
    GET   /api/acessos                        ?limite=10&placa=
    GET   /api/dashboard/resumo               ?limite=5000
    GET   /api/dashboard/figura/<categorica|numerica|temporal>   (JSON do Plotly)
    GET   /api/dashboard/relatorio            (HTML para download)
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, abort, jsonify, request
from werkzeug.exceptions import HTTPException

# Os módulos têm o nome das classes (como no seu AppPortaria.py).
# Se os seus arquivos tiverem outros nomes, ajuste só estas linhas.
from AppPortaria import AppPortaria
from DashboardAnalitics import DashboardAnalytics
from Morador import Morador
from ProcessadorDados import ProcessadorDados
from RegistroAcesso import RegistroAcesso
from Veiculo import Veiculo

# ======================================================================
# Configuração
# ======================================================================
PASTA_DADOS = Path(os.environ.get("SCANPASS_PASTA_DADOS", Path(__file__).resolve().parent / "dados"))
ARQUIVO_CADASTRO = PASTA_DADOS / "cadastro.json"
API_KEY = os.environ.get("SCANPASS_API_KEY", "")
CORS_ORIGEM = os.environ.get("SCANPASS_CORS_ORIGEM", "*")
JANELA_SEGUNDOS = int(os.environ.get("SCANPASS_JANELA_SEGUNDOS", "10"))
TIPOS_MOVIMENTO = ("ENTRADA", "SAIDA")

app = Flask(__name__)
app.json.ensure_ascii = False                    # acentos legíveis no JSON
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024     # corpo máximo de 64 KB


@app.after_request
def liberar_cors(resposta):
    # permite o app React Native Web (outra porta) chamar a API
    resposta.headers["Access-Control-Allow-Origin"] = CORS_ORIGEM
    resposta.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    resposta.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return resposta


@app.before_request
def exigir_chave():
    """Exige X-API-Key quando SCANPASS_API_KEY está definida (saúde e preflight ficam livres)."""
    if not API_KEY or request.method == "OPTIONS" or request.path == "/api/saude":
        return None
    enviada = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(enviada.encode("utf-8"), API_KEY.encode("utf-8")):
        return jsonify({"erro": "Chave de API ausente ou inválida"}), 401
    return None


# ======================================================================
# Estado em memória (Morador/Veiculo não têm persistência própria)
# ======================================================================
_trava = threading.RLock()
_moradores: Dict[int, Morador] = {}
_veiculos: Dict[int, Veiculo] = {}
_dono: Dict[int, int] = {}                       # id_veiculo -> id_morador
_proximo = {"morador": 1, "veiculo": 1}
_processador = ProcessadorDados(janela_deduplicacao_segundos=JANELA_SEGUNDOS)
_morador_por_placa: Dict[str, Morador] = {}


def _reconstruir_indices() -> None:
    """Recria o processador (índice hash -> veículo) e o índice placa -> morador.

    Chamar depois de qualquer mudança em veículos: assim um QR Code renovado
    invalida o hash antigo."""
    global _processador, _morador_por_placa
    todos = list(_veiculos.values())
    _processador = ProcessadorDados(janela_deduplicacao_segundos=JANELA_SEGUNDOS, veiculos=todos)
    _morador_por_placa = {v.placa: _moradores[_dono[v.id]] for v in todos}


def _salvar_cadastro() -> None:
    dados = {
        "proximo": _proximo,
        "moradores": [
            {
                "id": m.id,
                "nome": m.nome,
                "apartamento": m.apartamento,
                "bloco": m.bloco,
                "telefone": m.telefone,
                "veiculos": [
                    {
                        "id": v.id,
                        "placa": v.placa,
                        "modelo": v.modelo,
                        "cor": v.cor,
                        "ativo": v.ativo,
                        "qr_code_hash": v.qr_code_hash,
                    }
                    for v in m.veiculos
                ],
            }
            for m in _moradores.values()
        ],
    }
    temporario = ARQUIVO_CADASTRO.with_name(ARQUIVO_CADASTRO.name + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, ARQUIVO_CADASTRO)   # troca atômica: não deixa arquivo pela metade


def _carregar_cadastro() -> None:
    if not ARQUIVO_CADASTRO.exists():
        return
    dados = json.loads(ARQUIVO_CADASTRO.read_text(encoding="utf-8"))
    _proximo.update(dados["proximo"])
    for dm in dados["moradores"]:
        morador = Morador(dm["id"], dm["nome"], dm["apartamento"], dm["bloco"], dm["telefone"])
        for dv in dm["veiculos"]:
            veiculo = Veiculo(dv["id"], dv["placa"], dv["modelo"], dv["cor"], ativo=dv["ativo"])
            veiculo._qr_code_hash = dv["qr_code_hash"]   # restaura o QR Code já emitido
            morador.cadastrar_veiculo(veiculo)
            _veiculos[veiculo.id] = veiculo
            _dono[veiculo.id] = morador.id
        _moradores[morador.id] = morador
    _reconstruir_indices()


# ======================================================================
# Utilitários de entrada e saída
# ======================================================================
def _corpo() -> Dict[str, Any]:
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        raise ValueError("O corpo da requisição deve ser um objeto JSON")
    return corpo


def _texto(corpo: Dict[str, Any], campo: str, maximo: int, obrigatorio: bool = True) -> str:
    valor = corpo.get(campo)
    if isinstance(valor, int) and not isinstance(valor, bool):
        valor = str(valor)                       # ex.: apartamento enviado como número 101
    if valor is None or valor == "":
        if obrigatorio:
            raise ValueError(f"Campo obrigatório: {campo}")
        return ""
    if not isinstance(valor, str):
        raise ValueError(f"O campo {campo} deve ser texto")
    valor = valor.strip()
    if obrigatorio and not valor:
        raise ValueError(f"Campo obrigatório: {campo}")
    if len(valor) > maximo:
        raise ValueError(f"O campo {campo} excede {maximo} caracteres")
    return valor


def _inteiro_url(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = request.args.get(nome)
    if bruto is None:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        raise ValueError(f"O parâmetro {nome} deve ser um número inteiro") from None
    if not minimo <= valor <= maximo:
        raise ValueError(f"O parâmetro {nome} deve estar entre {minimo} e {maximo}")
    return valor


def _limpar_placa(placa: str) -> str:
    return placa.replace("-", "").replace(" ", "").upper()


def _portaria(corpo: Optional[Dict[str, Any]] = None) -> AppPortaria:
    operador = _texto(corpo, "operador", 60, obrigatorio=False) if corpo else ""
    return AppPortaria(0, operador or "Portaria")


def _obter(colecao: Dict[int, Any], identificador: int, nome: str):
    item = colecao.get(identificador)
    if item is None:
        abort(404, description=f"{nome} não encontrado")
    return item


def _veiculo_json(v: Veiculo) -> Dict[str, Any]:
    # o hash do QR Code NUNCA vai em listagens; só pelos endpoints /qrcode
    return {"id": v.id, "placa": v.placa, "modelo": v.modelo, "cor": v.cor,
            "ativo": v.ativo, "tem_qr_code": v.qr_code_hash is not None}


def _morador_json(m: Morador, com_veiculos: bool = True) -> Dict[str, Any]:
    dados = {"id": m.id, "nome": m.nome, "apartamento": m.apartamento,
             "bloco": m.bloco, "telefone": m.telefone}
    if com_veiculos:
        dados["veiculos"] = [_veiculo_json(v) for v in m.veiculos]
    return dados


def _resumo_morador(m: Optional[Morador]) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    return {"id": m.id, "nome": m.nome, "bloco": m.bloco, "apartamento": m.apartamento}


def _registro_json(r: RegistroAcesso) -> Dict[str, Any]:
    dados = r.para_dict()
    dados["data_hora"] = r.data_hora.isoformat()
    dados["morador"] = _resumo_morador(_morador_por_placa.get(r.placa))
    return dados


# ======================================================================
# Tratamento de erros (sempre JSON)
# ======================================================================
@app.errorhandler(HTTPException)
def erro_http(erro: HTTPException):
    return jsonify({"erro": erro.description}), erro.code


@app.errorhandler(ValueError)
def erro_validacao(erro: ValueError):
    return jsonify({"erro": str(erro)}), 400


@app.errorhandler(RuntimeError)
def erro_gravacao(erro: RuntimeError):
    app.logger.error("Falha de gravação: %s", erro)
    return jsonify({"erro": "Não foi possível gravar o registro. "
                            "Verifique se a planilha não está aberta no Excel."}), 503


@app.errorhandler(Exception)
def erro_interno(erro: Exception):
    app.logger.exception("Erro inesperado")
    return jsonify({"erro": "Erro interno do servidor"}), 500


# ======================================================================
# Saúde
# ======================================================================
@app.get("/api/saude")
def saude():
    return jsonify({"status": "ok", "hora": datetime.now().isoformat()})


# ======================================================================
# Moradores
# ======================================================================
@app.get("/api/moradores")
def listar_moradores():
    busca = request.args.get("q", "").strip().lower()
    bloco = request.args.get("bloco", "").strip().lower()
    apto = request.args.get("apartamento", "").strip().lower()
    with _trava:
        itens = [
            m for m in _moradores.values()
            if (not busca or busca in m.nome.lower())
            and (not bloco or m.bloco.lower() == bloco)
            and (not apto or m.apartamento.lower() == apto)
        ]
        itens.sort(key=lambda m: (m.bloco, m.apartamento, m.nome))
        return jsonify([_morador_json(m) for m in itens])


@app.post("/api/moradores")
def criar_morador():
    corpo = _corpo()
    nome = _texto(corpo, "nome", 150)
    apartamento = _texto(corpo, "apartamento", 10)
    bloco = _texto(corpo, "bloco", 50)
    telefone = _texto(corpo, "telefone", 20, obrigatorio=False)
    with _trava:
        morador = Morador(_proximo["morador"], nome, apartamento, bloco, telefone)
        _proximo["morador"] += 1
        _moradores[morador.id] = morador
        _salvar_cadastro()
        return jsonify(_morador_json(morador)), 201


@app.get("/api/moradores/<int:id_morador>")
def obter_morador(id_morador: int):
    with _trava:
        return jsonify(_morador_json(_obter(_moradores, id_morador, "Morador")))


@app.get("/api/moradores/<int:id_morador>/historico")
def historico_morador(id_morador: int):
    limite = _inteiro_url("limite", 50, 1, 500)
    with _trava:
        morador = _obter(_moradores, id_morador, "Morador")
        historico = morador.consultar_historico()[:limite]
        return jsonify([_registro_json(r) for r in historico])


# ======================================================================
# Veículos e QR Codes
# ======================================================================
@app.post("/api/moradores/<int:id_morador>/veiculos")
def cadastrar_veiculo(id_morador: int):
    corpo = _corpo()
    placa = _texto(corpo, "placa", 10)
    modelo = _texto(corpo, "modelo", 50)
    cor = _texto(corpo, "cor", 30)
    with _trava:
        morador = _obter(_moradores, id_morador, "Morador")
        veiculo = Veiculo(_proximo["veiculo"], placa, modelo, cor)   # valida a placa
        if veiculo.placa in _morador_por_placa:
            abort(409, description="Já existe um veículo cadastrado com essa placa")
        _proximo["veiculo"] += 1
        qr_code = veiculo.gerar_qr_code()
        morador.cadastrar_veiculo(veiculo)
        _veiculos[veiculo.id] = veiculo
        _dono[veiculo.id] = morador.id
        _reconstruir_indices()
        _salvar_cadastro()
        return jsonify({**_veiculo_json(veiculo), "qr_code": qr_code}), 201


@app.get("/api/veiculos")
def listar_veiculos():
    placa = _limpar_placa(request.args.get("placa", ""))
    with _trava:
        itens = [v for v in _veiculos.values() if not placa or placa in v.placa]
        itens.sort(key=lambda v: v.placa)
        return jsonify([{**_veiculo_json(v), "morador": _resumo_morador(_moradores[_dono[v.id]])}
                        for v in itens])


@app.patch("/api/veiculos/<int:id_veiculo>")
def atualizar_veiculo(id_veiculo: int):
    corpo = _corpo()
    if not isinstance(corpo.get("ativo"), bool):
        raise ValueError("O campo ativo deve ser true ou false")
    with _trava:
        veiculo = _obter(_veiculos, id_veiculo, "Veículo")
        veiculo.ativo = corpo["ativo"]
        _salvar_cadastro()
        return jsonify(_veiculo_json(veiculo))


@app.get("/api/veiculos/<int:id_veiculo>/qrcode")
def obter_qrcode(id_veiculo: int):
    with _trava:
        veiculo = _obter(_veiculos, id_veiculo, "Veículo")
        return jsonify({"id": veiculo.id, "placa": veiculo.placa, "qr_code": veiculo.qr_code_hash})


@app.post("/api/veiculos/<int:id_veiculo>/qrcode")
def renovar_qrcode(id_veiculo: int):
    with _trava:
        veiculo = _obter(_veiculos, id_veiculo, "Veículo")
        qr_code = veiculo.gerar_qr_code()
        _reconstruir_indices()                   # o hash antigo deixa de valer
        _salvar_cadastro()
        return jsonify({"id": veiculo.id, "placa": veiculo.placa, "qr_code": qr_code})


# ======================================================================
# Acessos
# ======================================================================
def _leitura_repetida(placa: str, tipo: str) -> bool:
    """True se a mesma placa já teve este movimento dentro da janela de deduplicação."""
    agora = datetime.now()
    janela = _processador.janela_deduplicacao_segundos
    for registro in RegistroAcesso.buscar(placas=[placa], limite=10):
        if (agora - registro.data_hora).total_seconds() >= janela:
            break                                # os mais antigos já estão fora da janela
        if registro.tipo_movimento == tipo:
            return True
    return False


@app.post("/api/acessos/validar")
def validar_acesso():
    """Recebe o texto lido do QR Code, valida e registra a entrada/saída.

    Sempre responde 200: o resultado do negócio está em `status` e `autorizado`.
    `duplicado: true` indica leitura repetida dentro da janela (não grava de novo)."""
    corpo = _corpo()
    hash_lido = _texto(corpo, "qr_code", 200)
    tipo = _texto(corpo, "tipo", 10, obrigatorio=False).upper() or "ENTRADA"
    if tipo not in TIPOS_MOVIMENTO:
        raise ValueError(f"O campo tipo deve ser um de {list(TIPOS_MOVIMENTO)}")
    portaria = _portaria(corpo)

    with _trava:
        status = _processador.validar_acesso(hash_lido)
        veiculo = _processador.buscar_veiculo(hash_lido)
        mensagem = portaria.exibir_alerta_visual(status)   # também aparece no console da portaria
        registro, duplicado = None, False
        if veiculo is not None:
            if _leitura_repetida(veiculo.placa, tipo):
                duplicado = True
            else:
                registro = _processador.registrar_log(veiculo, tipo)
        dono = _morador_por_placa.get(veiculo.placa) if veiculo else None
        return jsonify({
            "status": status.value,
            "autorizado": status.name == "AUTORIZADO",
            "mensagem": mensagem,
            "duplicado": duplicado,
            "tipo": tipo,
            "veiculo": _veiculo_json(veiculo) if veiculo else None,
            "morador": _resumo_morador(dono),
            "registro": _registro_json(registro) if registro else None,
        })


@app.post("/api/acessos/visitante")
def registrar_visitante():
    corpo = _corpo()
    placa = _texto(corpo, "placa", 10)
    motivo = _texto(corpo, "motivo", 200)
    portaria = _portaria(corpo)
    registro = portaria.registrar_visitante_manual(placa, motivo)   # valida a placa
    return jsonify(_registro_json(registro)), 201


@app.get("/api/acessos")
def listar_acessos():
    limite = _inteiro_url("limite", 10, 1, 200)
    placa = _limpar_placa(request.args.get("placa", ""))
    with _trava:
        if placa:
            registros = RegistroAcesso.buscar(placas=[placa], limite=limite)
        else:
            registros = _portaria().consultar_ultimos_acessos(limite)
        return jsonify([_registro_json(r) for r in registros])


# ======================================================================
# Dashboard
# ======================================================================
_VISOES = {
    "categorica": "gerar_visao_categorica",
    "numerica": "gerar_visao_numerica",
    "temporal": "gerar_visao_temporal",
}


def _dados_dashboard():
    """DataFrame dos últimos registros, sem leituras repetidas."""
    limite = _inteiro_url("limite", 5000, 1, 100000)
    with _trava:
        return _processador.filtrar_duplicidades(RegistroAcesso.buscar(limite=limite)), limite


@app.get("/api/dashboard/resumo")
def dashboard_resumo():
    import pandas as pd

    df, limite = _dados_dashboard()
    resumo: Dict[str, Any] = {"total": int(len(df)), "limite_consultado": limite,
                              "por_status": {}, "por_tipo": {}, "por_hora": {}, "por_dia": {}}
    if df.empty:
        return jsonify(resumo)

    def contar(serie) -> Dict[str, int]:
        return {str(chave): int(qtd) for chave, qtd in serie.value_counts().sort_index().items()}

    datas = pd.to_datetime(df["data_hora"])
    resumo["por_status"] = contar(df["status_autorizacao"])
    resumo["por_tipo"] = contar(df["tipo_movimento"])
    resumo["por_hora"] = contar(datas.dt.hour)
    resumo["por_dia"] = contar(datas.dt.strftime("%Y-%m-%d"))
    return jsonify(resumo)


@app.get("/api/dashboard/figura/<visao>")
def dashboard_figura(visao: str):
    if visao not in _VISOES:
        abort(404, description=f"Visão inexistente. Use: {', '.join(_VISOES)}")
    df, _ = _dados_dashboard()
    try:
        figura = getattr(DashboardAnalytics(df), _VISOES[visao])()
    except ImportError:
        return jsonify({"erro": "plotly não instalado no servidor (pip install plotly)"}), 501
    return Response(figura.to_json(), mimetype="application/json")


@app.get("/api/dashboard/relatorio")
def dashboard_relatorio():
    df, _ = _dados_dashboard()
    descritor, caminho = tempfile.mkstemp(suffix=".html")
    os.close(descritor)
    try:
        DashboardAnalytics(df).exportar_relatorio(caminho)
        html = Path(caminho).read_bytes()
    except ImportError:
        return jsonify({"erro": "plotly não instalado no servidor (pip install plotly)"}), 501
    finally:
        Path(caminho).unlink(missing_ok=True)
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": 'attachment; filename="relatorio_acessos.html"'})


# ======================================================================
# Inicialização
# ======================================================================
def iniciar() -> None:
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    RegistroAcesso.configurar_planilha(PASTA_DADOS / "registros_acesso", lote=1)
    _carregar_cadastro()
    if not API_KEY:
        app.logger.warning("SCANPASS_API_KEY não definida: a API está ABERTA para quem alcançar a porta.")


iniciar()

if __name__ == "__main__":
    # sem debug=True: o depurador do Werkzeug permite executar código no servidor
    app.run(host=os.environ.get("SCANPASS_HOST", "127.0.0.1"),
            port=int(os.environ.get("SCANPASS_PORT", "5000")),
            threaded=True)
