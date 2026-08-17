"""
security/totp.py
=================
Segundo factor de autenticación (2FA) vía TOTP (RFC 6238) — opcional, pensado
inicialmente para el rol ``administrador`` (las cuentas más sensibles del
sistema: pueden crear/eliminar usuarios y ver auditoría completa).

Este módulo solo contiene las primitivas criptográficas (generar secreto,
construir la URI de aprovisionamiento para apps como Google Authenticator /
Authy, verificar un código). La persistencia (activar/desactivar 2FA para
un usuario, verificar contra el secreto guardado en BD) vive en
``security/auth.py`` junto al resto del flujo de autenticación, para que
``obtener_conexion()`` siga siendo el único punto de acceso a datos.

Por qué TOTP y no SMS/email:
- No depende de infraestructura de envío (SMS/email) que ONE no tiene
  desplegada para SIDOE.
- Estándar abierto, compatible con cualquier app autenticadora existente
  que el personal de ONE ya pueda tener instalada.
- El secreto nunca sale del servidor tras el enrolamiento inicial (a
  diferencia de un código por SMS, que viaja por una red que no controla
  la aplicación).
"""

import base64
import logging
import secrets

import pyotp

logger = logging.getLogger(__name__)

_EMISOR = "SIDOE - ONE"

# Alfabeto para códigos de respaldo: sin 0/O ni 1/I/L, para reducir errores
# de transcripción cuando el usuario los copia a mano desde donde los guardó.
_ALFABETO_CODIGO_RESPALDO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CANTIDAD_CODIGOS_RESPALDO = 10

# Ventana de tolerancia: acepta el código válido del intervalo actual y el
# anterior/siguiente (±30s) para absorber pequeños desfases de reloj entre
# el servidor y el teléfono del usuario, sin abrir una ventana de validez
# tan amplia que facilite ataques de repetición.
_VENTANA_VALIDEZ = 1


def generar_secreto() -> str:
    """Genera un secreto base32 aleatorio nuevo para un enrolamiento de TOTP."""
    return pyotp.random_base32()


def generar_uri_provisioning(username: str, secreto: str) -> str:
    """Construye la URI ``otpauth://`` que una app autenticadora puede leer
    (directamente, o codificada como QR) para enrolar la cuenta.

    Args:
        username: Nombre de usuario SIDOE (se muestra en la app autenticadora).
        secreto:  Secreto base32 generado por ``generar_secreto()``.

    Returns:
        URI ``otpauth://totp/...`` lista para codificar como QR.
    """
    return pyotp.totp.TOTP(secreto).provisioning_uri(name=username, issuer_name=_EMISOR)


def generar_codigos_respaldo(cantidad: int = CANTIDAD_CODIGOS_RESPALDO) -> list[str]:
    """Genera *cantidad* códigos de respaldo (recovery codes) de un solo uso,
    en texto plano, para el caso en que el usuario pierda acceso a su app
    autenticadora.

    Cada código sirve como sustituto puntual del código TOTP durante el
    login: el llamador (``security.auth``) es responsable de hashearlos
    antes de guardarlos y de invalidar cada uno tras su primer uso. Se usa
    ``secrets`` (CSPRNG), no ``random``, porque funcionan como credenciales
    de un solo uso — el mismo criterio que ``generar_secreto()``.

    Args:
        cantidad: Cuántos códigos generar (por defecto 10).

    Returns:
        Lista de códigos con formato ``AAAA-AAAA`` (8 caracteres
        alfanuméricos en dos grupos de 4).
    """
    return [_generar_un_codigo_respaldo() for _ in range(cantidad)]


def _generar_un_codigo_respaldo() -> str:
    crudo = "".join(secrets.choice(_ALFABETO_CODIGO_RESPALDO) for _ in range(8))
    return f"{crudo[:4]}-{crudo[4:]}"


def verificar_codigo(secreto: str, codigo: str) -> bool:
    """Verifica un código TOTP de 6 dígitos contra un secreto.

    Args:
        secreto: Secreto base32 almacenado para el usuario.
        codigo:  Código de 6 dígitos ingresado por el usuario.

    Returns:
        True si el código es válido dentro de la ventana de tolerancia.
    """
    if not secreto or not codigo:
        return False
    codigo_limpio = codigo.strip().replace(" ", "")
    if not codigo_limpio.isdigit() or len(codigo_limpio) != 6:
        return False
    try:
        return pyotp.totp.TOTP(secreto).verify(codigo_limpio, valid_window=_VENTANA_VALIDEZ)
    except (base64.binascii.Error, ValueError):
        logger.warning("Secreto TOTP con formato inválido al verificar código.")
        return False
