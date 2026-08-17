"""views/consultas.py — Consulta general de indicadores con filtros y exportación Excel."""

import io

import pandas as pd
import streamlit as st

import config
from data import database as db_mod
from data.diccionario_datos import (
    ajustar_ancho_columnas_auto,
    aplicar_formato_encabezado_hoja_datos,
    escribir_hoja_diccionario_datos,
)
from utils.helpers import convertir_columna_utc_a_rd

# ---------------------------------------------------------------------------
# Queries separados por granularidad para evitar duplicar filas cuando un
# indicador tiene múltiples fuentes (solución: 1 query por tabla).
# ---------------------------------------------------------------------------

def _query_indicadores(es_publico: bool) -> str:
    filtro = " AND i.estado_publicacion = 'publicado'" if es_publico else ""
    return f"""
    SELECT i.codigo, i.generador_demanda, i.eje, i.politica_gobierno,
           ep.otros_ejes_politicas,
           i.indicador, i.indicadores_duplicados AS indicador_referenciado,
           i.dominio_actividad_estadistica, i.subdominio_actividad_estadistica,
           i.area_misional_one, i.sector_ioe,
           i.requerimiento_clasificacion, i.especificar_clasificacion,
           i.metodo_calculo, i.ficha_tecnica,
           i.numerador, i.denominador, i.unidad_medida,
           i.sexo AS sexo_indicador, i.edad AS edad_indicador,
           i.territorio AS territorio_indicador,
           i.discapacidad AS discapacidad_indicador,
           i.nivel_ingreso AS nivel_ingreso_indicador,
           i.periodicidad_indicador, i.ente_responsable_metodologia, i.alcance_metodologico,
           (SELECT COUNT(*) FROM fuentes_indicador f WHERE f.indicador_id = i.id) AS num_fuentes
    FROM indicadores_resuelto i
    LEFT JOIN ejes_politicas_secundarios_por_indicador ep ON ep.indicador_id = i.id
    WHERE i.estado_indicador = 'Activo'{filtro}
    ORDER BY i.codigo
"""


def _query_fuentes(es_publico: bool) -> str:
    filtro = " AND i.estado_publicacion = 'publicado'" if es_publico else ""
    return f"""
    SELECT f.id AS fuente_id, i.codigo AS indicador_codigo, i.indicador AS indicador_nombre,
           f.nombre_fuente, f.tipo_fuente, f.institucion_productora,
           f.periodicidad AS periodicidad_fuente, f.existencia_fuente,
           f.sexo AS sexo_fuente, f.edad AS edad_fuente,
           f.territorio AS territorio_fuente, f.discapacidad AS discapacidad_fuente,
           f.nivel_ingreso_socioeconomico, f.ioe, f.ra,
           f.calculado_datos_agregados, f.hipervinculo_ultimo_calculo,
           f.anio_ultimo_dato_disponible, f.comentarios
    FROM fuentes_resuelto f
    JOIN indicadores_resuelto i ON i.id = f.indicador_id
    WHERE i.estado_indicador = 'Activo'{filtro}
    ORDER BY i.codigo, f.id
"""


def _query_factibilidad(es_publico: bool) -> str:
    filtro = " AND i.estado_publicacion = 'publicado'" if es_publico else ""
    return f"""
    SELECT i.codigo, i.indicador, i.generador_demanda,
           c.c1_metodologia, c.c21_existencia_fuente, c.c22_disponibilidad,
           c.c23_periodicidad_establecida, c.c31_posee_desagregacion,
           c.num_desagregaciones_requeridas, c.num_desagregaciones_disponibles,
           c.articulacion_fuentes, c.armonizacion_conceptual, c.subregistro_cobertura,
           c.cobertura_territorial, c.estructura_datos, c.variables_calculo,
           c.score_factibilidad_final AS puntaje,
           c.categoria_factibilidad AS factibilidad,
           c.calc_timestamp
    FROM indicadores_resuelto i
    LEFT JOIN calculo_factibilidad c ON c.indicador_id = i.id
    WHERE i.estado_indicador = 'Activo'{filtro}
    ORDER BY i.codigo
"""

# Columnas exclusivas de factibilidad (no repetir en hoja Indicadores del Excel)
_COLS_SOLO_FACTIBILIDAD = [
    "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
    "c23_periodicidad_establecida", "c31_posee_desagregacion",
    "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
    "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
    "cobertura_territorial", "estructura_datos", "variables_calculo",
    "puntaje", "factibilidad", "calc_timestamp",
]


# ---------------------------------------------------------------------------
# Campos personalizados (tablas EAV) — se agregan como columnas extra
# ---------------------------------------------------------------------------

def _pivot_campos_personalizados(conn, componente: str) -> pd.DataFrame | None:
    """Devuelve un DataFrame con '_entidad_id' + 1 col por categoría personalizada activa."""
    categorias = pd.read_sql_query(
        "SELECT nombre_visible FROM auxiliares_categorias "
        "WHERE aplica_a = ? AND activo = 1 ORDER BY nombre_visible",
        conn, params=(componente,),
    )
    if categorias.empty:
        return None

    tabla = (
        "indicador_campos_personalizados"
        if componente == "indicador"
        else "fuente_campos_personalizados"
    )
    col_entidad = "indicador_id" if componente == "indicador" else "fuente_id"

    datos = pd.read_sql_query(
        f"""
        SELECT cp.{col_entidad} AS _entidad_id,
               ac.nombre_visible AS _categoria,
               av.valor AS _valor
        FROM {tabla} cp
        JOIN auxiliares_categorias ac
            ON ac.id = cp.categoria_id AND ac.aplica_a = ? AND ac.activo = 1
        LEFT JOIN auxiliares_valores av ON av.id = cp.valor_id
        """,
        conn, params=(componente,),
    )

    columnas_categorias = categorias["nombre_visible"].tolist()
    if datos.empty:
        return pd.DataFrame(columns=["_entidad_id"] + columnas_categorias)

    pivot = datos.pivot_table(
        index="_entidad_id", columns="_categoria", values="_valor", aggfunc="first"
    )
    pivot = pivot.reindex(columns=columnas_categorias).reset_index()
    return pivot


def _agregar_campos_personalizados(
    conn, df: pd.DataFrame, componente: str, id_map: dict | None = None
) -> pd.DataFrame:
    """Fusiona columnas de categorías personalizadas al final de df."""
    pivot = _pivot_campos_personalizados(conn, componente)
    if pivot is None or (pivot.empty and len(pivot.columns) <= 1):
        return df

    if componente == "indicador":
        pivot["codigo"] = pivot["_entidad_id"].map(id_map)
        pivot = pivot.drop(columns=["_entidad_id"])
        return df.merge(pivot, on="codigo", how="left")
    else:
        pivot = pivot.rename(columns={"_entidad_id": "fuente_id"})
        return df.merge(pivot, on="fuente_id", how="left")


# ---------------------------------------------------------------------------
# Carga de datos (cacheada)
# ---------------------------------------------------------------------------

# TTL corto (no 300s como en crud_auxiliares.py, que son catálogos de baja
# frecuencia de cambio): esta vista mezcla datos de indicadores/fuentes que
# editor/administrador modifican con regularidad, así que 60s balancea
# rendimiento (evita repetir 5+ queries + pivots en cada rerun del filtro)
# contra frescura de datos tras un guardado reciente.
_CACHE_TTL_CONSULTAS = 60


@st.cache_data(ttl=_CACHE_TTL_CONSULTAS)
def _cargar_datos_consultas(
    es_publico: bool, db_path: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Ejecuta todas las queries + pivots de campos personalizados una sola
    vez por combinación (es_publico, db_path) y TTL, en vez de en cada rerun
    de Streamlit (cada cambio de filtro dispara un rerun completo del script).

    ``db_path`` se recibe solo para formar parte de la clave de caché de
    Streamlit (``st.cache_data`` es un caché de proceso, no de sesión): en
    producción DB_PATH nunca cambia, así que no afecta el hit rate; en
    tests, cada BD temporal distinta obtiene su propia entrada de caché en
    vez de reutilizar resultados de otra BD.
    """
    conn = db_mod.obtener_conexion()
    try:
        df_ind = pd.read_sql_query(_query_indicadores(es_publico), conn)
        df_fac = pd.read_sql_query(_query_factibilidad(es_publico), conn)
        df_fuentes = pd.read_sql_query(_query_fuentes(es_publico), conn)

        # calc_timestamp se almacena en UTC (SQLite datetime('now')); se
        # convierte a hora local de RD solo para presentación/export (punto 7).
        if "calc_timestamp" in df_fac.columns:
            df_fac["calc_timestamp"] = convertir_columna_utc_a_rd(df_fac["calc_timestamp"])

        if df_ind.empty:
            return df_ind, df_fac, df_fuentes

        id_map_indicadores = pd.read_sql_query(
            "SELECT id, codigo FROM indicadores", conn
        ).set_index("id")["codigo"].to_dict()

        df_ind = _agregar_campos_personalizados(conn, df_ind, "indicador", id_map_indicadores)
        df_fuentes = _agregar_campos_personalizados(conn, df_fuentes, "fuente")
    finally:
        conn.close()

    df_fuentes = df_fuentes.drop(columns=["fuente_id"], errors="ignore")

    # Vista en pantalla: 1 fila por indicador (indicador + factibilidad)
    df = df_ind.merge(
        df_fac.drop(columns=["indicador", "generador_demanda"], errors="ignore"),
        on="codigo", how="left",
    )
    return df, df_fac, df_fuentes


# ---------------------------------------------------------------------------
# Vista principal
# ---------------------------------------------------------------------------

def mostrar_consultas() -> None:
    """Vista de consulta general: permite filtrar indicadores por múltiples
    criterios y exportar los resultados a Excel. Accesible para todos los roles."""
    st.header("🔍 Consulta General de Indicadores")

    es_publico = st.session_state.get("usuario") is None
    df, df_fac, df_fuentes = _cargar_datos_consultas(es_publico, config.DB_PATH)

    if df.empty:
        st.info("La base de datos está vacía.")
        return

    # ── Filtros ──────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_gen = st.multiselect(
            "Generador de Demanda", sorted(df["generador_demanda"].dropna().unique())
        )
    with col2:
        filtro_fac = st.multiselect(
            "Factibilidad", sorted(df["factibilidad"].dropna().unique())
        )
    with col3:
        buscar = st.text_input("Buscar por nombre o código")

    col4, col5 = st.columns(2)
    with col4:
        filtro_subdominio = st.multiselect(
            "Subdominio de Actividad Estadística",
            sorted(df["subdominio_actividad_estadistica"].dropna().unique()),
        )
    with col5:
        filtro_sector = st.multiselect(
            "Sector IOE", sorted(df["sector_ioe"].dropna().unique())
        )

    df_f = df.copy()
    if filtro_gen:
        df_f = df_f[df_f["generador_demanda"].isin(filtro_gen)]
    if filtro_fac:
        df_f = df_f[df_f["factibilidad"].isin(filtro_fac)]
    if filtro_subdominio:
        df_f = df_f[df_f["subdominio_actividad_estadistica"].isin(filtro_subdominio)]
    if filtro_sector:
        df_f = df_f[df_f["sector_ioe"].isin(filtro_sector)]
    if buscar:
        df_f = df_f[
            df_f["indicador"].str.contains(buscar, case=False, na=False, regex=False)
            | df_f["codigo"].str.contains(buscar, case=False, na=False, regex=False)
        ]

    st.write(f"**{len(df_f)}** registros encontrados")

    # ── Paginación explícita (50 filas por página) ─────────────────────────
    FILAS_POR_PAGINA = 50
    total_registros = len(df_f)
    total_paginas = max(1, -(-total_registros // FILAS_POR_PAGINA))  # ceil div

    if "consultas_pagina" not in st.session_state:
        st.session_state["consultas_pagina"] = 1
    # Si los filtros cambiaron y la página quedó fuera de rango, la ajustamos
    if st.session_state["consultas_pagina"] > total_paginas:
        st.session_state["consultas_pagina"] = total_paginas

    col_prev, col_info, col_next = st.columns([1, 3, 1])
    with col_prev:
        if st.button("⬅️ Anterior", disabled=st.session_state["consultas_pagina"] <= 1):
            st.session_state["consultas_pagina"] -= 1
            # Sin st.rerun(): el código de más abajo en este mismo script
            # (df_pagina = df_f.iloc[...]) ya lee el session_state
            # actualizado en esta misma pasada, así que un rerun explícito
            # aquí solo agregaba una vuelta completa redundante por clic.
    with col_next:
        if st.button("Siguiente ➡️", disabled=st.session_state["consultas_pagina"] >= total_paginas):
            st.session_state["consultas_pagina"] += 1
    with col_info:
        st.markdown(
            f"<div style='text-align:center; padding-top:0.4rem;'>"
            f"Página <strong>{st.session_state['consultas_pagina']}</strong> de "
            f"<strong>{total_paginas}</strong></div>",
            unsafe_allow_html=True,
        )

    pagina_actual = st.session_state["consultas_pagina"]
    inicio = (pagina_actual - 1) * FILAS_POR_PAGINA
    fin = inicio + FILAS_POR_PAGINA
    df_pagina = df_f.iloc[inicio:fin]

    st.caption(
        f"Mostrando filas {inicio + 1 if total_registros else 0}–"
        f"{min(fin, total_registros)} de {total_registros}"
    )
    st.dataframe(df_pagina, width="stretch")

    # ── Expander de fuentes por indicador ────────────────────────────────────
    with st.expander("📡 Ver fuentes de los indicadores filtrados"):
        codigos_visibles = sorted(df_f["codigo"].dropna().unique())
        if codigos_visibles:
            modo_vista = st.radio(
                "Modo de vista",
                ["Un indicador específico", "Todos los indicadores filtrados"],
                horizontal=True,
            )

            if modo_vista == "Un indicador específico":
                cod_sel = st.selectbox("Código del indicador", codigos_visibles)
                fuentes_vista = df_fuentes[df_fuentes["indicador_codigo"] == cod_sel]
                columnas_a_ocultar = ["indicador_codigo", "indicador_nombre"]
            else:
                fuentes_vista = df_fuentes[
                    df_fuentes["indicador_codigo"].isin(codigos_visibles)
                ].sort_values(["indicador_codigo"])
                columnas_a_ocultar = []

            if fuentes_vista.empty:
                st.caption(
                    "Este indicador no tiene fuentes registradas."
                    if modo_vista == "Un indicador específico"
                    else "Los indicadores del filtro actual no tienen fuentes registradas."
                )
            else:
                if modo_vista == "Todos los indicadores filtrados":
                    st.caption(
                        f"{fuentes_vista['indicador_codigo'].nunique()} indicador(es), "
                        f"{len(fuentes_vista)} fuente(s) en total."
                    )
                st.dataframe(
                    fuentes_vista.drop(columns=columnas_a_ocultar, errors="ignore"),
                    width="stretch",
                    hide_index=True,
                )
        else:
            st.caption("No hay indicadores en el filtro actual.")

    # ── Exportación Excel 3 hojas ─────────────────────────────────────────────
    # Antes esto se regeneraba en CADA rerun del script (cualquier clic en la
    # vista, incluida la paginación), aunque el usuario no fuera a descargar
    # nada. Ahora se genera solo al pedirlo explícitamente, con spinner, y se
    # cachea en session_state por firma de filtro para no repetir el trabajo
    # si el usuario hace clic en "Descargar" más de una vez sin cambiar nada.
    codigos_filtrados = set(df_f["codigo"])
    firma_export = (tuple(sorted(codigos_filtrados)), es_publico)

    if st.button("📊 Preparar Excel para descargar"):
        with st.spinner("Generando archivo Excel..."):
            df_ind_export = df_f.drop(
                columns=[c for c in _COLS_SOLO_FACTIBILIDAD if c in df_f.columns],
                errors="ignore",
            )
            df_fuentes_export = df_fuentes[
                df_fuentes["indicador_codigo"].isin(codigos_filtrados)
            ]
            df_fac_export = df_fac[df_fac["codigo"].isin(codigos_filtrados)]

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df_ind_export.to_excel(writer, index=False, sheet_name="Indicadores")
                df_fuentes_export.to_excel(writer, index=False, sheet_name="Fuentes")
                df_fac_export.to_excel(writer, index=False, sheet_name="Factibilidad")
                # Mismo estilo de encabezado azul/blanco institucional que
                # ya tenía la hoja "Diccionario de Datos" (a pedido de
                # Randy, para que las 4 hojas del archivo compartan
                # identidad visual).
                aplicar_formato_encabezado_hoja_datos(
                    writer.sheets["Indicadores"], len(df_ind_export.columns)
                )
                aplicar_formato_encabezado_hoja_datos(
                    writer.sheets["Fuentes"], len(df_fuentes_export.columns)
                )
                aplicar_formato_encabezado_hoja_datos(
                    writer.sheets["Factibilidad"], len(df_fac_export.columns)
                )
                # Ancho de columna ajustado al contenido real (evita el
                # texto cortado que se veía con el ancho fijo por defecto
                # de openpyxl en hojas con muchas columnas, ej. Indicadores).
                ajustar_ancho_columnas_auto(writer.sheets["Indicadores"], df_ind_export)
                ajustar_ancho_columnas_auto(writer.sheets["Fuentes"], df_fuentes_export)
                ajustar_ancho_columnas_auto(writer.sheets["Factibilidad"], df_fac_export)
                # Hoja "Diccionario de Datos": documenta, para las 3 hojas
                # anteriores, cada columna realmente exportada (incluye
                # campos personalizados de Auxiliares si están activos),
                # conforme a los Lineamientos ONE para diccionarios de
                # datos pasivos (ver data/diccionario_datos.py).
                escribir_hoja_diccionario_datos(
                    writer, df_ind_export, df_fuentes_export, df_fac_export
                )

            st.session_state["consultas_excel_bytes"] = buf.getvalue()
            st.session_state["consultas_excel_firma"] = firma_export

    excel_listo = (
        st.session_state.get("consultas_excel_firma") == firma_export
        and "consultas_excel_bytes" in st.session_state
    )
    if excel_listo:
        st.download_button(
            "📥 Descargar Excel (.xlsx)",
            st.session_state["consultas_excel_bytes"],
            "Matriz_SIDOE_ONE.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.caption(
            "Haz clic en \"Preparar Excel para descargar\" para generar el "
            "archivo con los filtros actuales."
        )
