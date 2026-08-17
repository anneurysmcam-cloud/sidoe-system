"""
security/auth.py
================
Autenticación de usuarios y control de acceso basado en roles (RBAC).

- ``validar_credenciales``: verifica usuario/contraseña, aplica protección
  anti-fuerza-bruta y migra hashes SHA-256 legados a bcrypt en el primer login.
- ``require_role``: decorador de Streamlit para bloquear vistas a roles no
  autorizados.
- ``registrar_usuario``: crea un nuevo usuario validando la política de
  contraseñas y el formato del username.
- ``resetear_password_admin``: restablece la contraseña de otro usuario sin
  requerir la anterior (uso exclusivo de administradores, con auditoría).
- ``cambiar_password``: actualiza la contraseña de un usuario existente con
  validación de política.
- ``logout``: limpia el estado de sesión de Streamlit.
- ``verificar_segundo_factor``, ``iniciar_enrolamiento_totp``,
  ``confirmar_activacion_totp``, ``desactivar_totp``: 2FA (TOTP) opcional,
  persistencia sobre las columnas totp_* de usuarios (ver security/totp.py
  para las primitivas criptográficas).
- ``generar_y_guardar_codigos_respaldo``, ``verificar_codigo_respaldo``,
  ``contar_codigos_respaldo_restantes``: códigos de respaldo (recovery
  codes) de un solo uso para cuando el usuario pierde acceso a su app
  autenticadora — persistencia sobre la tabla ``totp_codigos_respaldo``.
"""

import hashlib
import logging
from functools import wraps

import bcrypt
import streamlit as st

from data import database as db_mod

logger = logging.getLogger(__name__)

# Roles válidos reconocidos por el sistema
ROLES_VALIDOS = frozenset({"editor", "supervisor", "administrador"})


# ---------------------------------------------------------------------------
# Hashing de contraseñas
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Genera un hash bcrypt (con salt aleatorio) de la contraseña."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _es_hash_sha256_legado(password_hash: str) -> bool:
    """Detecta hashes SHA-256 sin sal de la implementación anterior (64 hex chars)."""
    return (
        len(password_hash) == 64
        and all(c in "0123456789abcdef" for c in password_hash.lower())
    )


def _verificar_password(password: str, password_hash: str) -> bool:
    """Verifica la contraseña contra un hash bcrypt actual o SHA-256 legado."""
    if _es_hash_sha256_legado(password_hash):
        return (
            hashlib.sha256(password.encode("utf-8")).hexdigest() == password_hash
        )
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        logger.warning("Hash de contraseña con formato inesperado/corrupto encontrado.")
        return False


# ---------------------------------------------------------------------------
# Validación de credenciales — con protección anti-fuerza-bruta
# ---------------------------------------------------------------------------

def validar_credenciales(username: str, password: str) -> dict | None:
    """Valida usuario/contraseña y devuelve un dict del usuario si es correcto.

    Pasos internos:
    1. Verifica bloqueo por intentos fallidos (anti-fuerza-bruta).
    2. Saneamiento básico del username.
    3. Consulta la BD (siempre parameterizada).
    4. Verificación de contraseña.
    5. Migración perezosa SHA-256 → bcrypt si aplica.
    6. En caso de fallo: registra intento + audita en BD.
    7. En caso de éxito: limpia el historial de intentos fallidos.

    Args:
        username: Nombre de usuario tal como se ingresó en el formulario.
        password: Contraseña en texto plano.

    Returns:
        ``{'id': int, 'username': str, 'rol': str, 'totp_habilitado': bool,
        'requiere_2fa': bool}`` si las credenciales son válidas y el
        usuario está activo; ``None`` en cualquier otro caso. Si
        ``totp_habilitado`` es True, el llamador (app.py) NO debe
        completar el login todavía — debe pedir el segundo factor y
        confirmarlo con ``verificar_segundo_factor()`` antes de establecer
        la sesión. Si ``requiere_2fa`` es True pero ``totp_habilitado`` es
        False, el administrador exigió 2FA a este usuario y este todavía
        no lo configuró: el llamador debe forzar el enrolamiento TOTP
        (``iniciar_enrolamiento_totp`` / ``confirmar_activacion_totp``)
        antes de establecer la sesión, en vez de dejarlo entrar sin 2FA.
    """
    from security.hardening import (
        auditar_login_fallido,
        limpiar_intentos_exitosos,
        registrar_intento_fallido,
        sanitizar_username,
        verificar_bloqueo,
    )

    if not username or not password:
        return None

    # 1 · Verificar bloqueo activo
    bloqueado, segundos = verificar_bloqueo(username)
    if bloqueado:
        minutos = (segundos + 59) // 60
        logger.warning(
            "SEGURIDAD: login bloqueado para '%s' — %d seg restantes.", username, segundos
        )
        # Lanzamos un ValueError especial para que app.py pueda mostrar el mensaje
        raise BloqueadoError(
            f"Cuenta bloqueada temporalmente por múltiples intentos fallidos. "
            f"Inténtalo de nuevo en {minutos} minuto(s)."
        )

    # 2 · Sanear username (solo chars permitidos)
    username_limpio = sanitizar_username(username)
    if not username_limpio:
        logger.warning("SEGURIDAD: username con caracteres inválidos rechazado.")
        return None

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, username, password_hash, rol, activo, totp_habilitado, "
            "requiere_2fa FROM usuarios WHERE username = ?",
            (username_limpio,),
        )
        row = cursor.fetchone()

        # 3a · Usuario no encontrado o inactivo — tiempo constante para no revelar existencia
        if not row or row[4] != 1:
            # Ejecutar una verificación bcrypt ficticia para equiparar tiempo de respuesta
            bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt()))
            logger.info("Login fallido — usuario no encontrado o inactivo: '%s'.", username_limpio)
            registrar_intento_fallido(username_limpio)
            auditar_login_fallido(username_limpio, "Usuario no encontrado o inactivo")
            return None

        usuario_id, uname, password_hash, rol, _activo, totp_habilitado, requiere_2fa = row

        # 3b · Verificar contraseña
        if not _verificar_password(password, password_hash):
            logger.warning("Contraseña incorrecta para usuario '%s'.", uname)
            registrar_intento_fallido(uname)
            auditar_login_fallido(uname, "Contraseña incorrecta")
            return None

        # 4 · Migración perezosa SHA-256 → bcrypt
        if _es_hash_sha256_legado(password_hash):
            nuevo_hash = hash_password(password)
            cursor.execute(
                "UPDATE usuarios SET password_hash = ? WHERE id = ?",
                (nuevo_hash, usuario_id),
            )
            conn.commit()
            logger.info("Hash de contraseña migrado a bcrypt para usuario '%s'.", uname)

        # 5 · Validar rol
        rol_normalizado = (rol or "").strip().lower()
        if rol_normalizado not in ROLES_VALIDOS:
            logger.error(
                "Usuario '%s' tiene rol inválido: '%s'.", uname, rol_normalizado
            )
            return None

        # 6 · Login exitoso — limpiar historial de intentos
        limpiar_intentos_exitosos(uname)
        logger.info("Login exitoso: usuario='%s', rol='%s'.", uname, rol_normalizado)
        return {
            "id": usuario_id,
            "username": uname,
            "rol": rol_normalizado,
            "totp_habilitado": bool(totp_habilitado),
            "requiere_2fa": bool(requiere_2fa),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Excepción especial para bloqueo de cuenta
# ---------------------------------------------------------------------------

class BloqueadoError(Exception):
    """Se lanza cuando un usuario intenta login estando bloqueado."""


# ---------------------------------------------------------------------------
# Gestión de usuarios
# ---------------------------------------------------------------------------

def registrar_usuario(username: str, password: str, rol: str) -> None:
    """Crea un nuevo usuario en la tabla usuarios con hash bcrypt.

    Valida política de contraseñas y formato de username antes de persistir.

    Args:
        username: Nombre de usuario único.
        password: Contraseña en texto plano (se almacena hasheada).
        rol: Rol asignado; debe ser uno de ROLES_VALIDOS.

    Raises:
        ValueError: Si el rol, username o contraseña no son válidos.
        sqlite3.IntegrityError: Si el username ya existe.
    """
    from security.hardening import sanitizar_username, validar_politica_password

    if not username or not username.strip():
        raise ValueError("El nombre de usuario no puede estar vacío.")

    username_limpio = sanitizar_username(username.strip())
    if not username_limpio:
        raise ValueError(
            "El nombre de usuario solo puede contener letras, números, "
            "puntos, guiones y guiones bajos."
        )

    if not password:
        raise ValueError("La contraseña no puede estar vacía.")

    # Validar política de contraseñas
    errores_pass = validar_politica_password(password)
    if errores_pass:
        raise ValueError("La contraseña no cumple la política de seguridad:\n" +
                         "\n".join(f"• {e}" for e in errores_pass))

    rol_norm = (rol or "").strip().lower()
    if rol_norm not in ROLES_VALIDOS:
        raise ValueError(f"Rol inválido: '{rol}'. Válidos: {sorted(ROLES_VALIDOS)}.")

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO usuarios (username, password_hash, rol, activo, fecha_creacion)
            VALUES (?, ?, ?, 1, datetime('now'))
            """,
            (username_limpio, hash_password(password), rol_norm),
        )
        conn.commit()
        logger.info("Usuario '%s' creado con rol '%s'.", username_limpio, rol_norm)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cambiar_password(usuario_id: int, password_actual: str, password_nuevo: str) -> None:
    """Cambia la contraseña de un usuario verificando la contraseña actual.

    Args:
        usuario_id:      ID del usuario que cambia su contraseña.
        password_actual: Contraseña actual en texto plano (para confirmación).
        password_nuevo:  Nueva contraseña en texto plano.

    Raises:
        ValueError: Si la contraseña actual es incorrecta o la nueva no cumple política.
        LookupError: Si el usuario_id no existe.
    """
    from security.hardening import validar_politica_password

    errores_pass = validar_politica_password(password_nuevo)
    if errores_pass:
        raise ValueError("La nueva contraseña no cumple la política de seguridad:\n" +
                         "\n".join(f"• {e}" for e in errores_pass))

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT password_hash FROM usuarios WHERE id = ?", (usuario_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise LookupError(f"Usuario con id={usuario_id} no encontrado.")

        if not _verificar_password(password_actual, row[0]):
            raise ValueError("La contraseña actual es incorrecta.")

        cursor.execute(
            "UPDATE usuarios SET password_hash = ? WHERE id = ?",
            (hash_password(password_nuevo), usuario_id),
        )
        conn.commit()
        logger.info("Contraseña actualizada para usuario id=%d.", usuario_id)
    except Exception:
        conn.rollback()
        raise


def verificar_password_propia(usuario_id: int, password: str) -> bool:
    """Verifica la contraseña de ``usuario_id`` sin modificar nada.

    Pensada para pasos de re-autenticación (p. ej. un administrador
    confirmando su propia contraseña antes de resetear la de otro usuario).
    A diferencia de ``cambiar_password``, no escribe en la base de datos.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM usuarios WHERE id = ?", (usuario_id,))
    row = cursor.fetchone()
    if not row:
        return False
    return _verificar_password(password, row[0])


def resetear_password_admin(usuario_objetivo_id: int, password_nuevo: str) -> None:
    """Restablece la contraseña de otro usuario sin conocer la anterior.

    Pensado para que un administrador resetee la contraseña de un usuario que
    la olvidó. A diferencia de ``cambiar_password``, esta función NO exige la
    contraseña actual del usuario objetivo (el administrador nunca debería
    tener que conocerla). El control de acceso se apoya en dos capas fuera de
    esta función: el decorador ``@require_role("administrador")`` de la vista
    que la invoca, y el registro de auditoría (acción ``RESET_PASSWORD_ADMIN``)
    que la vista debe registrar indicando qué administrador ejecutó el reseteo
    y sobre qué usuario, para trazabilidad completa.

    Args:
        usuario_objetivo_id: ID del usuario cuya contraseña se restablece.
        password_nuevo:       Nueva contraseña en texto plano.

    Raises:
        ValueError: Si la nueva contraseña no cumple la política de seguridad.
        LookupError: Si el usuario_objetivo_id no existe.
    """
    from security.hardening import validar_politica_password

    errores_pass = validar_politica_password(password_nuevo)
    if errores_pass:
        raise ValueError("La nueva contraseña no cumple la política de seguridad:\n" +
                         "\n".join(f"• {e}" for e in errores_pass))

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM usuarios WHERE id = ?", (usuario_objetivo_id,)
        )
        if not cursor.fetchone():
            raise LookupError(f"Usuario con id={usuario_objetivo_id} no encontrado.")

        cursor.execute(
            "UPDATE usuarios SET password_hash = ? WHERE id = ?",
            (hash_password(password_nuevo), usuario_objetivo_id),
        )
        conn.commit()
        logger.info(
            "Contraseña reseteada por administrador para usuario id=%d.",
            usuario_objetivo_id,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def logout(session_state) -> None:
    """Cierra sesión limpiando el estado de sesión de Streamlit."""
    session_state.clear()
    logger.info("Sesión cerrada.")


# ---------------------------------------------------------------------------
# Segundo factor de autenticación (2FA / TOTP)
# ---------------------------------------------------------------------------
# La verificación criptográfica del código vive en security/totp.py; aquí
# solo se maneja la persistencia (leer/guardar el secreto y el flag
# totp_habilitado), igual que el resto de este módulo maneja la BD.

def verificar_segundo_factor(usuario_id: int, codigo: str) -> bool:
    """Verifica un código TOTP contra el secreto guardado del usuario.

    Args:
        usuario_id: Id del usuario que ya pasó la verificación de contraseña.
        codigo:     Código de 6 dígitos ingresado.

    Returns:
        True si el código es válido. False si el usuario no tiene 2FA
        activo, no tiene secreto guardado, o el código es incorrecto.
    """
    from security.totp import verificar_codigo

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT totp_secret, totp_habilitado FROM usuarios WHERE id = ?",
            (usuario_id,),
        )
        row = cursor.fetchone()
        if not row or not row[1] or not row[0]:
            return False
        secreto, _habilitado = row
        valido = verificar_codigo(secreto, codigo)
        if not valido:
            logger.warning("SEGURIDAD: código 2FA inválido para usuario_id=%d.", usuario_id)
        return valido
    finally:
        conn.close()


def iniciar_enrolamiento_totp(usuario_id: int) -> tuple[str, str]:
    """Genera un secreto TOTP nuevo (sin activarlo todavía) y devuelve el
    secreto junto con la URI de aprovisionamiento para el QR.

    El secreto se guarda en BD de inmediato en un estado "pendiente"
    (totp_habilitado sigue en 0) para que el flujo de confirmación
    (``confirmar_activacion_totp``) pueda verificarlo contra el primer
    código que el usuario ingrese desde su app autenticadora, sin tener
    que mantener el secreto solo en session_state de Streamlit (que se
    pierde si el usuario recarga la página a mitad del enrolamiento).

    Args:
        usuario_id: Id del usuario que está activando 2FA.

    Returns:
        Tupla ``(secreto, uri_provisioning)``.
    """
    from security.totp import generar_secreto, generar_uri_provisioning

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username FROM usuarios WHERE id = ?", (usuario_id,))
        row = cursor.fetchone()
        if not row:
            raise LookupError(f"Usuario con id={usuario_id} no encontrado.")
        username = row[0]

        secreto = generar_secreto()
        cursor.execute(
            "UPDATE usuarios SET totp_secret = ?, totp_habilitado = 0 WHERE id = ?",
            (secreto, usuario_id),
        )
        conn.commit()
        logger.info("Enrolamiento TOTP iniciado (pendiente de confirmar) para id=%d.", usuario_id)
        return secreto, generar_uri_provisioning(username, secreto)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirmar_activacion_totp(usuario_id: int, codigo: str) -> None:
    """Confirma el enrolamiento de 2FA verificando el primer código generado
    por la app autenticadora, y solo entonces marca ``totp_habilitado = 1``.

    Exigir esta confirmación (en vez de activar 2FA apenas se muestra el QR)
    evita que un usuario quede bloqueado fuera de su propia cuenta por un
    QR mal escaneado o una app autenticadora mal configurada.

    Args:
        usuario_id: Id del usuario.
        codigo:     Código de 6 dígitos generado por la app autenticadora.

    Raises:
        LookupError: Si el usuario no existe o no tiene un enrolamiento
                     pendiente (llamar primero a ``iniciar_enrolamiento_totp``).
        ValueError:  Si el código no es válido.
    """
    from security.totp import verificar_codigo

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT totp_secret FROM usuarios WHERE id = ?", (usuario_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            raise LookupError(
                f"Usuario id={usuario_id} no tiene un enrolamiento TOTP pendiente."
            )
        secreto = row[0]
        if not verificar_codigo(secreto, codigo):
            raise ValueError("Código inválido. Verifica la hora de tu dispositivo e intenta de nuevo.")

        cursor.execute(
            "UPDATE usuarios SET totp_habilitado = 1 WHERE id = ?", (usuario_id,)
        )
        conn.commit()
        logger.info("2FA activado correctamente para usuario_id=%d.", usuario_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def desactivar_totp(usuario_id: int) -> None:
    """Desactiva 2FA para un usuario y borra el secreto guardado.

    No exige contraseña/código aquí porque el llamador (views/admin_usuarios.py)
    ya opera detrás de ``require_role(["administrador"])`` — es una acción de
    autoservicio sobre la propia cuenta o de administración, igual que el
    resto de las operaciones de esa vista.

    Args:
        usuario_id: Id del usuario al que desactivar 2FA.

    Raises:
        LookupError: Si el usuario no existe.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM usuarios WHERE id = ?", (usuario_id,))
        if not cursor.fetchone():
            raise LookupError(f"Usuario con id={usuario_id} no encontrado.")

        cursor.execute(
            "UPDATE usuarios SET totp_habilitado = 0, totp_secret = NULL WHERE id = ?",
            (usuario_id,),
        )
        # Los códigos de respaldo solo tienen sentido junto con 2FA activo;
        # se eliminan aquí para que una futura reactivación empiece con un
        # lote nuevo en vez de arrastrar códigos de un enrolamiento previo.
        cursor.execute(
            "DELETE FROM totp_codigos_respaldo WHERE usuario_id = ?", (usuario_id,)
        )
        conn.commit()
        logger.info("2FA desactivado para usuario_id=%d (códigos de respaldo eliminados).", usuario_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Códigos de respaldo (recovery codes) — respaldo del 2FA
# ---------------------------------------------------------------------------
# Complementan al TOTP: permiten completar el segundo factor sin el
# teléfono cuando el usuario lo pierde o no tiene acceso a él. La primitiva
# de generación vive en security/totp.py; aquí se maneja el hasheo (mismo
# esquema bcrypt que las contraseñas), la persistencia y la invalidación
# de un solo uso.

def generar_y_guardar_codigos_respaldo(usuario_id: int) -> list[str]:
    """Genera un lote nuevo de códigos de respaldo, invalida cualquier lote
    anterior del usuario y guarda los hashes en BD.

    Devuelve los códigos EN TEXTO PLANO — es la única vez que existen fuera
    de donde el usuario los guarde. El llamador debe mostrarlos una sola
    vez (con opción de descargar/copiar) y no debe conservarlos en
    session_state más tiempo del necesario para renderizarlos.

    Args:
        usuario_id: Id del usuario. Debe tener 2FA activo o en proceso de
            activación (no se valida aquí; el llamador ya opera detrás de
            la vista de autoservicio de 2FA).

    Returns:
        Lista de códigos en texto plano, formato ``AAAA-AAAA``.
    """
    from security.totp import generar_codigos_respaldo

    codigos = generar_codigos_respaldo()
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM totp_codigos_respaldo WHERE usuario_id = ?", (usuario_id,)
        )
        cursor.executemany(
            "INSERT INTO totp_codigos_respaldo (usuario_id, codigo_hash) VALUES (?, ?)",
            [(usuario_id, hash_password(codigo)) for codigo in codigos],
        )
        conn.commit()
        logger.info(
            "Códigos de respaldo regenerados (%d) para usuario_id=%d.",
            len(codigos), usuario_id,
        )
        return codigos
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verificar_codigo_respaldo(usuario_id: int, codigo: str) -> bool:
    """Verifica un código de respaldo y, si es válido, lo marca como usado.

    Un código de respaldo válido solo puede usarse una vez: se compara
    contra todos los hashes sin usar del usuario (no hay forma de indexar
    directamente por el código en texto plano, igual que con contraseñas),
    y el primero que haga match se marca ``usado = 1`` de inmediato dentro
    de la misma conexión.

    Args:
        usuario_id: Id del usuario que ya pasó la verificación de contraseña.
        codigo:     Código ingresado por el usuario (formato ``AAAA-AAAA``).

    Returns:
        True si el código era válido y no había sido usado antes.
    """
    if not usuario_id or not codigo or not codigo.strip():
        return False
    codigo_limpio = codigo.strip().upper()

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, codigo_hash FROM totp_codigos_respaldo "
            "WHERE usuario_id = ? AND usado = 0",
            (usuario_id,),
        )
        for codigo_id, codigo_hash in cursor.fetchall():
            if _verificar_password(codigo_limpio, codigo_hash):
                cursor.execute(
                    "UPDATE totp_codigos_respaldo SET usado = 1, "
                    "usado_en = datetime('now') WHERE id = ?",
                    (codigo_id,),
                )
                conn.commit()
                logger.warning(
                    "SEGURIDAD: código de respaldo usado para usuario_id=%d "
                    "(codigo_id=%d).", usuario_id, codigo_id,
                )
                return True
        logger.warning(
            "SEGURIDAD: intento de código de respaldo inválido para usuario_id=%d.",
            usuario_id,
        )
        return False
    finally:
        conn.close()


def contar_codigos_respaldo_restantes(usuario_id: int) -> int:
    """Cuenta cuántos códigos de respaldo sin usar le quedan al usuario."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM totp_codigos_respaldo "
            "WHERE usuario_id = ? AND usado = 0",
            (usuario_id,),
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Control de acceso basado en roles
# ---------------------------------------------------------------------------

def require_role(roles_permitidos: list[str]):
    """Decorador de Streamlit que bloquea la ejecución si el usuario no tiene
    uno de los roles indicados.

    Uso::

        @require_role(["editor", "supervisor"])
        def mostrar_crear_indicador():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            usuario = st.session_state.get("usuario")
            if not usuario or usuario.get("rol") not in roles_permitidos:
                st.error(
                    "🚫 Acceso denegado. No tienes permisos para esta sección. "
                    f"Se requiere uno de estos roles: {', '.join(roles_permitidos)}."
                )
                st.stop()
            return func(*args, **kwargs)
        return wrapper
    return decorator
