"""
models/crud_usuarios.py
========================
CRUD de la entidad ``usuarios``: lecturas y escrituras administrativas
(cambio de rol, activar/desactivar, exigencia de 2FA, eliminación).

Extraído de ``views/admin_usuarios.py`` (Hallazgo A del Informe de Auditoría
Arquitectónica, agosto 2026): antes esta entidad era la única del sistema
sin un módulo de modelo propio — el SQL de lectura y escritura vivía
directamente en la vista, rompiendo el patrón ya establecido para
indicadores/auxiliares (``models/crud_indicadores.py``,
``models/crud_auxiliares.py``).

Fuera de alcance de esta extracción (permanecen en ``security/auth.py``
porque ya viven correctamente fuera de la vista):
- Creación de usuario (``registrar_usuario``).
- Cambio/reseteo de contraseña, TOTP/2FA, códigos de respaldo.

Reglas de negocio
------------------
- Solo el rol 'administrador' puede invocar las funciones de escritura de
  este módulo. La primera línea de defensa sigue siendo ``@require_role``
  en la vista (``views/admin_usuarios.py``); cada función de escritura
  sensible aquí vuelve a verificar el rol recibido explícitamente como
  parámetro ``rol_actor`` (Hallazgo D del informe de arquitectura, agosto
  2026 — defensa en profundidad, no reemplazo del decorador). Se usa un
  parámetro explícito en vez de leer ``st.session_state`` directamente
  para mantener este módulo desacoplado de Streamlit, igual que
  ``models/crud_indicadores.py`` (ver ``security/autorizacion.py``).
- El registro de auditoría (``registrar_log_standalone``) de cada acción
  permanece en la vista, no aquí: es el mismo patrón no-atómico que ya
  existía antes de esta extracción (comportamiento observable preservado
  a propósito, ver Hallazgo A del informe — el informe no encontró
  inconsistencia de datos con este patrón, así que no se cambia de paso).
"""

import logging
import sqlite3

from data import database as db_mod
from security.autorizacion import verificar_rol

logger = logging.getLogger(__name__)

_ROLES_ADMIN = ["administrador"]


def _mensaje_error_bd(exc: sqlite3.Error) -> str:
    """Mensaje para el usuario final ante un error esperable de la base de
    datos (violación de constraint, fila bloqueada, etc.)."""
    return (
        "No se pudo completar la operación porque los datos violan una "
        "restricción de la base de datos (por ejemplo, un valor duplicado "
        "o una referencia inválida). Revisa los campos relacionados. Si el "
        f"problema persiste, contacta al administrador con este detalle: {exc}"
    )


def _mensaje_error_inesperado(exc: Exception) -> str:
    """Mensaje para el usuario final ante una excepción que NO es un error
    esperable de la base de datos, es decir, un probable bug de
    programación. No expone ``str(exc)`` crudo al usuario de negocio."""
    return (
        "Ocurrió un error inesperado al procesar la operación. El equipo "
        "técnico ya cuenta con el detalle en los registros del sistema; si "
        "el problema persiste, contacta al administrador."
    )


# ---------------------------------------------------------------------------
# Lecturas
# ---------------------------------------------------------------------------

def listar_usuarios() -> list[dict]:
    """Devuelve todos los usuarios registrados, ordenados por username.

    Cada elemento incluye: id, username, rol, activo, fecha_creacion,
    requiere_2fa, totp_habilitado, eliminaciones_recientes.
    """
    columnas = [
        "id", "username", "rol", "activo", "fecha_creacion", "requiere_2fa",
        "totp_habilitado", "eliminaciones_recientes",
    ]
    conn = db_mod.obtener_conexion()
    try:
        filas = conn.execute(
            f"SELECT {', '.join(columnas)} FROM usuarios ORDER BY username"
        ).fetchall()
        return [dict(zip(columnas, fila)) for fila in filas]
    finally:
        conn.close()


def obtener_totp_habilitado(usuario_id: int) -> bool:
    """Devuelve si el usuario dado tiene 2FA (TOTP) habilitado."""
    conn = db_mod.obtener_conexion()
    try:
        fila = conn.execute(
            "SELECT totp_habilitado FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        return bool(fila[0]) if fila else False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Escrituras
# ---------------------------------------------------------------------------

def eliminar_usuario(usuario_id: int, rol_actor: str | None) -> tuple[bool, str]:
    """Elimina permanentemente un usuario. Defensa en profundidad: además
    del ``@require_role`` en la vista que llama a esta función, se vuelve a
    verificar el rol aquí (Hallazgo D del informe de arquitectura, agosto
    2026)."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        conn.commit()
        return True, "Usuario eliminado permanentemente."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al eliminar usuario id=%d: %s.", usuario_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al eliminar usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def cambiar_rol_usuario(
    usuario_id: int, nuevo_rol: str, rol_actor: str | None
) -> tuple[bool, str]:
    """Cambia el rol de un usuario. Defensa en profundidad (Hallazgo D)."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute(
            "UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, usuario_id)
        )
        conn.commit()
        return True, "Rol actualizado correctamente."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al cambiar rol de usuario id=%d: %s.", usuario_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al cambiar rol de usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def desactivar_usuario(usuario_id: int, rol_actor: str | None) -> tuple[bool, str]:
    """Desactiva (soft-disable) un usuario. Defensa en profundidad (Hallazgo D)."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario_id,))
        conn.commit()
        return True, "Usuario desactivado."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al desactivar usuario id=%d: %s.", usuario_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al desactivar usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def activar_usuario(usuario_id: int, rol_actor: str | None) -> tuple[bool, str]:
    """Reactiva un usuario previamente desactivado. Defensa en profundidad
    (Hallazgo D)."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute("UPDATE usuarios SET activo = 1 WHERE id = ?", (usuario_id,))
        conn.commit()
        return True, "Usuario activado."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al activar usuario id=%d: %s.", usuario_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al activar usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def quitar_exigencia_2fa(usuario_id: int, rol_actor: str | None) -> tuple[bool, str]:
    """Quita la marca 'requiere_2fa' de un usuario. Defensa en profundidad
    (Hallazgo D). No desactiva el TOTP ya configurado — eso lo maneja el
    llamador vía ``security.auth.desactivar_totp`` cuando corresponda, tal
    como hacía la vista antes de esta extracción."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute(
            "UPDATE usuarios SET requiere_2fa = 0 WHERE id = ?", (usuario_id,)
        )
        conn.commit()
        return True, "Exigencia de 2FA retirada."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning(
            "Error de BD al quitar exigencia de 2FA de usuario id=%d: %s.", usuario_id, exc
        )
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al quitar exigencia de 2FA de usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def exigir_2fa(usuario_id: int, rol_actor: str | None) -> tuple[bool, str]:
    """Marca a un usuario con 'requiere_2fa'. Defensa en profundidad (Hallazgo D)."""
    verificar_rol(rol_actor, _ROLES_ADMIN)
    conn = db_mod.obtener_conexion()
    try:
        conn.execute(
            "UPDATE usuarios SET requiere_2fa = 1 WHERE id = ?", (usuario_id,)
        )
        conn.commit()
        return True, "2FA exigido. El usuario deberá configurarlo en su próximo login."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al exigir 2FA a usuario id=%d: %s.", usuario_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al exigir 2FA a usuario id=%d.", usuario_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()
