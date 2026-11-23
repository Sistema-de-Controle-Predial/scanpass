from Utilitarios import Utilitarios
from typing import List
from Veiculo import *
from RegistroAcesso import *

class Morador:
    def __init__(
        self,
        id_morador: int,
        nome: str,
        apartamento: str,
        bloco: str,
        telefone: str,
    ) -> None:
        self._id = id_morador
        self._nome = nome
        self._apartamento = apartamento
        self._bloco = bloco
        self._telefone = telefone
        self._veiculos: List[Veiculo] = []

    id = Utilitarios._somente_leitura("_id")
    nome = Utilitarios._somente_leitura("_nome")
    apartamento = Utilitarios._somente_leitura("_apartamento")
    bloco = Utilitarios._somente_leitura("_bloco")
    telefone = Utilitarios._somente_leitura("_telefone")

    @property
    def veiculos(self) -> tuple:
        """Veículos do morador (cópia imutável)."""
        return tuple(self._veiculos)

    def cadastrar_veiculo(self, v: Veiculo) -> None:
        if any(existente.placa == v.placa for existente in self._veiculos):
            raise ValueError(f"Veículo de placa {v.placa} já cadastrado para este morador")
        self._veiculos.append(v)

    def consultar_historico(self) -> List[RegistroAcesso]:
        """Registros de acesso de todos os veículos do morador (mais recentes primeiro)."""
        placas = [v.placa for v in self._veiculos]
        return RegistroAcesso.buscar(placas=placas) if placas else []

    def __repr__(self) -> str:
        return f"Morador(id={self._id}, nome={self._nome!r}, bloco={self._bloco}, ap={self._apartamento})"
