"""
models/logs.py
==============
Funciones de auditoría para registrar acciones administrativas en la tabla
``auditoria``. Se distinguen dos variantes:

- ``registrar_log(cursor, ...)`` — participa de una transacción ya abierta;
  si la operación falla, el log se revierte junto con el resto del cambio.
- ``registrar_log_standalone(...)`` — abre su propia conexión, útil para
  acciones fuera del ciclo CRUD de indicadores (gestión de usuarios, etc.).
"""

import logging

from data import database as db_mod

logger = logging.getLogger(__name__)


def registrar_log(cursor, usuario_id: int, accion: str, detalle: str = "") -> None:
    """Inserta un registro de auditoría dentro de una transacción ya abierta.

    Args:
        cursor: Cursor activo de la transacción en curso.
        usuario_id: ID del usuario que realiza la acción.
        accion: Categoría de la acción (CREAR, ACTUALIZAR, ELIMINAR, etc.).
        detalle: Descripción libre de lo que se hizo.
    """
    if usuario_id is None:
        logger.warning(
            "registrar_log llamado sin usuario_id para acción '%s'. "
            "El registro de auditoría se omitirá.",
            accion,
        )
        return
    cursor.execute(
        "INSERT INTO auditoria (usuario_id, accion, detalle) VALUES (?, ?, ?)",
        (usuario_id, accion, detalle),
    )


def registrar_log_standalone(
    usuario_id: int, accion: str, detalle: str = ""
) -> None:
    """Registra una acción de auditoría abriendo su propia conexión.

    Usar cuando no hay una transacción previa disponible (p. ej. gestión de
    usuarios, operaciones de mantenimiento).
    """
    if usuario_id is None:
        logger.warning(
            "registrar_log_standalone llamado sin usuario_id para acción '%s'. "
            "El registro de auditoría se omitirá.",
            accion,
        )
        return
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        registrar_log(cursor, usuario_id, accion, detalle)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception(
            "Error al registrar auditoría standalone: acción='%s', usuario_id=%s.",
            accion,
            usuario_id,
        )
    finally:
        conn.close()
