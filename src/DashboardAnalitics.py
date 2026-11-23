from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    import pandas as pd
    from plotly.graph_objects import Figure


class DashboardAnalytics:
    """Gera gráficos Plotly a partir de um DataFrame de registros de acesso
    (por exemplo, o retorno de ProcessadorDados.filtrar_duplicidades)."""

    def __init__(self, dados: "pd.DataFrame") -> None:
        self._dados = dados.copy()

    def gerar_visao_categorica(self) -> "Figure":
        """Quantidade de acessos por status de autorização."""
        import plotly.express as px

        contagem = self._dados.groupby("status_autorizacao").size().reset_index(name="quantidade")
        return px.bar(
            contagem,
            x="status_autorizacao",
            y="quantidade",
            title="Acessos por status de autorização",
            labels={"status_autorizacao": "Status", "quantidade": "Quantidade"},
        )

    def gerar_visao_numerica(self) -> "Figure":
        """Distribuição dos acessos por hora do dia (0 a 23)."""
        import pandas as pd
        import plotly.express as px

        horas = pd.to_datetime(self._dados["data_hora"]).dt.hour
        return px.histogram(
            x=horas,
            nbins=24,
            title="Distribuição de acessos por hora do dia",
            labels={"x": "Hora do dia", "count": "Quantidade"},
        )

    def gerar_visao_temporal(self) -> "Figure":
        """Evolução do número de acessos ao longo do tempo (agrupado por hora)."""
        import pandas as pd
        import plotly.express as px

        datas = pd.to_datetime(self._dados["data_hora"])
        serie = (
            self._dados.assign(hora=datas.dt.floor("h"))
            .groupby("hora")
            .size()
            .reset_index(name="quantidade")
        )
        return px.line(
            serie,
            x="hora",
            y="quantidade",
            markers=True,
            title="Acessos ao longo do tempo",
            labels={"hora": "Data/hora", "quantidade": "Quantidade"},
        )

    def exportar_relatorio(self, caminho: str = "relatorio_acessos.html") -> str:
        """Salva as três visões em um único arquivo HTML e devolve o caminho."""
        figuras = [
            self.gerar_visao_categorica(),
            self.gerar_visao_numerica(),
            self.gerar_visao_temporal(),
        ]
        partes = [
            fig.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False)
            for i, fig in enumerate(figuras)
        ]
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Relatório ScanPass</title></head><body>"
            "<h1>Relatório de acessos - ScanPass</h1>" + "".join(partes) + "</body></html>"
        )
        Path(caminho).write_text(html, encoding="utf-8")
        return caminho
