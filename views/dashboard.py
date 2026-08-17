"""views/dashboard.py — Tablero de control interactivo SIDOE ONE."""

import pandas as pd
import plotly.express as px
import streamlit as st

import config
from config import CAT_I, CAT_II, CAT_III
from data import database as db_mod

_COLORES = {CAT_I: "#002F6C", CAT_II: "#FFC72C", CAT_III: "#D9534F"}

# Etiquetas visibles → nombre real de columna para cruces dinámicos
_DIMENSIONES = {
    "Instrumento de Demanda": "generador_demanda",
    "Nivel de Factibilidad": "categoria_factibilidad",
    "Sector IOE": "sector_ioe",
    "Dominio de Actividad Estadística": "dominio_actividad_estadistica",
    "Subdominio de Actividad Estadística": "subdominio_actividad_estadistica",
    "Disponibilidad de Fuente": "disponibilidad_fuente",
    "Eje": "eje",
    "Alcance Metodológico": "alcance_metodologico",
    "Periodicidad del Indicador": "periodicidad_indicador",
}


def _grafico_descargable(fig, nombre_archivo: str, alto: int | None = None) -> None:
    """Muestra un gráfico Plotly con botón PNG en la barra de herramientas."""
    if alto:
        fig.update_layout(height=alto)
    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "displaylogo": False,
            "toImageButtonOptions": {
                "format": "png",
                "filename": nombre_archivo,
                "scale": 2,
            },
        },
    )


# TTL corto (no 300s como en crud_auxiliares.py, que son catálogos de baja
# frecuencia de cambio): el dashboard refleja ediciones de indicadores que
# ocurren con regularidad, así que 60s balancea rendimiento (cada cambio de
# filtro/dimensión dispara un rerun completo del script) contra frescura.
_CACHE_TTL_DASHBOARD = 60


@st.cache_data(ttl=_CACHE_TTL_DASHBOARD)
def _cargar_datos(es_publico: bool, db_path: str) -> pd.DataFrame:
    """Carga indicadores activos con su factibilidad y disponibilidad de fuente.

    Si es_publico es True (modo público, sin sesión), se filtra además por
    estado_publicacion = 'publicado' — no afecta la visibilidad interna para
    editor/administrador, que solo depende de estado_indicador = 'Activo'.

    ``db_path`` solo forma parte de la clave de caché de Streamlit (proceso,
    no sesión); ver la nota equivalente en views/consultas.py.
    """
    conn = db_mod.obtener_conexion()
    filtro_publicacion = " AND i.estado_publicacion = 'publicado'" if es_publico else ""
    df = pd.read_sql_query(
        f"""
        SELECT i.id AS indicador_id, i.codigo, i.generador_demanda, i.eje,
               i.area_misional_one, i.periodicidad_indicador, i.alcance_metodologico,
               i.dominio_actividad_estadistica, i.subdominio_actividad_estadistica,
               i.sector_ioe,
               c.score_factibilidad_final, c.categoria_factibilidad
        FROM indicadores_resuelto i
        LEFT JOIN calculo_factibilidad c ON c.indicador_id = i.id
        WHERE i.estado_indicador = 'Activo'{filtro_publicacion}
        """,
        conn,
    )
    df_fuentes = pd.read_sql_query(
        """
        SELECT indicador_id, existencia_fuente
        FROM fuentes_resuelto
        WHERE existencia_fuente IS NOT NULL
        """,
        conn,
    )
    conn.close()

    # Disponibilidad de fuente agregada a nivel de indicador (prioridad: Completamente > Parcialmente > No hay fuente)
    prioridad = {"Completamente": 3, "Parcialmente": 2, "No hay fuente": 1}
    if not df_fuentes.empty:
        df_fuentes["_rank"] = df_fuentes["existencia_fuente"].map(prioridad).fillna(0)
        mejor_fuente = (
            df_fuentes.sort_values("_rank", ascending=False)
            .drop_duplicates("indicador_id")
            .set_index("indicador_id")["existencia_fuente"]
        )
        df["disponibilidad_fuente"] = df["indicador_id"].map(mejor_fuente)
    else:
        df["disponibilidad_fuente"] = None
    df["disponibilidad_fuente"] = df["disponibilidad_fuente"].fillna("Sin fuente registrada")

    return df


def mostrar_dashboard() -> None:
    """Vista de tablero interactivo: gráficos y KPIs sobre indicadores,
    fuentes y factibilidad, con filtros dinámicos. Accesible para todos los roles."""
    st.header("📊 Tablero de Control — SIDOE ONE")

    es_publico = st.session_state.get("usuario") is None
    df = _cargar_datos(es_publico, config.DB_PATH)
    if df.empty:
        st.info("⚠️ Cargue datos para visualizar el tablero.")
        return

    total = len(df)
    f1 = len(df[df["categoria_factibilidad"] == CAT_I])
    f2 = len(df[df["categoria_factibilidad"] == CAT_II])
    f3 = len(df[df["categoria_factibilidad"] == CAT_III])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Indicadores", total)
    k2.metric(CAT_I, f1)
    k3.metric(CAT_II, f2)
    k4.metric(CAT_III, f3)
    st.divider()

    # ── Distribución por Factibilidad ────────────────────────────────────────
    st.subheader("Distribución de Factibilidad")
    fcol1, fcol2 = st.columns(2)
    with fcol1:
        filtro_subdominio = st.multiselect(
            "Filtrar por Subdominio de Actividad Estadística",
            sorted(df["subdominio_actividad_estadistica"].dropna().unique()),
            key="dash_filtro_subdominio",
        )
    with fcol2:
        filtro_sector = st.multiselect(
            "Filtrar por Sector IOE",
            sorted(df["sector_ioe"].dropna().unique()),
            key="dash_filtro_sector",
        )

    df_charts = df.copy()
    if filtro_subdominio:
        df_charts = df_charts[df_charts["subdominio_actividad_estadistica"].isin(filtro_subdominio)]
    if filtro_sector:
        df_charts = df_charts[df_charts["sector_ioe"].isin(filtro_sector)]

    if df_charts.empty:
        st.warning("No hay indicadores que coincidan con los filtros seleccionados.")
    else:
        conteo = df_charts["categoria_factibilidad"].value_counts().reset_index()
        conteo.columns = ["Factibilidad", "Cantidad"]
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Distribución por Factibilidad**")
            fig = px.pie(
                conteo, values="Cantidad", names="Factibilidad",
                color="Factibilidad", color_discrete_map=_COLORES, hole=0.4,
            )
            fig.update_traces(textinfo="percent+label")
            _grafico_descargable(fig, "distribucion_por_factibilidad")
        with col2:
            st.markdown("**Indicadores por Categoría**")
            fig2 = px.bar(
                conteo, x="Factibilidad", y="Cantidad",
                color="Factibilidad", text="Cantidad", color_discrete_map=_COLORES,
            )
            fig2.update_traces(textposition="outside")
            _grafico_descargable(fig2, "indicadores_por_categoria")

    st.divider()
    st.subheader("Cruce: Generador de Demanda vs Factibilidad")
    cruce = pd.crosstab(
        df["generador_demanda"], df["categoria_factibilidad"],
        margins=True, margins_name="Total",
    )
    orden = [c for c in [CAT_I, CAT_II, CAT_III, "Total"] if c in cruce.columns]
    st.table(cruce[orden])

    st.divider()
    st.subheader("🔀 Análisis de Cruces Dinámicos")
    st.caption(
        "Elige las dimensiones que quieres cruzar y el gráfico se actualiza al instante."
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        dim1_label = st.selectbox(
            "Dimensión principal", list(_DIMENSIONES.keys()), index=0, key="cruce_dim1"
        )
    with c2:
        opciones_dim2 = ["Ninguna"] + [d for d in _DIMENSIONES.keys() if d != dim1_label]
        dim2_label = st.selectbox("Cruzar con", opciones_dim2, index=0, key="cruce_dim2")
    with c3:
        tipo_grafico = st.selectbox(
            "Tipo de gráfico", ["Barras", "Pastel", "Treemap", "Mapa de calor"],
            key="cruce_tipo",
        )
    with c4:
        modo_valor = st.selectbox("Mostrar como", ["Cantidad", "Porcentaje"], key="cruce_modo")

    tiene_cruce = dim2_label != "Ninguna"
    if tipo_grafico == "Pastel" and tiene_cruce:
        st.info("El gráfico de Pastel solo admite una dimensión; se ignorará 'Cruzar con'.")
        tiene_cruce = False
    if tipo_grafico == "Mapa de calor" and not tiene_cruce:
        st.warning("El Mapa de calor requiere dos dimensiones. Selecciona 'Cruzar con'.")
        tipo_grafico = "Barras"

    col1n = _DIMENSIONES[dim1_label]
    df_cruce = df.copy()

    if tiene_cruce:
        col2n = _DIMENSIONES[dim2_label]
        base = df_cruce[[col1n, col2n]].dropna()
        tabla = pd.crosstab(base[col1n], base[col2n])

        if tipo_grafico == "Mapa de calor":
            tabla_plot = tabla
            if modo_valor == "Porcentaje" and tabla_plot.values.sum() > 0:
                tabla_plot = (tabla_plot.div(tabla_plot.sum(axis=1), axis=0) * 100).round(1)
            fig3 = px.imshow(
                tabla_plot,
                labels=dict(
                    x=dim2_label, y=dim1_label,
                    color="Porcentaje" if modo_valor == "Porcentaje" else "Cantidad",
                ),
                text_auto=True,
                color_continuous_scale="Blues",
                aspect="auto",
            )
        elif tipo_grafico == "Treemap":
            if modo_valor == "Porcentaje":
                st.caption("El modo Porcentaje no aplica a Treemap; se muestra el conteo.")
            df_tm = base.copy()
            df_tm["Cantidad"] = 1
            fig3 = px.treemap(df_tm, path=[col1n, col2n], values="Cantidad", color=col1n)
        else:  # Barras
            tabla_valores = tabla
            if modo_valor == "Porcentaje":
                tabla_valores = (tabla.div(tabla.sum(axis=1), axis=0) * 100).round(1)
            tabla_long = tabla_valores.reset_index().melt(
                id_vars=col1n, var_name=col2n, value_name="Valor"
            )
            fig3 = px.bar(tabla_long, x=col1n, y="Valor", color=col2n, barmode="group", text="Valor")
            fig3.update_traces(textposition="outside")
            fig3.update_layout(
                yaxis_title="Porcentaje (%)" if modo_valor == "Porcentaje" else "Cantidad"
            )
    else:
        serie = df_cruce[col1n].dropna().value_counts()
        if modo_valor == "Porcentaje" and serie.sum() > 0:
            serie = (serie / serie.sum() * 100).round(1)
        tabla_uni = serie.reset_index()
        tabla_uni.columns = [col1n, "Valor"]

        if tipo_grafico == "Pastel":
            fig3 = px.pie(tabla_uni, names=col1n, values="Valor", hole=0.4)
            fig3.update_traces(textinfo="percent+label")
        elif tipo_grafico == "Treemap":
            if modo_valor == "Porcentaje":
                st.caption("El modo Porcentaje no aplica a Treemap; se muestra el conteo.")
                serie = df_cruce[col1n].dropna().value_counts()
                tabla_uni = serie.reset_index()
                tabla_uni.columns = [col1n, "Valor"]
            fig3 = px.treemap(tabla_uni, path=[col1n], values="Valor")
        else:  # Barras
            fig3 = px.bar(tabla_uni, x=col1n, y="Valor", text="Valor", color=col1n)
            fig3.update_traces(textposition="outside")
            fig3.update_layout(
                yaxis_title="Porcentaje (%)" if modo_valor == "Porcentaje" else "Cantidad",
                showlegend=False,
            )

    _grafico_descargable(fig3, "cruce_dinamico_sidoe", alto=520)
