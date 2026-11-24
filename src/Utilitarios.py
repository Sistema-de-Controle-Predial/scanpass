from __future__ import annotations

import re
from enum import Enum


_PADRAO_PLACA = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")


# ======================================================================
# Utilitários
# ======================================================================
class Utilitarios():
    def _somente_leitura(nome_atributo: str) -> property:
        """Cria uma propriedade somente leitura para um atributo privado."""
        return property(lambda self: getattr(self, nome_atributo))




    def _normalizar_placa(placa: str) -> str:
        """Remove hífen/espaços, coloca em maiúsculas e valida (antiga ou Mercosul)."""
        limpa = placa.replace("-", "").replace(" ", "").upper()
        if not _PADRAO_PLACA.match(limpa):
            raise ValueError(f"Placa inválida: {placa!r}")
        return limpa

class StatusAcesso(Enum):
    """Resultado da validação de um acesso (usado por ProcessadorDados e AppPortaria)."""

    AUTORIZADO = "AUTORIZADO"
    VEICULO_INATIVO = "VEICULO_INATIVO"
    NAO_CADASTRADO = "NAO_CADASTRADO"
    VISITANTE_MANUAL = "VISITANTE_MANUAL"

