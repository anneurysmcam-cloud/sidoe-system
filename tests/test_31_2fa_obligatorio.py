"""
tests/test_31_2fa_obligatorio.py
=================================
Cobertura del 2FA obligatorio exigido por administrador (columna
``requiere_2fa`` en usuarios — distinta de ``totp_habilitado``, que indica
si el usuario ya lo configuró).

- data/database.py: migración idempotente de la columna.
- security/auth.py: validar_credenciales expone requiere_2fa.
- views/admin_usuarios.py: el admin puede exigir/quitar la exigencia a un
  usuario puntual (con auditoría).
- app.py: el login de un usuario con requiere_2fa=1 y totp_habilitado=0 NO
  establece sesión hasta completar el enrolamiento TOTP forzado.
"""

import pyotp
import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

import data.database  # noqa: F401 — ver test_25 para por qué este import va a nivel de módulo
from streamlit.testing.v1 import AppTest

from security.auth import registrar_usuario, validar_credenciales

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


# ---------------------------------------------------------------------------
# data/database.py — migración
# ---------------------------------------------------------------------------

def test_migrar_requiere_2fa_es_idempotente(sidoe_config):
    from data.database import migrar_requiere_2fa

    migrar_requiere_2fa()
    migrar_requiere_2fa()  # segunda llamada no debe fallar ni duplicar la columna


# ---------------------------------------------------------------------------
# security/auth.py — validar_credenciales
# ---------------------------------------------------------------------------

def test_validar_credenciales_incluye_requiere_2fa_false_por_defecto(sidoe_config):
    registrar_usuario("editor_req2fa_test", "ClaveSegura123!", rol="editor")
    resultado = validar_credenciales("editor_req2fa_test", "ClaveSegura123!")
    assert resultado is not None
    assert resultado["requiere_2fa"] is False


def test_admin_exige_2fa_se_refleja_en_validar_credenciales(sidoe_config, db_conn):
    registrar_usuario("editor_forzado_test", "ClaveSegura123!", rol="editor")
    db_conn.execute(
        "UPDATE usuarios SET requiere_2fa = 1 WHERE username = ?",
        ("editor_forzado_test",),
    )
    db_conn.commit()

    resultado = validar_credenciales("editor_forzado_test", "ClaveSegura123!")
    assert resultado["requiere_2fa"] is True
    assert resultado["totp_habilitado"] is False  # aún no lo configuró


# ---------------------------------------------------------------------------
# views/admin_usuarios.py — toggle de exigencia (AppTest)
# ---------------------------------------------------------------------------

def _login_admin(at: AppTest) -> None:
    at.session_state["usuario"] = {
        "id": 1, "username": "p31_admin_apptest", "rol": "administrador",
    }


def _navegar_a(at: AppTest, opcion: str) -> None:
    at.sidebar.radio[0].set_value(opcion).run()
    assert not at.exception


class TestAdminExigir2FAAppTest:

    def test_admin_puede_exigir_2fa_a_un_editor(self, sidoe_config, db_conn):
        registrar_usuario("editor_p31_toggle", "ClaveSegura123!", rol="editor")
        uid = db_conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("editor_p31_toggle",)
        ).fetchone()[0]

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_admin(at)
        at.run()
        _navegar_a(at, "Administrar Usuarios")

        sel = next(sb for sb in at.selectbox if sb.label == "Selecciona usuario por ID")
        sel.set_value(uid).run()
        assert not at.exception

        boton = next(b for b in at.button if b.label == "🔒 Exigir 2FA")
        boton.click().run()
        assert not at.exception

        fila = db_conn.execute(
            "SELECT requiere_2fa FROM usuarios WHERE id = ?", (uid,)
        ).fetchone()
        assert fila[0] == 1

    def test_admin_puede_quitar_la_exigencia(self, sidoe_config, db_conn):
        registrar_usuario("editor_p31_toggle2", "ClaveSegura123!", rol="editor")
        uid = db_conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("editor_p31_toggle2",)
        ).fetchone()[0]
        db_conn.execute("UPDATE usuarios SET requiere_2fa = 1 WHERE id = ?", (uid,))
        db_conn.commit()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_admin(at)
        at.run()
        _navegar_a(at, "Administrar Usuarios")

        sel = next(sb for sb in at.selectbox if sb.label == "Selecciona usuario por ID")
        sel.set_value(uid).run()
        assert not at.exception

        boton = next(b for b in at.button if b.label == "🔓 Quitar exigencia de 2FA")
        boton.click().run()
        assert not at.exception

        fila = db_conn.execute(
            "SELECT requiere_2fa FROM usuarios WHERE id = ?", (uid,)
        ).fetchone()
        assert fila[0] == 0

    def test_quitar_la_exigencia_tambien_desactiva_el_totp_ya_configurado(
        self, sidoe_config, db_conn
    ):
        """Regresión: si el usuario ya completó el enrolamiento forzado
        (totp_habilitado=1), 'Quitar exigencia de 2FA' debe desactivarle el
        TOTP también — de lo contrario seguiría pidiéndole el código en
        cada login pese a que el admin ya no lo exige (reportado por
        Randy: el toggle solo bajaba requiere_2fa a 0 sin tocar
        totp_habilitado/totp_secret)."""
        import pyotp

        from security.auth import (
            confirmar_activacion_totp,
            generar_y_guardar_codigos_respaldo,
            iniciar_enrolamiento_totp,
        )

        registrar_usuario("editor_p31_toggle3", "ClaveSegura123!", rol="editor")
        uid = db_conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("editor_p31_toggle3",)
        ).fetchone()[0]
        db_conn.execute("UPDATE usuarios SET requiere_2fa = 1 WHERE id = ?", (uid,))
        db_conn.commit()

        secreto, _uri = iniciar_enrolamiento_totp(uid)
        confirmar_activacion_totp(uid, pyotp.TOTP(secreto).now())
        generar_y_guardar_codigos_respaldo(uid)

        pre = db_conn.execute(
            "SELECT requiere_2fa, totp_habilitado FROM usuarios WHERE id = ?", (uid,)
        ).fetchone()
        assert pre[0] == 1 and pre[1] == 1

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_admin(at)
        at.run()
        _navegar_a(at, "Administrar Usuarios")

        sel = next(sb for sb in at.selectbox if sb.label == "Selecciona usuario por ID")
        sel.set_value(uid).run()
        assert not at.exception

        boton = next(b for b in at.button if b.label == "🔓 Quitar exigencia de 2FA")
        boton.click().run()
        assert not at.exception

        post = db_conn.execute(
            "SELECT requiere_2fa, totp_habilitado, totp_secret FROM usuarios WHERE id = ?",
            (uid,),
        ).fetchone()
        assert post[0] == 0
        assert post[1] == 0
        assert post[2] is None

        codigos_restantes = db_conn.execute(
            "SELECT COUNT(*) FROM totp_codigos_respaldo WHERE usuario_id = ?", (uid,)
        ).fetchone()[0]
        assert codigos_restantes == 0

        # Y el login ya no debe pedir código: totp_habilitado quedó en False.
        resultado = validar_credenciales("editor_p31_toggle3", "ClaveSegura123!")
        assert resultado["totp_habilitado"] is False
        assert resultado["requiere_2fa"] is False


# ---------------------------------------------------------------------------
# app.py — enrolamiento forzado durante el login (AppTest)
# ---------------------------------------------------------------------------

class TestLoginConDosFactorObligatorioAppTest:

    def test_bloquea_acceso_hasta_completar_enrolamiento_y_luego_establece_sesion(
        self, sidoe_config, db_conn
    ):
        registrar_usuario("editor_p31_forzado", "ClaveSegura123!", rol="editor")
        uid = db_conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("editor_p31_forzado",)
        ).fetchone()[0]
        db_conn.execute("UPDATE usuarios SET requiere_2fa = 1 WHERE id = ?", (uid,))
        db_conn.commit()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        # Se siembra el estado "usuario pendiente de configurar 2FA" tal
        # como lo dejaría _procesar_intento_login tras un password correcto
        # — igual que test_25 salta el formulario de login sembrando
        # session_state["usuario"] directamente, acá se salta un paso antes.
        at.session_state["usuario_pendiente_setup_2fa"] = {
            "id": uid, "username": "editor_p31_forzado", "rol": "editor",
            "totp_habilitado": False, "requiere_2fa": True,
        }
        at.run()
        assert not at.exception
        assert at.session_state["usuario"] is None
        assert any("Configuración obligatoria de 2FA" in h.value for h in at.header)

        boton_comenzar = next(b for b in at.button if b.label == "🔐 Comenzar configuración de 2FA")
        boton_comenzar.click().run()
        assert not at.exception
        assert at.session_state["usuario"] is None
        secreto = at.session_state["setup2fa_secreto"]

        codigo_valido = pyotp.TOTP(secreto).now()
        campo_codigo = next(
            ti for ti in at.text_input if ti.label == "Código de 6 dígitos"
        )
        campo_codigo.set_value(codigo_valido)
        boton_confirmar = next(
            b for b in at.button if b.label == "✅ Confirmar y activar"
        )
        boton_confirmar.click().run()
        assert not at.exception
        assert at.session_state["usuario"] is None  # todavía faltan los códigos de respaldo
        assert "setup2fa_codigos_respaldo" in at.session_state
        assert at.session_state["setup2fa_codigos_respaldo"]

        boton_continuar = next(b for b in at.button if b.label == "Continuar al sistema")
        boton_continuar.click().run()
        assert not at.exception
        assert at.session_state["usuario"]["id"] == uid
        assert "usuario_pendiente_setup_2fa" not in at.session_state
        assert "setup2fa_codigos_respaldo" not in at.session_state

        from security.auth import validar_credenciales

        resultado = validar_credenciales("editor_p31_forzado", "ClaveSegura123!")
        assert resultado["totp_habilitado"] is True
