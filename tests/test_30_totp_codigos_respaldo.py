"""
tests/test_30_totp_codigos_respaldo.py
=======================================
Cobertura de códigos de respaldo (recovery codes) del 2FA:

- security/totp.py: generar_codigos_respaldo (primitiva pura).
- security/auth.py: generar_y_guardar_codigos_respaldo, verificar_codigo_respaldo,
  contar_codigos_respaldo_restantes, y su interacción con desactivar_totp
  (limpieza) y confirmar_activacion_totp (flujo real de la vista).
"""

import re

import pyotp

from security.auth import (
    confirmar_activacion_totp,
    contar_codigos_respaldo_restantes,
    desactivar_totp,
    generar_y_guardar_codigos_respaldo,
    iniciar_enrolamiento_totp,
    registrar_usuario,
    validar_credenciales,
    verificar_codigo_respaldo,
)
from security.totp import CANTIDAD_CODIGOS_RESPALDO, generar_codigos_respaldo

_PATRON_CODIGO = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")


# ---------------------------------------------------------------------------
# security/totp.py — primitiva pura
# ---------------------------------------------------------------------------

def test_generar_codigos_respaldo_cantidad_y_formato_por_defecto():
    codigos = generar_codigos_respaldo()
    assert len(codigos) == CANTIDAD_CODIGOS_RESPALDO
    for codigo in codigos:
        assert _PATRON_CODIGO.match(codigo), codigo


def test_generar_codigos_respaldo_sin_caracteres_ambiguos():
    # 0/O y 1/I/L quedan fuera del alfabeto para evitar errores de transcripción.
    codigos = generar_codigos_respaldo(50)
    texto = "".join(codigos)
    for caracter_prohibido in "01IOL":
        assert caracter_prohibido not in texto


def test_generar_codigos_respaldo_son_distintos_entre_si():
    codigos = generar_codigos_respaldo(20)
    assert len(set(codigos)) == len(codigos)


def test_generar_codigos_respaldo_cantidad_personalizada():
    assert len(generar_codigos_respaldo(3)) == 3


# ---------------------------------------------------------------------------
# security/auth.py — persistencia y verificación
# ---------------------------------------------------------------------------

def _crear_admin_con_2fa_activo(sidoe_config, username: str) -> tuple[int, str]:
    registrar_usuario(username, "ClaveSegura123!", rol="administrador")
    resultado = validar_credenciales(username, "ClaveSegura123!")
    uid = resultado["id"]
    secreto, _uri = iniciar_enrolamiento_totp(uid)
    confirmar_activacion_totp(uid, pyotp.TOTP(secreto).now())
    return uid, secreto


def test_generar_y_guardar_codigos_respaldo_devuelve_texto_plano_y_persiste_conteo(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test1")

    codigos = generar_y_guardar_codigos_respaldo(uid)
    assert len(codigos) == CANTIDAD_CODIGOS_RESPALDO
    assert contar_codigos_respaldo_restantes(uid) == CANTIDAD_CODIGOS_RESPALDO


def test_verificar_codigo_respaldo_valido_lo_marca_usado_y_no_sirve_dos_veces(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test2")
    codigos = generar_y_guardar_codigos_respaldo(uid)
    primero = codigos[0]

    assert verificar_codigo_respaldo(uid, primero) is True
    assert contar_codigos_respaldo_restantes(uid) == CANTIDAD_CODIGOS_RESPALDO - 1

    # Un código de un solo uso no debe volver a funcionar.
    assert verificar_codigo_respaldo(uid, primero) is False


def test_verificar_codigo_respaldo_acepta_minusculas_y_espacios(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test3")
    codigos = generar_y_guardar_codigos_respaldo(uid)
    variante = f" {codigos[0].lower()} "

    assert verificar_codigo_respaldo(uid, variante) is True


def test_verificar_codigo_respaldo_incorrecto_es_falso(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test4")
    generar_y_guardar_codigos_respaldo(uid)

    assert verificar_codigo_respaldo(uid, "ZZZZ-ZZZZ") is False
    assert contar_codigos_respaldo_restantes(uid) == CANTIDAD_CODIGOS_RESPALDO


def test_verificar_codigo_respaldo_vacio_o_none_es_falso(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test5")
    generar_y_guardar_codigos_respaldo(uid)

    assert verificar_codigo_respaldo(uid, "") is False
    assert verificar_codigo_respaldo(uid, "   ") is False


def test_regenerar_codigos_respaldo_invalida_el_lote_anterior(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test6")
    lote_viejo = generar_y_guardar_codigos_respaldo(uid)
    lote_nuevo = generar_y_guardar_codigos_respaldo(uid)

    assert contar_codigos_respaldo_restantes(uid) == CANTIDAD_CODIGOS_RESPALDO
    # El primer código del lote viejo ya no debe ser válido tras regenerar.
    assert verificar_codigo_respaldo(uid, lote_viejo[0]) is False
    # Pero el lote nuevo sí funciona.
    assert verificar_codigo_respaldo(uid, lote_nuevo[0]) is True


def test_desactivar_totp_elimina_codigos_de_respaldo(sidoe_config):
    uid, _secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test7")
    generar_y_guardar_codigos_respaldo(uid)
    assert contar_codigos_respaldo_restantes(uid) == CANTIDAD_CODIGOS_RESPALDO

    desactivar_totp(uid)

    assert contar_codigos_respaldo_restantes(uid) == 0


def test_contar_codigos_respaldo_restantes_sin_2fa_es_cero(sidoe_config):
    registrar_usuario("editor_resp_test1", "ClaveSegura123!", rol="editor")
    resultado = validar_credenciales("editor_resp_test1", "ClaveSegura123!")
    assert contar_codigos_respaldo_restantes(resultado["id"]) == 0


def test_verificar_codigo_respaldo_usuario_sin_codigos_es_falso(sidoe_config):
    registrar_usuario("editor_resp_test2", "ClaveSegura123!", rol="editor")
    resultado = validar_credenciales("editor_resp_test2", "ClaveSegura123!")
    assert verificar_codigo_respaldo(resultado["id"], "AAAA-BBBB") is False


def test_flujo_completo_login_con_codigo_de_respaldo(sidoe_config):
    """Simula el escenario real: admin con 2FA activo pierde el teléfono y
    entra con un código de respaldo en vez del código TOTP."""
    uid, secreto = _crear_admin_con_2fa_activo(sidoe_config, "admin_resp_test8")
    codigos = generar_y_guardar_codigos_respaldo(uid)

    resultado = validar_credenciales("admin_resp_test8", "ClaveSegura123!")
    assert resultado["totp_habilitado"] is True

    # Sin el teléfono, el código TOTP normal ya no es una opción viable —
    # el usuario usa uno de sus códigos de respaldo en su lugar.
    assert verificar_codigo_respaldo(uid, codigos[3]) is True

    # El código TOTP real sigue funcionando en paralelo (no se invalidó).
    from security.auth import verificar_segundo_factor
    assert verificar_segundo_factor(uid, pyotp.TOTP(secreto).now()) is True
