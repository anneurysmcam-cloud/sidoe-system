"""
tests/test_13_auth_flujos_completos.py
=======================================
Cobertura de los flujos de security/auth.py que test_02_auth_seguridad.py
no ejercitaba: validar_credenciales() de punta a punta (éxito, bloqueo,
usuario inexistente/inactivo, contraseña incorrecta, migración SHA-256→
bcrypt, rol inválido), cambiar_password(), logout(), require_role(), y
dos ramas de registrar_usuario() sin cubrir (username con solo caracteres
inválidos, password vacío).

Todo opera sobre la BD temporal de `sidoe_config` — nunca la de producción.
"""

import hashlib

import pytest

from security.auth import (
    BloqueadoError,
    cambiar_password,
    logout,
    registrar_usuario,
    require_role,
    validar_credenciales,
    _verificar_password,
)


# ---------------------------------------------------------------------------
# validar_credenciales — flujo completo
# ---------------------------------------------------------------------------

class TestValidarCredenciales:

    def test_username_o_password_vacios_devuelve_none(self, sidoe_config):
        assert validar_credenciales("", "algo") is None
        assert validar_credenciales("algo", "") is None
        assert validar_credenciales("", "") is None

    def test_usuario_bloqueado_lanza_bloqueadoerror(self, sidoe_config, monkeypatch):
        import security.hardening as hardening_mod

        monkeypatch.setattr(
            hardening_mod, "verificar_bloqueo", lambda u: (True, 125)
        )
        with pytest.raises(BloqueadoError, match="bloqueada"):
            validar_credenciales("cualquiera", "ClaveX!123")

    def test_username_con_solo_caracteres_invalidos_devuelve_none(self, sidoe_config):
        """sanitizar_username() devuelve '' para un username sin ningún
        carácter permitido — validar_credenciales debe cortar ahí."""
        assert validar_credenciales("!!!@@@", "ClaveX!123") is None

    def test_usuario_inexistente_devuelve_none(self, sidoe_config):
        assert validar_credenciales("usuario_que_no_existe_123", "ClaveX!123") is None

    def test_usuario_inactivo_devuelve_none(self, sidoe_config):
        registrar_usuario("test_inactivo", "Contrasena!Fuerte99", "editor")
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        conn.execute(
            "UPDATE usuarios SET activo = 0 WHERE username = ?", ("test_inactivo",)
        )
        conn.commit()
        conn.close()
        assert validar_credenciales("test_inactivo", "Contrasena!Fuerte99") is None

    def test_password_incorrecta_devuelve_none(self, sidoe_config):
        registrar_usuario("test_pwd_incorrecta", "Contrasena!Fuerte99", "editor")
        assert validar_credenciales("test_pwd_incorrecta", "ClaveEquivocada!1") is None

    def test_login_exitoso_devuelve_dict_usuario(self, sidoe_config):
        registrar_usuario("test_login_ok", "Contrasena!Fuerte99", "editor")
        resultado = validar_credenciales("test_login_ok", "Contrasena!Fuerte99")
        assert resultado is not None
        assert resultado["username"] == "test_login_ok"
        assert resultado["rol"] == "editor"
        assert "id" in resultado

    def test_migracion_sha256_legado_a_bcrypt_en_login_exitoso(self, sidoe_config):
        """Un usuario con hash SHA-256 legado debe poder loguearse, y su
        hash debe migrarse a bcrypt automáticamente en el mismo login."""
        import data.database as db_mod

        clave = "ClaveVieja!Legado99"
        sha = hashlib.sha256(clave.encode("utf-8")).hexdigest()
        conn = db_mod.obtener_conexion()
        conn.execute(
            "INSERT INTO usuarios (username, password_hash, rol, activo, fecha_creacion) "
            "VALUES (?, ?, 'editor', 1, datetime('now'))",
            ("test_legado_sha256", sha),
        )
        conn.commit()
        conn.close()

        resultado = validar_credenciales("test_legado_sha256", clave)
        assert resultado is not None
        assert resultado["username"] == "test_legado_sha256"

        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT password_hash FROM usuarios WHERE username = ?",
            ("test_legado_sha256",),
        ).fetchone()
        conn.close()
        assert row[0].startswith("$2b$") or row[0].startswith("$2a$")

    def test_rol_invalido_en_bd_devuelve_none(self, sidoe_config, monkeypatch):
        """Un usuario con un rol que no está en ROLES_VALIDOS (ej. dato
        corrupto/legado) debe rechazarse aunque la contraseña sea correcta.
        La tabla `usuarios` tiene un CHECK constraint que impide insertar
        un rol inválido directamente (correcto a nivel de BD) — para
        ejercitar esta rama defensiva del código se mockea la fila
        devuelta por la conexión."""
        import security.auth as auth_mod
        from security.auth import hash_password

        fila_con_rol_invalido = (
            1, "test_rol_invalido", hash_password("ClaveValida!123"),
            "superadmin_invalido", 1, 0, 0,
        )

        class _CursorFalso:
            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return fila_con_rol_invalido

        class _ConnFalsa:
            def cursor(self):
                return _CursorFalso()

            def close(self):
                pass

        monkeypatch.setattr(auth_mod.db_mod, "obtener_conexion", lambda: _ConnFalsa())
        assert validar_credenciales("test_rol_invalido", "ClaveValida!123") is None

    def test_login_exitoso_limpia_intentos_fallidos_previos(self, sidoe_config):
        """Tras un login exitoso, los intentos fallidos previos deben
        limpiarse (para que el usuario no quede cerca del umbral de bloqueo
        por errores anteriores ya superados)."""
        import security.hardening as hardening_mod

        registrar_usuario("test_limpieza_intentos", "Contrasena!Fuerte99", "editor")
        validar_credenciales("test_limpieza_intentos", "ClaveIncorrecta!1")
        assert hardening_mod.intentos_restantes("test_limpieza_intentos") < 5

        validar_credenciales("test_limpieza_intentos", "Contrasena!Fuerte99")
        assert hardening_mod.intentos_restantes("test_limpieza_intentos") == 5


# ---------------------------------------------------------------------------
# _verificar_password — hash corrupto/formato inesperado
# ---------------------------------------------------------------------------

def test_verificar_password_con_hash_corrupto_devuelve_false():
    """Un hash que no es SHA-256 legado ni un bcrypt bien formado debe
    manejarse con gracia (ValueError capturado), no propagar la excepción."""
    assert _verificar_password("cualquierclave", "esto-no-es-un-hash-valido") is False


# ---------------------------------------------------------------------------
# registrar_usuario — ramas no cubiertas por test_02
# ---------------------------------------------------------------------------

class TestRegistrarUsuarioRamasAdicionales:

    def test_username_con_solo_caracteres_invalidos_lanza_valueerror(self, sidoe_config):
        with pytest.raises(ValueError, match="letras, números"):
            registrar_usuario("!!!@@@###", "Contrasena!Fuerte99", "editor")

    def test_password_vacia_lanza_valueerror(self, sidoe_config):
        with pytest.raises(ValueError, match="contraseña no puede estar vacía"):
            registrar_usuario("test_pwd_vacia", "", "editor")


# ---------------------------------------------------------------------------
# cambiar_password
# ---------------------------------------------------------------------------

class TestCambiarPassword:

    def test_cambio_exitoso_actualiza_hash(self, sidoe_config):
        registrar_usuario("test_cambio_pwd", "ClaveOriginal!123", "editor")
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        usuario_id = conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("test_cambio_pwd",)
        ).fetchone()[0]
        conn.close()

        cambiar_password(usuario_id, "ClaveOriginal!123", "ClaveNueva!456")

        resultado = validar_credenciales("test_cambio_pwd", "ClaveNueva!456")
        assert resultado is not None
        assert validar_credenciales("test_cambio_pwd", "ClaveOriginal!123") is None

    def test_password_actual_incorrecta_lanza_valueerror(self, sidoe_config):
        registrar_usuario("test_cambio_pwd_mal", "ClaveOriginal!123", "editor")
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        usuario_id = conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("test_cambio_pwd_mal",)
        ).fetchone()[0]
        conn.close()

        with pytest.raises(ValueError, match="actual es incorrecta"):
            cambiar_password(usuario_id, "ClaveIncorrecta!1", "ClaveNueva!456")

    def test_nueva_password_debil_lanza_valueerror(self, sidoe_config):
        registrar_usuario("test_cambio_pwd_debil", "ClaveOriginal!123", "editor")
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        usuario_id = conn.execute(
            "SELECT id FROM usuarios WHERE username = ?", ("test_cambio_pwd_debil",)
        ).fetchone()[0]
        conn.close()

        with pytest.raises(ValueError, match="política de seguridad"):
            cambiar_password(usuario_id, "ClaveOriginal!123", "1234")

    def test_usuario_id_inexistente_lanza_lookuperror(self, sidoe_config):
        with pytest.raises(LookupError, match="no encontrado"):
            cambiar_password(999999, "Cualquiera!123", "ClaveNueva!456")


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

def test_logout_limpia_el_estado_de_sesion():
    estado_falso = {"usuario": {"username": "x"}, "otro_dato": 123}
    logout(estado_falso)
    assert len(estado_falso) == 0


# ---------------------------------------------------------------------------
# require_role — decorador de control de acceso
# ---------------------------------------------------------------------------

class _DetenerEjecucion(Exception):
    """Sustituto de prueba para el efecto real de st.stop() (que en una
    app Streamlit real interrumpe el script; en modo bare no lo hace)."""


class TestRequireRole:

    def _preparar_mocks(self, monkeypatch, session_state, capturar_error):
        import security.auth as auth_mod

        monkeypatch.setattr(auth_mod.st, "session_state", session_state)
        monkeypatch.setattr(auth_mod.st, "error", capturar_error.append)

        def _stop():
            raise _DetenerEjecucion()

        monkeypatch.setattr(auth_mod.st, "stop", _stop)

    def test_usuario_con_rol_permitido_ejecuta_la_funcion(self, monkeypatch):
        errores = []
        self._preparar_mocks(
            monkeypatch, {"usuario": {"rol": "administrador"}}, errores
        )

        @require_role(["administrador", "editor"])
        def vista_protegida():
            return "contenido secreto"

        assert vista_protegida() == "contenido secreto"
        assert errores == []

    def test_usuario_con_rol_no_permitido_bloquea_acceso(self, monkeypatch):
        errores = []
        self._preparar_mocks(
            monkeypatch, {"usuario": {"rol": "editor"}}, errores
        )

        @require_role(["administrador"])
        def vista_protegida():
            return "no debería llegar aquí"

        with pytest.raises(_DetenerEjecucion):
            vista_protegida()
        assert len(errores) == 1
        assert "Acceso denegado" in errores[0]

    def test_sin_usuario_en_sesion_bloquea_acceso(self, monkeypatch):
        errores = []
        self._preparar_mocks(monkeypatch, {}, errores)

        @require_role(["editor"])
        def vista_protegida():
            return "no debería llegar aquí"

        with pytest.raises(_DetenerEjecucion):
            vista_protegida()
        assert len(errores) == 1
