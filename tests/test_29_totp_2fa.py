"""
tests/test_29_totp_2fa.py
==========================
Cobertura del segundo factor de autenticación (2FA / TOTP):

- security/totp.py: primitivas puras (generar_secreto, generar_uri_provisioning,
  verificar_codigo) — incluye validación de formato de código y ventana de
  tolerancia.
- security/auth.py: persistencia (iniciar_enrolamiento_totp,
  confirmar_activacion_totp, desactivar_totp, verificar_segundo_factor) y su
  integración con validar_credenciales (totp_habilitado en el dict de retorno).
"""

import pyotp
import pytest

from security.auth import (
    confirmar_activacion_totp,
    desactivar_totp,
    iniciar_enrolamiento_totp,
    registrar_usuario,
    validar_credenciales,
    verificar_segundo_factor,
)
from security.totp import generar_secreto, generar_uri_provisioning, verificar_codigo


# ---------------------------------------------------------------------------
# security/totp.py — primitivas puras
# ---------------------------------------------------------------------------

def test_generar_secreto_produce_base32_distinto_cada_vez():
    s1 = generar_secreto()
    s2 = generar_secreto()
    assert s1 != s2
    assert len(s1) >= 16


def test_generar_uri_provisioning_incluye_usuario_y_emisor():
    secreto = generar_secreto()
    uri = generar_uri_provisioning("randy", secreto)
    assert uri.startswith("otpauth://totp/")
    assert "randy" in uri
    assert "SIDOE" in uri


def test_verificar_codigo_correcto_es_valido():
    secreto = generar_secreto()
    codigo_actual = pyotp.TOTP(secreto).now()
    assert verificar_codigo(secreto, codigo_actual) is True


def test_verificar_codigo_incorrecto_es_invalido():
    secreto = generar_secreto()
    assert verificar_codigo(secreto, "000000") is False


def test_verificar_codigo_con_formato_invalido_no_lanza_excepcion():
    secreto = generar_secreto()
    assert verificar_codigo(secreto, "abc") is False
    assert verificar_codigo(secreto, "12345") is False
    assert verificar_codigo(secreto, "") is False
    assert verificar_codigo("", "123456") is False


# ---------------------------------------------------------------------------
# security/auth.py — persistencia y flujo completo
# ---------------------------------------------------------------------------

def test_validar_credenciales_incluye_totp_habilitado_false_por_defecto(sidoe_config):
    registrar_usuario("editor_2fa_test", "ClaveSegura123!", rol="editor")
    resultado = validar_credenciales("editor_2fa_test", "ClaveSegura123!")
    assert resultado is not None
    assert resultado["totp_habilitado"] is False


def test_flujo_completo_enrolamiento_y_login_con_2fa(sidoe_config):
    registrar_usuario("admin_2fa_test", "ClaveSegura123!", rol="administrador")
    resultado = validar_credenciales("admin_2fa_test", "ClaveSegura123!")
    uid = resultado["id"]

    # Antes de activar, el login no debe exigir 2FA.
    assert resultado["totp_habilitado"] is False

    secreto, uri = iniciar_enrolamiento_totp(uid)
    assert secreto
    assert uri.startswith("otpauth://totp/")

    # Enrolamiento pendiente: totp_habilitado sigue en False hasta confirmar.
    resultado_pendiente = validar_credenciales("admin_2fa_test", "ClaveSegura123!")
    assert resultado_pendiente["totp_habilitado"] is False

    codigo_valido = pyotp.TOTP(secreto).now()
    confirmar_activacion_totp(uid, codigo_valido)

    # Tras confirmar, el login SÍ debe marcar que se requiere 2FA.
    resultado_final = validar_credenciales("admin_2fa_test", "ClaveSegura123!")
    assert resultado_final["totp_habilitado"] is True

    # Y el segundo factor debe validar correctamente el código actual.
    assert verificar_segundo_factor(uid, pyotp.TOTP(secreto).now()) is True
    assert verificar_segundo_factor(uid, "000000") is False


def test_confirmar_activacion_con_codigo_invalido_no_activa(sidoe_config):
    registrar_usuario("admin_2fa_test2", "ClaveSegura123!", rol="administrador")
    resultado = validar_credenciales("admin_2fa_test2", "ClaveSegura123!")
    uid = resultado["id"]

    iniciar_enrolamiento_totp(uid)
    with pytest.raises(ValueError):
        confirmar_activacion_totp(uid, "000000")

    # No debe haber quedado activado tras el intento fallido.
    resultado_tras_fallo = validar_credenciales("admin_2fa_test2", "ClaveSegura123!")
    assert resultado_tras_fallo["totp_habilitado"] is False


def test_confirmar_activacion_sin_enrolamiento_pendiente_lanza_lookup_error(sidoe_config):
    registrar_usuario("admin_2fa_test3", "ClaveSegura123!", rol="administrador")
    resultado = validar_credenciales("admin_2fa_test3", "ClaveSegura123!")
    uid = resultado["id"]

    with pytest.raises(LookupError):
        confirmar_activacion_totp(uid, "123456")


def test_desactivar_totp_limpia_secreto_y_flag(sidoe_config):
    registrar_usuario("admin_2fa_test4", "ClaveSegura123!", rol="administrador")
    resultado = validar_credenciales("admin_2fa_test4", "ClaveSegura123!")
    uid = resultado["id"]

    secreto, _uri = iniciar_enrolamiento_totp(uid)
    confirmar_activacion_totp(uid, pyotp.TOTP(secreto).now())
    assert validar_credenciales("admin_2fa_test4", "ClaveSegura123!")["totp_habilitado"] is True

    desactivar_totp(uid)
    assert validar_credenciales("admin_2fa_test4", "ClaveSegura123!")["totp_habilitado"] is False
    # El secreto viejo ya no debe servir para nada.
    assert verificar_segundo_factor(uid, pyotp.TOTP(secreto).now()) is False


def test_desactivar_totp_usuario_inexistente_lanza_lookup_error(sidoe_config):
    with pytest.raises(LookupError):
        desactivar_totp(999_999)


def test_iniciar_enrolamiento_usuario_inexistente_lanza_lookup_error(sidoe_config):
    with pytest.raises(LookupError):
        iniciar_enrolamiento_totp(999_999)


def test_verificar_segundo_factor_usuario_sin_2fa_es_falso(sidoe_config):
    registrar_usuario("editor_sin_2fa_test", "ClaveSegura123!", rol="editor")
    resultado = validar_credenciales("editor_sin_2fa_test", "ClaveSegura123!")
    uid = resultado["id"]
    assert verificar_segundo_factor(uid, "123456") is False
