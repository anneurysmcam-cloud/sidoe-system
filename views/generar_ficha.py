"""views/generar_ficha.py — Generación de fichas PDF de indicadores."""

import pandas as pd
import streamlit as st

from data import database as db_mod
from models.crud_indicadores import obtener_indicador_por_id
from tracking.generar_ficha_pdf import generar_ficha_pdf


def _construir_datos_ficha(indicador_id: int) -> tuple[dict, list[dict]]:
    """Prepara el dict del indicador y la lista completa de fuentes para la ficha PDF.

    Devuelve una tupla (ficha, fuentes): `ficha` son los campos propios del
    indicador (más el resumen de factibilidad), y `fuentes` es la lista
    completa de fuentes asociadas —un indicador puede tener más de una—,
    cada una con sus propios campos ya resueltos a texto.
    """
    datos = obtener_indicador_por_id(indicador_id)
    indicador = datos["indicador"]
    fuentes = datos["fuentes"]
    factibilidad = datos["factibilidad"]

    ficha = dict(indicador)
    # Campos de uso interno: no deben aparecer en la ficha pública
    ficha.pop("estado_indicador", None)
    # Las claves _id son para formularios, no para el PDF
    ficha = {k: v for k, v in ficha.items() if not k.endswith("_id")}

    # Un indicador puede estar asociado a varios pares Eje/Política (1:N).
    # 'ejes_politicas_todos' ya trae la lista completa; si no hay filas en
    # indicador_ejes_politicas (o la migración de backfill aún no corrió),
    # se recurre al par único legado 'eje'/'politica_gobierno' (punto 5:
    # antes la ficha solo mostraba ese primer par y perdía el resto).
    ejes_politicas = ficha.pop("ejes_politicas_todos", None)
    eje = ficha.pop("eje", None)
    politica = ficha.pop("politica_gobierno", None)
    ficha.pop("num_ejes_politicas", None)
    ficha["ejes_politicas"] = ejes_politicas or " / ".join(
        v for v in (eje, politica) if v
    )

    # El campo interno se llama 'indicadores_duplicados' (nombre legado de la
    # columna en BD/Excel oficial), pero conceptualmente ya no representa un
    # "duplicado" sino un vínculo entre indicadores referenciados que
    # comparten fuente/tratamiento metodológico (ver sincronizar_
    # contenido_referenciados()). En la ficha PDF, la etiqueta se toma
    # directamente de la clave del dict, así que se renombra solo para la
    # presentación — no se toca el nombre de columna en BD.
    if "indicadores_duplicados" in ficha:
        ficha["indicadores_referenciados"] = ficha.pop("indicadores_duplicados")

    ficha["cantidad_fuentes"] = len(fuentes)

    if factibilidad:
        ficha["score_factibilidad_final"] = factibilidad.get("score_factibilidad_final", "")
        ficha["categoria_factibilidad"] = factibilidad.get("categoria_factibilidad", "")

    return ficha, fuentes


def mostrar_generar_ficha() -> None:
    """Vista de generación de ficha: permite seleccionar un indicador y
    descargar su ficha técnica en PDF, incluyendo todas sus fuentes."""
    st.header("📄 Generación de Ficha de Indicador")

    conn = db_mod.obtener_conexion()
    es_publico = st.session_state.get("usuario") is None
    filtro_publicacion = " AND estado_publicacion = 'publicado'" if es_publico else ""
    df = pd.read_sql_query(
        "SELECT id, codigo, indicador FROM indicadores "
        f"WHERE estado_indicador = 'Activo'{filtro_publicacion} ORDER BY codigo",
        conn,
    )
    conn.close()

    if df.empty:
        st.warning("⚠️ No hay indicadores activos registrados.")
        return

    id_ficha = st.selectbox(
        "Selecciona el indicador para generar la ficha",
        options=df["id"],
        format_func=lambda x: (
            f"{df.loc[df['id'] == x, 'codigo'].values[0]} — "
            f"{df.loc[df['id'] == x, 'indicador'].values[0]}"
        ),
        key="ficha_indicador_seleccionado",
    )

    if not id_ficha:
        return

    # Generación explícita, disparada solo por este botón — evita que el PDF
    # se regenere implícitamente en cada rerun (p. ej. al cambiar la
    # selección), que es lo que causaba que download_button sirviera datos
    # de una selección anterior (desfasados un paso respecto a la actual).
    if st.button("📄 Generar ficha"):
        ficha, fuentes = _construir_datos_ficha(id_ficha)
        if ficha:
            st.session_state["_ficha_pdf_bytes"] = generar_ficha_pdf(ficha, fuentes)
            st.session_state["_ficha_pdf_indicador_id"] = id_ficha
        else:
            st.session_state.pop("_ficha_pdf_bytes", None)
            st.session_state.pop("_ficha_pdf_indicador_id", None)
            st.warning("⚠️ No se encontró el indicador seleccionado.")

    # El botón de descarga solo se muestra si los bytes en memoria
    # corresponden EXACTAMENTE al indicador actualmente seleccionado. Si el
    # usuario cambia la selección después de generar, el botón desaparece
    # hasta que vuelva a generar — así nunca se puede descargar una ficha
    # que no coincide con lo que está seleccionado en pantalla.
    if (
        st.session_state.get("_ficha_pdf_indicador_id") == id_ficha
        and "_ficha_pdf_bytes" in st.session_state
    ):
        st.download_button(
            label="⬇️ Descargar ficha PDF",
            data=st.session_state["_ficha_pdf_bytes"],
            file_name=f"ficha_indicador_{id_ficha}.pdf",
            mime="application/pdf",
            key=f"btn_descargar_ficha_{id_ficha}",
        )
