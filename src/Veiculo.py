from Utilitarios import Utilitarios
from typing import Optional
import secrets, hashlib

class Veiculo:
    def __init__(
        self,
        id_veiculo: int,
        placa: str,
        modelo: str,
        cor: str,
        ativo: bool = True,
    ) -> None:
        self._id = id_veiculo
        self._placa = Utilitarios._normalizar_placa(placa)
        self._modelo = modelo
        self._cor = cor
        self._qr_code_hash: Optional[str] = None
        self._ativo = ativo

    id = Utilitarios._somente_leitura("_id")
    placa = Utilitarios._somente_leitura("_placa")
    modelo = Utilitarios._somente_leitura("_modelo")
    cor = Utilitarios._somente_leitura("_cor")
    qr_code_hash = Utilitarios._somente_leitura("_qr_code_hash")

    @property
    def ativo(self) -> bool:
        return self._ativo

    @ativo.setter
    def ativo(self, valor: bool) -> None:
        self._ativo = valor

    def gerar_qr_code(self) -> str:
        
        sal = secrets.token_hex(8)
        conteudo = f"{self._id}:{self._placa}:{sal}"
        self._qr_code_hash = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()
        return self._qr_code_hash

    def validar_status(self) -> bool:
        """O veículo só é válido se estiver ativo e já possuir QR Code."""
        return self._ativo and self._qr_code_hash is not None

    def __repr__(self) -> str:
        return f"Veiculo(id={self._id}, placa={self._placa!r}, ativo={self._ativo})"
