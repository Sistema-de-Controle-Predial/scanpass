"""
BancoDados.py - acesso ao PostgreSQL do ScanPass.

Instalação:
    pip install "psycopg[binary]" psycopg-pool

Uso:
    banco = BancoDados("postgresql://usuario:senha@localhost:5432/condominio")
    # ou defina DATABASE_URL no ambiente e chame BancoDados()

    morador = banco.inserir_morador("Maria", bloco="A", apartamento="101", telefone="21999990000")
    veiculo = banco.inserir_veiculo(morador.id, "ABC1D23", modelo="Onix", cor="Prata")
    veiculo.gerar_qr_code()
    banco.salvar_qr_code(veiculo)

    # Faz o RegistroAcesso gravar/ler no PostgreSQL em vez das planilhas:
    RegistroAcesso.configurar_banco(banco)

Todas as operações usam uma conexão do pool e rodam em uma transação: confirmam
(commit) ao terminar e desfazem (rollback) se der erro. Para agrupar várias
operações em uma única transação, use `with banco.transacao() as conn:` e passe
`conn=conn` aos métodos genéricos (consultar / consultar_um / executar).
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional
from dotenv import load_dotenv
load_dotenv()

from psycopg import Error, connect
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from Morador import Morador
from RegistroAcesso import RegistroAcesso
from Utilitarios import Utilitarios
from Veiculo import Veiculo



class BancoDados:
    TIPOS_VEICULO = ("carro", "moto", "outro")

    _SQL_MORADORES = """
        SELECT m.id_morador, m.nome, m.telefone,
               b.nome AS bloco, a.numero AS apartamento,
               v.id_veiculo, v.placa, v.modelo, v.cor,
               v.ativo AS veiculo_ativo, v.qr_code_hash
        FROM morador m
        JOIN apartamento a  ON a.id_apartamento = m.id_apartamento
        JOIN bloco b        ON b.id_bloco       = a.id_bloco
        LEFT JOIN veiculo v ON v.id_morador     = m.id_morador
        {where}
        ORDER BY b.nome, a.numero, m.nome, m.id_morador, v.id_veiculo
    """

    _SQL_VEICULOS = """
        SELECT id_veiculo, placa, modelo, cor, ativo, qr_code_hash
        FROM veiculo
        {where}
        ORDER BY id_veiculo
    """

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    def __init__(
        self,
        dsn: Optional[str] = None,
        min_conexoes: int = 1,
        max_conexoes: int = 10,
        timeout_conexao: float = 30.0,
        timeout_tentativa: int = 5,
    ) -> None:
        dsn = dsn or os.environ.get("DATABASE_URL")
        if not dsn:
            raise ValueError("Informe o dsn ou defina a variável de ambiente DATABASE_URL")

        # Testa uma conexão direta ANTES de criar o pool. O pool sozinho só registra os erros
        # de conexão no log e fica tentando; no fim levanta "PoolTimeout" sem dizer o motivo.
        try:
            with connect(dsn, connect_timeout=timeout_tentativa):
                pass
        except Error as erro:
            raise ConnectionError(
                f"Não foi possível conectar ao PostgreSQL em {self._dsn_sem_senha(dsn)}\n"
                f"Motivo: {str(erro).strip()}"
            ) from erro
        
        self._pool = ConnectionPool(
            dsn,
            min_size=min_conexoes,
            max_size=max_conexoes,
            kwargs={"row_factory": dict_row},
            open=True,
        )

        try:
            self._pool.wait(timeout=timeout_conexao)  # falha logo no início se não conectar
        except Exception:
            self._pool.close()

    @staticmethod
    def _dsn_sem_senha(dsn: str) -> str:
        """Troca a senha por *** (formatos postgresql://u:senha@host/db e password=senha)."""
        dsn = re.sub(r"(://[^:/@\s]+:)[^/\s]*@", r"\1***@", dsn)
        return re.sub(r"(password\s*=\s*)('[^']*'|\S+)", r"\1***", dsn, flags=re.IGNORECASE)

    def fechar(self) -> None:
        self._pool.close()

    def __enter__(self) -> "BancoDados":
        return self

    def __exit__(self, *_: Any) -> None:
        self.fechar()

    @contextmanager
    def transacao(self) -> Iterator[Any]:
        """Entrega uma conexão em transação: commit ao sair, rollback se houver exceção."""
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def _cursor(self, conn: Any = None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
        else:
            with self.transacao() as nova, nova.cursor() as cur:
                yield cur

    # ------------------------------------------------------------------
    # SQL genérico (sempre com parâmetros %s, nunca concatenando valores)
    # ------------------------------------------------------------------
    def consultar(self, sql: str, params: Any = None, conn: Any = None) -> List[Dict[str, Any]]:
        with self._cursor(conn) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def consultar_um(self, sql: str, params: Any = None, conn: Any = None) -> Optional[Dict[str, Any]]:
        with self._cursor(conn) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def executar(self, sql: str, params: Any = None, conn: Any = None) -> int:
        """Executa INSERT/UPDATE/DELETE e devolve o número de linhas afetadas."""
        with self._cursor(conn) as cur:
            cur.execute(sql, params)
            return cur.rowcount

    # ------------------------------------------------------------------
    # Conversão linha -> objeto
    # ------------------------------------------------------------------
    @staticmethod
    def _criar_veiculo(
        id_veiculo: int,
        placa: str,
        modelo: Optional[str],
        cor: Optional[str],
        ativo: bool,
        qr_code_hash: Optional[str],
    ) -> Veiculo:
        veiculo = Veiculo(id_veiculo, placa, modelo or "", cor or "", ativo=ativo)
        # Veiculo só expõe o hash por leitura; aqui restauramos o valor guardado no banco.
        veiculo._qr_code_hash = qr_code_hash.strip() if qr_code_hash else None
        return veiculo

    @classmethod
    def _veiculo_de_linha(cls, r: Dict[str, Any]) -> Veiculo:
        return cls._criar_veiculo(
            r["id_veiculo"], r["placa"], r["modelo"], r["cor"], r["ativo"], r["qr_code_hash"]
        )

    @classmethod
    def _montar_moradores(cls, linhas: Iterable[Dict[str, Any]]) -> List[Morador]:
        moradores: Dict[int, Morador] = {}
        for r in linhas:
            morador = moradores.get(r["id_morador"])
            if morador is None:
                morador = Morador(
                    r["id_morador"], r["nome"], r["apartamento"], r["bloco"], r["telefone"] or ""
                )
                moradores[r["id_morador"]] = morador
            if r["id_veiculo"] is not None:
                morador.cadastrar_veiculo(
                    cls._criar_veiculo(
                        r["id_veiculo"], r["placa"], r["modelo"], r["cor"],
                        r["veiculo_ativo"], r["qr_code_hash"],
                    )
                )
        return list(moradores.values())

    # ------------------------------------------------------------------
    # Moradores
    # ------------------------------------------------------------------
    def inserir_morador(
        self,
        nome: str,
        bloco: str,
        apartamento: str,
        telefone: str = "",
        cpf: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Morador:
        """Cria o morador (e o bloco/apartamento, se ainda não existirem)."""
        if cpf:
            cpf = re.sub(r"\D", "", cpf)
            if len(cpf) != 11:
                raise ValueError("CPF deve ter 11 dígitos")
        try:
            with self._cursor() as cur:  # uma única transação para os três INSERTs
                cur.execute(
                    "INSERT INTO bloco (nome) VALUES (%s) "
                    "ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id_bloco",
                    (bloco,),
                )
                id_bloco = cur.fetchone()["id_bloco"]
                cur.execute(
                    "INSERT INTO apartamento (id_bloco, numero) VALUES (%s, %s) "
                    "ON CONFLICT (id_bloco, numero) DO UPDATE SET numero = EXCLUDED.numero "
                    "RETURNING id_apartamento",
                    (id_bloco, apartamento),
                )
                id_apartamento = cur.fetchone()["id_apartamento"]
                cur.execute(
                    "INSERT INTO morador (id_apartamento, nome, cpf, telefone, email) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id_morador",
                    (id_apartamento, nome, cpf or None, telefone or None, email or None),
                )
                id_morador = cur.fetchone()["id_morador"]
        except errors.UniqueViolation as erro:
            raise ValueError("Já existe um morador com este CPF") from erro
        return Morador(id_morador, nome, apartamento, bloco, telefone)

    def buscar_morador(self, id_morador: int) -> Optional[Morador]:
        linhas = self.consultar(
            self._SQL_MORADORES.format(where="WHERE m.id_morador = %s"), (id_morador,)
        )
        moradores = self._montar_moradores(linhas)
        return moradores[0] if moradores else None

    def buscar_morador_por_placa(self, placa: str) -> Optional[Morador]:
        placa = Utilitarios._normalizar_placa(placa)
        linhas = self.consultar(
            self._SQL_MORADORES.format(
                where="WHERE m.id_morador = (SELECT id_morador FROM veiculo WHERE placa = %s)"
            ),
            (placa,),
        )
        moradores = self._montar_moradores(linhas)
        return moradores[0] if moradores else None

    def listar_moradores(self, apenas_ativos: bool = True) -> List[Morador]:
        where = "WHERE m.ativo" if apenas_ativos else ""
        return self._montar_moradores(self.consultar(self._SQL_MORADORES.format(where=where)))

    def desativar_morador(self, id_morador: int) -> bool:
        """Marca o morador como inativo (mantém o histórico). False se não existir."""
        return self.executar("UPDATE morador SET ativo = FALSE WHERE id_morador = %s", (id_morador,)) > 0

    # ------------------------------------------------------------------
    # Veículos
    # ------------------------------------------------------------------
    def inserir_veiculo(
        self,
        id_morador: int,
        placa: str,
        modelo: str = "",
        cor: str = "",
        tipo: str = "carro",
        marca: Optional[str] = None,
        ativo: bool = True,
    ) -> Veiculo:
        placa = Utilitarios._normalizar_placa(placa)
        if tipo not in self.TIPOS_VEICULO:
            raise ValueError(f"Tipo de veículo deve ser um de {self.TIPOS_VEICULO}")
        try:
            linha = self.consultar_um(
                "INSERT INTO veiculo (id_morador, placa, tipo, marca, modelo, cor, ativo) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_veiculo",
                (id_morador, placa, tipo, marca or None, modelo or None, cor or None, ativo),
            )
        except errors.UniqueViolation as erro:
            raise ValueError(f"Veículo de placa {placa} já cadastrado") from erro
        except errors.ForeignKeyViolation as erro:
            raise ValueError(f"Morador {id_morador} não encontrado") from erro
        return self._criar_veiculo(linha["id_veiculo"], placa, modelo, cor, ativo, None)

    def salvar_qr_code(self, veiculo: Veiculo) -> None:
        """Grava no banco o hash gerado por Veiculo.gerar_qr_code()."""
        if veiculo.qr_code_hash is None:
            raise ValueError("Gere o QR Code (Veiculo.gerar_qr_code) antes de salvar")
        alterados = self.executar(
            "UPDATE veiculo SET qr_code_hash = %s WHERE id_veiculo = %s",
            (veiculo.qr_code_hash, veiculo.id),
        )
        if alterados == 0:
            raise ValueError(f"Veículo {veiculo.id} não encontrado")

    def definir_veiculo_ativo(self, id_veiculo: int, ativo: bool) -> bool:
        return self.executar(
            "UPDATE veiculo SET ativo = %s WHERE id_veiculo = %s", (ativo, id_veiculo)
        ) > 0

    def remover_veiculo(self, id_veiculo: int) -> bool:
        return self.executar("DELETE FROM veiculo WHERE id_veiculo = %s", (id_veiculo,)) > 0

    def buscar_veiculo_por_hash(self, hash_qr: str) -> Optional[Veiculo]:
        linha = self.consultar_um(
            self._SQL_VEICULOS.format(where="WHERE qr_code_hash = %s"), (hash_qr,)
        )
        return self._veiculo_de_linha(linha) if linha else None

    def buscar_veiculo_por_placa(self, placa: str) -> Optional[Veiculo]:
        linha = self.consultar_um(
            self._SQL_VEICULOS.format(where="WHERE placa = %s"),
            (Utilitarios._normalizar_placa(placa),),
        )
        return self._veiculo_de_linha(linha) if linha else None

    def listar_veiculos(self, apenas_ativos: bool = False) -> List[Veiculo]:
        """Útil para alimentar ProcessadorDados.carregar_veiculos()."""
        where = "WHERE ativo" if apenas_ativos else ""
        return [self._veiculo_de_linha(r) for r in self.consultar(self._SQL_VEICULOS.format(where=where))]

    # ------------------------------------------------------------------
    # Registros de acesso
    # ------------------------------------------------------------------
    def salvar_registro(self, registro: RegistroAcesso) -> int:
        """Insere o registro e devolve o id gerado pelo banco."""
        linha = self.consultar_um(
            "INSERT INTO registro_acesso "
            "(data_hora, tipo_movimento, status_autorizacao, placa, observacao) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id_registro",
            (
                registro.data_hora.astimezone(),  # datetime ingênuo = horário local da máquina
                registro.tipo_movimento,
                registro.status_autorizacao,
                registro.placa,
                registro.observacao or "",
            ),
        )
        return linha["id_registro"]

    def buscar_registros(
        self,
        placas: Optional[Iterable[str]] = None,
        limite: Optional[int] = None,
    ) -> List[RegistroAcesso]:
        """Registros mais recentes primeiro, opcionalmente filtrados por placas."""
        condicoes: List[str] = []
        params: List[Any] = []
        if placas is not None:
            lista = list(placas)
            if not lista:
                return []
            condicoes.append("placa = ANY(%s)")
            params.append(lista)
        sql = (
            "SELECT id_registro, data_hora, tipo_movimento, status_autorizacao, placa, observacao "
            "FROM registro_acesso"
        )
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY data_hora DESC, id_registro DESC"
        if limite is not None:
            sql += " LIMIT %s"
            params.append(limite)
        return [
            RegistroAcesso(
                r["tipo_movimento"],
                r["status_autorizacao"],
                placa=r["placa"],
                observacao=r["observacao"] or "",
                # volta ao horário local sem fuso, como o restante do código (datetime.now())
                data_hora=r["data_hora"].astimezone().replace(tzinfo=None),
                id_registro=r["id_registro"],
            )
            for r in self.consultar(sql, params or None)
        ]

MORADORES_EXEMPLO = [
    {
        "nome": "Maria Souza",
        "bloco": "A",
        "apartamento": "101",
        "telefone": "21999990001",
        "veiculo": {"placa": "ABC1D23", "modelo": "Onix", "cor": "Prata", "tipo": "carro"},
    },
    {
        "nome": "João Lima",
        "bloco": "A",
        "apartamento": "204",
        "telefone": "21999990002",
        "veiculo": {"placa": "XYZ9K87", "modelo": "HB20", "cor": "Branco", "tipo": "carro"},
    },
    {
        "nome": "Ana Costa",
        "bloco": "B",
        "apartamento": "102",
        "telefone": "21999990003",
        "veiculo": {"placa": "QWE4R56", "modelo": "CG 160", "cor": "Preta", "tipo": "moto"},
    },
    {
        "nome": "Carlos Reis",
        "bloco": "B",
        "apartamento": "305",
        "telefone": "21999990004",
        "veiculo": {"placa": "JKL2M34", "modelo": "Corolla", "cor": "Cinza", "tipo": "carro"},
    },
]


def main() -> None:
    with BancoDados() as banco:
        for item in MORADORES_EXEMPLO:
            morador = banco.inserir_morador(
                nome=item["nome"],
                bloco=item["bloco"],
                apartamento=item["apartamento"],
                telefone=item["telefone"],
            )
            v_info = item["veiculo"]
            veiculo = banco.inserir_veiculo(
                id_morador=morador.id,
                placa=v_info["placa"],
                modelo=v_info["modelo"],
                cor=v_info["cor"],
                tipo=v_info["tipo"],
            )
            qr_hash = veiculo.gerar_qr_code()
            banco.salvar_qr_code(veiculo)
            print(f"{morador.nome} ({morador.bloco}-{morador.apartamento}) | Placa: {veiculo.placa} | QR: {qr_hash}")


if __name__ == "__main__":
    main()
