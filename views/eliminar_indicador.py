"""views/eliminar_indicador.py — Eliminación de indicadores (supervisor)."""

import pandas as pd
import streamlit as st

from config import UMBRAL_ELIMINACIONES_AUTOBLOQUEO
from data import database as db_mod
from models.crud_indicadores import borrar_indicador
from security.auth import logout, require_role
from utils.ui_mensajes import marcar_mensaje, mostrar_mensaje_pendiente


def _usuario_id() -> int | None:
    """Devuelve el id del usuario autenticado en la sesión actual de Streamlit, o None si no hay sesión."""
    return (st.session_state.get("usuario") or {}).get("id")


@st.dialog("¿Confirmar eliminación?")
def _confirmar_eliminacion(id_eliminar: int, nombre: str) -> None:
    """Diálogo modal de confirmación antes de borrar un indicador
    permanentemente; ejecuta la eliminación solo si el usuario confirma."""
    st.write(f"Está a punto de borrar permanentemente: **{nombre}**")
    st.warning("⚠️ Se eliminarán también sus fuentes y cálculo de factibilidad.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            uid = _usuario_id()
            exito, msg = borrar_indicador(id_eliminar, uid)

            # borrar_indicador() puede haber desactivado la cuenta si se
            # alcanzó el límite de eliminaciones (config.py,
            # UMBRAL_ELIMINACIONES_AUTOBLOQUEO) — se revisa acá, no
            # parseando el texto del mensaje, para no depender de su
            # redacción. Si pasó, se cierra la sesión de inmediato: la app
            # nunca vuelve a leer `activo` de la BD después del login (el
            # rol/estado queda cacheado en session_state por el resto de
            # la sesión), así que sin este chequeo el supervisor podría
            # seguir eliminando indicadores indefinidamente en la misma
            # sesión aunque su cuenta ya esté desactivada — el bloqueo
            # solo le pegaría en su PRÓXIMO intento de login, no ahora,
            # que es cuando realmente importa.
            cuenta_desactivada = False
            if exito and uid is not None:
                conn = db_mod.obtener_conexion()
                fila = conn.execute(
                    "SELECT activo FROM usuarios WHERE id = ?", (uid,)
                ).fetchone()
                conn.close()
                cuenta_desactivada = bool(fila) and fila[0] == 0

            if cuenta_desactivada:
                logout(st.session_state)
                marcar_mensaje("warning", msg)
            else:
                marcar_mensaje("success" if exito else "error", msg)
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


@require_role(["supervisor"])
def mostrar_eliminar_indicador() -> None:
    """Vista de eliminación: lista indicadores activos y permite borrarlos
    (junto con sus fuentes y cálculo de factibilidad) tras confirmación.
    Accesible únicamente para el rol supervisor (reestructuración de roles,
    ver imagen de la jefa: "Elimina indicadores").

    Salvaguarda (agosto-2026): cada 5 eliminaciones consecutivas (ver
    config.UMBRAL_ELIMINACIONES_AUTOBLOQUEO) la cuenta se desactiva
    automáticamente y la sesión se cierra — un administrador debe
    reactivarla desde 'Administrar Usuarios' antes de que este supervisor
    pueda volver a entrar. Para eliminar un volumen grande de indicadores
    de una vez (más de lo que este límite permite sin interrupciones),
    ver el protocolo de eliminación masiva vía TI documentado en
    DESPLIEGUE_PRODUCCION.md.
    """
    st.header("🗑️ Eliminación de Indicadores")
    mostrar_mensaje_pendiente()
    st.caption(
        "Solo se listan indicadores en estado 'Activo'. Para eliminar uno "
        "'Desactivado', primero reactívelo desde 'Actualizar indicador'."
    )
    st.caption(
        f"⚠️ Por seguridad, cada {UMBRAL_ELIMINACIONES_AUTOBLOQUEO} "
        "eliminaciones consecutivas su cuenta se desactiva automáticamente "
        "y debe ser reactivada por un administrador."
    )

    conn = db_mod.obtener_conexion()
    df = pd.read_sql_query(
        "SELECT id, codigo, indicador FROM indicadores "
        "WHERE estado_indicador = 'Activo' ORDER BY codigo",
        conn,
    )
    conn.close()

    if df.empty:
        st.info("No hay indicadores activos registrados.")
        return

    opciones = {
        f"{codigo} — {indicador}": id_
        for id_, codigo, indicador in zip(
            df["id"], df["codigo"], df["indicador"], strict=True
        )
    }
    sel = st.selectbox("Seleccione el indicador a eliminar:", list(opciones.keys()))
    id_elim = opciones[sel]

    st.warning("⚠️ Esta acción no se puede deshacer.")
    if st.button("❌ Eliminar Indicador"):
        _confirmar_eliminacion(id_elim, sel)
