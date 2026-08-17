"""
security/hardening.py
=====================
Capas de seguridad complementarias para SIDOE — ONE RD.

Módulos incluidos
-----------------
1. **Protección anti-fuerza-bruta** — bloqueo temporal de IP/usuario tras N
   intentos fallidos consecutivos de login, usando estado en memoria del
   proceso Streamlit (``st.session_state`` + dict global por proceso).
2. **Política de contraseñas** — valida longitud mínima, mayúscula, dígito y
   carácter especial antes de permitir la creación o cambio de contraseña.
3. **Tiempo de espera de sesión** — cierra automáticamente la sesión tras un
   período de inactividad configurable, sin requerir middleware externo.
4. **Validación y saneamiento de entradas** — limpia y acota texto libre antes
   de que llegue a la capa de datos.
5. **Seguridad de archivos de la BD** — verifica y corrige permisos del
   archivo SQLite al arrancar, evitando lectura por usuarios no autorizados
   del sistema operativo.
6. **Auditoría de intentos de login fallidos** — persiste cada intento fallido
   en la tabla ``auditoria`` para trazabilidad administrativa.

Notas de diseño
---------------
- El bloqueo por intentos fallidos es *por proceso*: si Streamlit se reinicia
  los contadores se limpian.  Esto es adecuado para el despliegue monoproceso
  de ONE.  Para multi-proceso se necesitaría un backend externo (Redis, etc.).
- No se implementa cifrado de BD en este módulo porque requiere compilar
  SQLCipher; esa responsabilidad queda en la infraestructura de ONE.
- No se tratan XSS/CSRF de forma explícita porque Streamlit ya mitiga ambos:
  los datos de usuario nunca se inyectan como HTML crudo y la comunicación
  usa WebSockets con token propio de Streamlit.
"""

import logging
import os
import re
import stat
import time
from datetime import datetime

import streamlit as st

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración centralizada
# ---------------------------------------------------------------------------

# — Fuerza bruta —
_MAX_INTENTOS_LOGIN: int = 5          # intentos fallidos antes de bloqueo
_VENTANA_INTENTOS_SEG: int = 300      # ventana deslizante (5 minutos)
_DURACION_BLOQUEO_SEG: int = 900      # bloqueo de 15 minutos tras exceder

# — Sesión —
_TIMEOUT_INACTIVIDAD_SEG: int = 3600  # 60 minutos sin interacción → logout

# — Contraseña —
_PASS_MIN_LEN: int = 8
_PASS_REQUIRE_UPPER: bool = True
_PASS_REQUIRE_DIGIT: bool = True
_PASS_REQUIRE_SPECIAL: bool = True
_PASS_SPECIAL_CHARS: str = r"!@#$%^&*()_\-+=\[\]{};':\"\\|,.<>/?`~"

# — Entradas de texto —
_MAX_LEN_CAMPO_CORTO: int = 255       # nombres, etiquetas
_MAX_LEN_CAMPO_LARGO: int = 2000      # comentarios, descripciones libres
_MAX_LEN_URL: int = 512

# ---------------------------------------------------------------------------
# 1 · Protección anti-fuerza-bruta
# ---------------------------------------------------------------------------

# Diccionario global del proceso: username → lista de timestamps de fallos
_intentos_fallidos: dict[str, list[float]] = {}
# Diccionario global del proceso: username → timestamp de fin del bloqueo
_bloqueados: dict[str, float] = {}


def registrar_intento_fallido(username: str) -> None:
    """Registra un intento de login fallido y bloquea si supera el umbral.

    Args:
        username: Nombre de usuario con el que se intentó el login.
    """
    ahora = time.time()
    historial = _intentos_fallidos.get(username, [])

    # Mantener solo intentos dentro de la ventana deslizante
    historial = [t for t in historial if ahora - t < _VENTANA_INTENTOS_SEG]
    historial.append(ahora)
    _intentos_fallidos[username] = historial

    if len(historial) >= _MAX_INTENTOS_LOGIN:
        _bloqueados[username] = ahora + _DURACION_BLOQUEO_SEG
        logger.warning(
            "SEGURIDAD: usuario '%s' bloqueado por %d seg tras %d intentos fallidos.",
            username, _DURACION_BLOQUEO_SEG, len(historial),
        )
        _intentos_fallidos[username] = []  # resetear para el próximo ciclo


def verificar_bloqueo(username: str) -> tuple[bool, int]:
    """Comprueba si el usuario está en período de bloqueo.

    Returns:
        (bloqueado: bool, segundos_restantes: int)
    """
    fin_bloqueo = _bloqueados.get(username, 0)
    ahora = time.time()
    if fin_bloqueo > ahora:
        return True, int(fin_bloqueo - ahora)
    # Bloqueo expirado: limpiar
    if username in _bloqueados:
        del _bloqueados[username]
    return False, 0


def limpiar_intentos_exitosos(username: str) -> None:
    """Limpia el historial de intentos fallidos tras un login exitoso."""
    _intentos_fallidos.pop(username, None)
    _bloqueados.pop(username, None)


def intentos_restantes(username: str) -> int:
    """Devuelve cuántos intentos quedan antes de bloqueo (informativo)."""
    ahora = time.time()
    historial = _intentos_fallidos.get(username, [])
    recientes = [t for t in historial if ahora - t < _VENTANA_INTENTOS_SEG]
    return max(0, _MAX_INTENTOS_LOGIN - len(recientes))


# ---------------------------------------------------------------------------
# 2 · Política de contraseñas
# ---------------------------------------------------------------------------

def validar_politica_password(password: str) -> list[str]:
    """Valida que la contraseña cumpla la política de SIDOE.

    Args:
        password: Contraseña en texto plano a validar.

    Returns:
        Lista de mensajes de error (vacía si la contraseña es válida).
    """
    errores: list[str] = []

    if len(password) < _PASS_MIN_LEN:
        errores.append(f"Debe tener al menos {_PASS_MIN_LEN} caracteres.")

    if _PASS_REQUIRE_UPPER and not re.search(r"[A-Z]", password):
        errores.append("Debe contener al menos una letra mayúscula.")

    if _PASS_REQUIRE_DIGIT and not re.search(r"\d", password):
        errores.append("Debe contener al menos un número.")

    if _PASS_REQUIRE_SPECIAL and not re.search(
        rf"[{_PASS_SPECIAL_CHARS}]", password
    ):
        errores.append(
            "Debe contener al menos un carácter especial "
            f"({_PASS_SPECIAL_CHARS.replace(chr(92), '')})."
        )

    return errores


def password_es_valida(password: str) -> bool:
    """Conveniencia: devuelve True si la contraseña pasa la política."""
    return len(validar_politica_password(password)) == 0


# ---------------------------------------------------------------------------
# 3 · Tiempo de espera de sesión (inactividad)
# ---------------------------------------------------------------------------

_KEY_ULTIMO_ACCESO = "_sidoe_ultimo_acceso"


def registrar_actividad() -> None:
    """Actualiza el timestamp de última actividad en la sesión actual.

    Debe llamarse al inicio de cada renderizado de página (en app.py).
    """
    st.session_state[_KEY_ULTIMO_ACCESO] = time.time()


def verificar_timeout_sesion() -> bool:
    """Comprueba si la sesión ha superado el tiempo de inactividad.

    Returns:
        True si la sesión expiró (el llamador debe hacer logout + rerun).
    """
    ultimo = st.session_state.get(_KEY_ULTIMO_ACCESO)
    if ultimo is None:
        return False
    transcurrido = time.time() - ultimo
    if transcurrido > _TIMEOUT_INACTIVIDAD_SEG:
        logger.info(
            "Sesión expirada por inactividad (%.0f seg > %d seg).",
            transcurrido, _TIMEOUT_INACTIVIDAD_SEG,
        )
        return True
    return False


def minutos_restantes_sesion() -> int:
    """Devuelve minutos restantes antes del timeout de sesión."""
    ultimo = st.session_state.get(_KEY_ULTIMO_ACCESO)
    if ultimo is None:
        return _TIMEOUT_INACTIVIDAD_SEG // 60
    restantes = _TIMEOUT_INACTIVIDAD_SEG - (time.time() - ultimo)
    return max(0, int(restantes // 60))


# ---------------------------------------------------------------------------
# 4 · Validación y saneamiento de entradas
# ---------------------------------------------------------------------------

def sanitizar_texto(
    valor: str | None,
    max_len: int = _MAX_LEN_CAMPO_CORTO,
    campo: str = "campo",
) -> str:
    """Limpia y trunca una cadena de texto antes de persistirla.

    - Elimina caracteres de control (salvo espacios y saltos de línea).
    - Trunca si supera ``max_len``.
    - Aplica strip() para quitar espacios extremos.

    Args:
        valor:   Texto ingresado por el usuario (puede ser None).
        max_len: Longitud máxima permitida.
        campo:   Nombre del campo, solo para logging.

    Returns:
        Cadena saneada. Devuelve "" si el valor era None o vacío.
    """
    if not valor:
        return ""

    # Eliminar caracteres de control (\x00–\x08, \x0B, \x0C, \x0E–\x1F, \x7F)
    # Se preservan \x09 (tab), \x0A (LF), \x0D (CR) para campos de texto largo.
    limpio = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(valor))
    limpio = limpio.strip()

    if len(limpio) > max_len:
        logger.warning(
            "Campo '%s' truncado de %d a %d caracteres.", campo, len(limpio), max_len
        )
        limpio = limpio[:max_len]

    return limpio


def sanitizar_url(url: str | None) -> str:
    """Valida que la URL tenga esquema http/https y la trunca si es necesario.

    Args:
        url: URL ingresada por el usuario.

    Returns:
        URL saneada o cadena vacía si el valor no es una URL válida.
    """
    if not url:
        return ""
    url_limpia = sanitizar_texto(url.strip(), max_len=_MAX_LEN_URL, campo="url")
    if url_limpia and not re.match(r"^https?://", url_limpia, re.IGNORECASE):
        logger.warning("URL rechazada por esquema inválido: '%s'", url_limpia[:50])
        return ""
    return url_limpia


def sanitizar_username(username: str | None) -> str:
    """Permite solo letras, dígitos, puntos, guiones y guiones bajos en usernames.

    Args:
        username: Nombre de usuario ingresado.

    Returns:
        Username saneado o cadena vacía si el valor no es válido.
    """
    if not username:
        return ""
    limpio = username.strip()[:64]
    if not re.match(r"^[a-zA-Z0-9._\-]+$", limpio):
        logger.warning("Username con caracteres inválidos: '%s'", limpio)
        return ""
    return limpio


# ---------------------------------------------------------------------------
# 5 · Seguridad de archivos de la base de datos
# ---------------------------------------------------------------------------

def asegurar_permisos_db(db_path: str) -> None:
    """Verifica y corrige que el archivo SQLite solo sea legible por el owner.

    En sistemas Linux/macOS, establece permisos ``600`` (rw-------).
    En Windows, omite la operación con un aviso.

    Args:
        db_path: Ruta absoluta al archivo ``.db``.
    """
    if not os.path.exists(db_path):
        return  # el archivo aún no se ha creado; se creará al conectar

    if os.name == "nt":
        logger.info(
            "Verificación de permisos de BD omitida en Windows. "
            "Asegure manualmente que '%s' no sea accesible por otros usuarios.",
            db_path,
        )
        return

    try:
        permisos_actuales = stat.S_IMODE(os.stat(db_path).st_mode)
        permisos_deseados = stat.S_IRUSR | stat.S_IWUSR  # 0o600

        if permisos_actuales != permisos_deseados:
            os.chmod(db_path, permisos_deseados)
            logger.info(
                "Permisos de BD corregidos: '%s' → 600 (rw-------).", db_path
            )
        else:
            logger.debug("Permisos de BD correctos (600): '%s'.", db_path)
    except OSError as exc:
        logger.error(
            "No se pudieron ajustar los permisos de '%s': %s", db_path, exc
        )


# ---------------------------------------------------------------------------
# 6 · Auditoría de intentos fallidos en la tabla auditoria
# ---------------------------------------------------------------------------

def auditar_login_fallido(username: str, motivo: str = "Credenciales inválidas") -> None:
    """Persiste un intento de login fallido en la tabla ``auditoria`` de la BD.

    Usa ``usuario_id = NULL`` ya que el usuario no está autenticado; la
    columna es nullable (ver ``migrar_auditoria_usuario_id_nullable``) para
    permitir justamente este caso.

    Args:
        username: Nombre de usuario que intentó el login.
        motivo:   Descripción del motivo del fallo.
    """
    try:
        from data import database as db_mod
        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO auditoria (usuario_id, accion, detalle)
                VALUES (NULL, 'LOGIN_FALLIDO', ?)
                """,
                (f"username='{username}' | motivo={motivo} | "
                 f"hora={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.warning(
                "No se pudo auditar login fallido en BD para '%s'.", username, exc_info=True
            )
        finally:
            conn.close()
    except Exception:
        # Si la BD no está disponible, el log Python ya capturó el evento.
        pass
