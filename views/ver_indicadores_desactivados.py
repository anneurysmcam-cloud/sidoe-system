"""views/ver_indicadores_desactivados.py — Indicadores desactivados (rol supervisor)."""

import pandas as pd
import streamlit as st

from data import database as db_mod
from security.auth import require_role

_QUERY_DESACTIVADOS = """
    SELECT i.codigo, i.indicador, i.generador_demanda, i.eje,
           i.area_misional_one, i.sector_ioe, i.periodicidad_indicador
    FROM indicadores_resuelto i
    WHERE i.estado_indicador = 'Desactivado'
    ORDER BY i.codigo
"""


@require_role(["supervisor"])
def mostrar_indicadores_desactivados() -> None:
    """Vista de solo lectura que lista los indicadores en estado
    'Desactivado' (excluidos de Dashboard y Consultas). Permite
    reactivarlos. Accesible solo para el rol supervisor (reestructuración
    de roles: es quien controla la desactivación de indicadores)."""
    st.header("🚫 Indicadores Desactivados")
    st.caption(
        "Estos indicadores no aparecen en el Dashboard, no se pueden consultar "
        "ni exportar, y tampoco figuran en 'Eliminar Indicador'. Para restaurarlos, "
        "vaya a 'Actualizar Indicador' y cambie su Estado a 'Activo'."
    )

    conn = db_mod.obtener_conexion()
    df = pd.read_sql_query(_QUERY_DESACTIVADOS, conn)
    conn.close()

    if df.empty:
        st.success("✅ No hay indicadores desactivados actualmente.")
        return

    st.metric("Total de indicadores desactivados", len(df))
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        filtro_gen = st.multiselect(
            "Generador de Demanda", sorted(df["generador_demanda"].dropna().unique())
        )
    with col2:
        buscar = st.text_input("Buscar por nombre o código")

    df_f = df.copy()
    if filtro_gen:
        df_f = df_f[df_f["generador_demanda"].isin(filtro_gen)]
    if buscar:
        df_f = df_f[
            df_f["indicador"].str.contains(buscar, case=False, na=False, regex=False)
            | df_f["codigo"].str.contains(buscar, case=False, na=False, regex=False)
        ]

    df_f = df_f.rename(columns={
        "codigo": "Código", "indicador": "Indicador", "generador_demanda": "Generador",
        "eje": "Eje", "area_misional_one": "Área Misional ONE",
        "sector_ioe": "Sector IOE", "periodicidad_indicador": "Periodicidad",
    })
    st.write(f"**{len(df_f)}** de **{len(df)}** indicadores desactivados")
    st.dataframe(df_f, width="stretch")
