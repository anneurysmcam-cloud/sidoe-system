"""views/aprobar_indicadores.py — Aprobación de publicación de indicadores (rol supervisor).

Reestructuración de roles (agosto-2026): todo indicador creado o
actualizado por Editor o Supervisor entra en estado 'borrador' (ver
views/crear_indicador.py y views/actualizar_indicador.py) y permanece
oculto de la vista pública sin sesión hasta que un Supervisor lo revisa y
lo aprueba aquí. Así la jefa se asegura de que ningún cambio llega al
público sin un segundo par de ojos que valide que es correcto.

Revisión sin salir de esta pantalla
------------------------------------
Cada fila indica si el indicador es contenido **🆕 Nuevo** (nunca ha sido
público) o una **✏️ Actualización** de algo que el público ya veía, y — si
es una actualización — un desplegable con el detalle campo por campo de lo
que cambió (ver models/revision_pendiente.py), para que el supervisor no
tenga que abrir 'Actualizar Indicador' y comparar a ojo contra lo que
recuerda. Si de todos modos quiere editar algo antes de aprobar (corregir
un error que ve en el diff, por ejemplo), el botón "Editar antes de
aprobar" lo lleva directo a 'Actualizar Indicador' con ese indicador ya
seleccionado.
"""

import pandas as pd
import streamlit as st

from data import database as db_mod
from models.crud_indicadores import aprobar_publicacion_indicador
from models.revision_pendiente import leer_cambios
from security.auth import require_role
from utils.ui_mensajes import marcar_mensaje, mostrar_mensaje_pendiente
from views._form_indicador_shared import usuario_id

_QUERY_PENDIENTES = """
    SELECT id, codigo, indicador, estado_indicador,
           revision_tipo, revision_detalle, revision_fecha
    FROM indicadores
    WHERE estado_publicacion = 'borrador'
    ORDER BY
        CASE WHEN revision_tipo = 'actualizado' THEN 0 ELSE 1 END,
        codigo
"""


def _ir_a_actualizar_indicador(indicador_id: int) -> None:
    """Navega a 'Actualizar Indicador' con este indicador ya preseleccionado
    (ver app.py: opcion_autenticada_preseleccionada / views/actualizar_indicador.py)."""
    st.session_state["_indicador_a_editar_id"] = indicador_id
    st.session_state["opcion_autenticada_preseleccionada"] = "Actualizar Indicador"
    st.rerun()


@require_role(["supervisor"])
def mostrar_aprobar_indicadores() -> None:
    """Vista de aprobación: lista los indicadores en estado 'borrador'
    (pendientes de revisión) y permite publicarlos tras confirmar que los
    cambios son correctos. Accesible únicamente para el rol supervisor."""
    st.header("✅ Aprobar Indicadores")
    mostrar_mensaje_pendiente()
    st.caption(
        "Todo indicador creado o actualizado por Editor o Supervisor —incluyendo "
        "agregar, editar o eliminar una fuente— queda en 'Borrador' hasta que un "
        "supervisor lo aprueba aquí y lo publica."
    )

    conn = db_mod.obtener_conexion()
    df = pd.read_sql_query(_QUERY_PENDIENTES, conn)
    conn.close()

    if df.empty:
        st.success("🎉 No hay indicadores pendientes de aprobación.")
        return

    n_nuevos = (df["revision_tipo"] == "nuevo").sum()
    n_actualizados = (df["revision_tipo"] == "actualizado").sum()
    st.info(
        f"📋 {len(df)} indicador(es) pendiente(s) de aprobación "
        f"— 🆕 {n_nuevos} nuevo(s), ✏️ {n_actualizados} actualización(es)."
    )
    st.divider()

    columnas = df.columns
    for fila_tupla in df.itertuples(index=False):
        fila = dict(zip(columnas, fila_tupla, strict=True))
        cambios = leer_cambios(fila["revision_detalle"])
        with st.container(border=True):
            col_info, col_accion = st.columns([4, 1])
            with col_info:
                if fila["revision_tipo"] == "actualizado":
                    st.markdown("✏️ **Actualización**")
                elif fila["revision_tipo"] == "nuevo":
                    st.markdown("🆕 **Nuevo indicador**")
                else:
                    # Borrador de antes de esta función (sin clasificar) —
                    # no se asume nada, se muestra neutro.
                    st.markdown("⏳ **Pendiente**")
                st.write(f"**{fila['codigo']}** — {fila['indicador']}")
                if fila["estado_indicador"] == "Desactivado":
                    st.caption("⚠️ Este indicador también está Desactivado.")

                if cambios:
                    with st.expander(f"Ver qué cambió ({len(cambios)} campo(s))"):
                        for cambio in cambios:
                            st.markdown(
                                f"**{cambio['campo']}**  \n"
                                f"~~{cambio['anterior']}~~ → **{cambio['nuevo']}**"
                            )
                elif fila["revision_tipo"] == "actualizado":
                    st.caption(
                        "No hay detalle de campos disponible para esta actualización "
                        "(borrador de antes de esta función, o sin cambios de contenido "
                        "más allá del estado)."
                    )
            with col_accion:
                if st.button(
                    "✅ Aprobar y publicar", key=f"aprobar_{fila['id']}",
                    width="stretch", type="primary",
                ):
                    exito, msg = aprobar_publicacion_indicador(
                        int(fila["id"]), usuario_id()
                    )
                    marcar_mensaje("success" if exito else "error", msg)
                    st.rerun()
                if st.button(
                    "✏️ Editar antes de aprobar", key=f"editar_{fila['id']}",
                    width="stretch",
                ):
                    _ir_a_actualizar_indicador(int(fila["id"]))
