"""views/ver_auditoria.py — Vista de auditoría del sistema (solo administradores)."""

import pandas as pd
import streamlit as st

from data import database as db_mod
from security.auth import require_role
from utils.helpers import convertir_columna_utc_a_rd

# Acciones sobre indicadores (ver models/crud_indicadores.py). Incluye el
# flujo de aprobación (agosto-2026: APROBAR_PUBLICACION, AUTO_DESACTIVAR) y
# la sincronización de indicadores referenciados (SINCRONIZAR_REFERENCIA),
# que faltaban en este filtro pese a ya registrarse en auditoría desde hace
# varias sesiones — no se podían filtrar por acción aunque sí aparecían en
# el listado general (bug reportado por Randy).
_ACCIONES_INDICADOR = [
    "CREAR", "ACTUALIZAR", "ELIMINAR", "CAMBIO_PUBLICACION", "CAMBIO_ESTADO",
    "APROBAR_PUBLICACION", "SINCRONIZAR_REFERENCIA", "AUTO_DESACTIVAR",
]
_ACCIONES_USUARIO = [
    "CREAR_USUARIO", "CAMBIAR_ROL", "ACTIVAR_USUARIO",
    "DESACTIVAR_USUARIO", "ELIMINAR_USUARIO", "CAMBIAR_PASSWORD",
    "RESET_PASSWORD_ADMIN",
]
# Acciones de seguridad/2FA y de sistema (ver views/admin_usuarios.py y
# app.py::_procesar_configuracion_2fa_obligatoria /
# _procesar_verificacion_2fa) — tampoco estaban en el filtro.
_ACCIONES_SEGURIDAD = [
    "ACTIVAR_2FA", "DESACTIVAR_2FA", "DESACTIVAR_2FA_ADMIN",
    "ACTIVAR_2FA_OBLIGATORIO", "EXIGIR_2FA", "QUITAR_EXIGENCIA_2FA",
    "TOTP_CODIGOS_REGENERADOS", "TOTP_CODIGO_RESPALDO_USADO", "BACKUP_DB",
]

_TAMANOS_PAGINA = [25, 50, 100, 200]


def _construir_filtros(usuario_filtro: str, accion_filtro: str) -> tuple[str, list]:
    """Construye la cláusula WHERE parametrizada según los filtros activos."""
    condiciones = []
    parametros: list = []
    if usuario_filtro:
        condiciones.append("u.username LIKE ? ESCAPE '\\'")
        escapado = usuario_filtro.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parametros.append(f"%{escapado}%")
    if accion_filtro:
        condiciones.append("a.accion = ?")
        parametros.append(accion_filtro)
    clausula = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return clausula, parametros


@require_role(["administrador"])
def mostrar_ver_auditoria() -> None:
    """Vista de auditoría: lista el historial de acciones de usuarios
    (creación, edición, eliminación de indicadores y usuarios) con
    filtros y paginación real a nivel SQL. Accesible solo para
    administradores.

    A diferencia de la versión anterior, esta vista NUNCA carga la tabla
    completa en memoria: los filtros y el LIMIT/OFFSET se aplican en la
    base de datos, porque `auditoria` solo crece con el tiempo y cargar
    todo el historial en cada render degrada progresivamente.
    """
    st.header("📜 Auditoría del Sistema")

    st.subheader("🔍 Filtros")
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        usuario_filtro = st.text_input("Filtrar por usuario")
    with col2:
        accion_filtro = st.selectbox(
            "Filtrar por acción",
            [""] + _ACCIONES_INDICADOR + _ACCIONES_USUARIO + _ACCIONES_SEGURIDAD,
        )
    with col3:
        tamano_pagina = st.selectbox("Filas por página", _TAMANOS_PAGINA, index=1)

    clausula_where, parametros = _construir_filtros(usuario_filtro, accion_filtro)

    conn = db_mod.obtener_conexion()
    try:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM auditoria a
            LEFT JOIN usuarios u ON u.id = a.usuario_id
            {clausula_where}
            """,
            parametros,
        ).fetchone()[0]

        if total == 0:
            st.info("No hay registros de auditoría que coincidan con los filtros." if clausula_where
                     else "No hay registros de auditoría todavía.")
            return

        total_paginas = max(1, (total + tamano_pagina - 1) // tamano_pagina)

        # Reinicia a la página 1 si cambian los filtros o si la página
        # actual queda fuera de rango tras un cambio de tamaño de página.
        clave_estado = "auditoria_pagina_actual"
        if clave_estado not in st.session_state:
            st.session_state[clave_estado] = 1
        pagina_actual = min(st.session_state[clave_estado], total_paginas)

        col_nav1, col_nav2, col_nav3 = st.columns([1, 2, 1])
        with col_nav1:
            if st.button("⬅ Anterior", disabled=pagina_actual <= 1):
                pagina_actual -= 1
        with col_nav3:
            if st.button("Siguiente ➡", disabled=pagina_actual >= total_paginas):
                pagina_actual += 1
        with col_nav2:
            st.markdown(
                f"**Página {pagina_actual} de {total_paginas}** — {total} registro(s)"
            )
        st.session_state[clave_estado] = pagina_actual
        offset = (pagina_actual - 1) * tamano_pagina

        df = pd.read_sql_query(
            f"""
            SELECT a.id, a.timestamp, u.username AS usuario, a.accion, a.detalle
            FROM auditoria a
            LEFT JOIN usuarios u ON u.id = a.usuario_id
            {clausula_where}
            ORDER BY a.timestamp DESC
            LIMIT ? OFFSET ?
            """,
            conn,
            params=[*parametros, tamano_pagina, offset],
        )
    finally:
        conn.close()

    # auditoria.timestamp se almacena en UTC (SQLite datetime('now')); se
    # convierte a hora local de RD solo para presentación.
    df["timestamp"] = convertir_columna_utc_a_rd(df["timestamp"])

    st.dataframe(df, width="stretch")
