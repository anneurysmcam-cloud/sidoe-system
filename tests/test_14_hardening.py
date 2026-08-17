"""
tests/test_14_hardening.py
===========================
Cobertura de security/hardening.py — módulo que solo estaba ejercitado
indirectamente (vía test_02_auth_seguridad.py) para validar_politica_password
y sanitizar_username. Este archivo cubre el resto:

1. Protección anti-fuerza-bruta (registrar_intento_fallido, verificar_bloqueo,
   limpiar_intentos_exitosos, intentos_restantes)
2. Timeout de sesión (registrar_actividad, verificar_timeout_sesion,
   minutos_restantes_sesion) — st.session_state se sustituye por un dict
   plano vía monkeypatch para evitar dependencias del runtime de Streamlit.
3. Saneamiento de texto y URLs (sanitizar_texto, sanitizar_url)
4. Permisos de archivo de la BD (asegurar_permisos_db)
5. Auditoría de login fallido en BD (auditar_login_fallido)
"""

import os
import stat
import time

import pytest

import security.hardening as hardening_mod
from security.hardening import (
    _MAX_INTENTOS_LOGIN,
    _TIMEOUT_INACTIVIDAD_SEG,
    asegurar_permisos_db,
    auditar_login_fallido,
    intentos_restantes,
    limpiar_intentos_exitosos,
    minutos_restantes_sesion,
    registrar_actividad,
    registrar_intento_fallido,
    sanitizar_texto,
    sanitizar_url,
    verificar_bloqueo,
    verificar_timeout_sesion,
)


@pytest.fixture(autouse=True)
def _limpiar_estado_fuerza_bruta():
    """Los diccionarios de intentos/bloqueos son globales por proceso —
    se limpian antes y después de cada test para evitar fugas entre tests."""
    hardening_mod._intentos_fallidos.clear()
    hardening_mod._bloqueados.clear()
    yield
    hardening_mod._intentos_fallidos.clear()
    hardening_mod._bloqueados.clear()


# ---------------------------------------------------------------------------
# 1 · Anti-fuerza-bruta
# ---------------------------------------------------------------------------

class TestAntiFuerzaBruta:

    def test_usuario_sin_intentos_no_esta_bloqueado(self):
        bloqueado, segundos = verificar_bloqueo("usuario_nuevo")
        assert bloqueado is False
        assert segundos == 0

    def test_intentos_restantes_inicia_en_el_maximo(self):
        assert intentos_restantes("usuario_fresco") == _MAX_INTENTOS_LOGIN

    def test_intentos_fallidos_reducen_intentos_restantes(self):
        registrar_intento_fallido("usuario_con_fallos")
        registrar_intento_fallido("usuario_con_fallos")
        assert intentos_restantes("usuario_con_fallos") == _MAX_INTENTOS_LOGIN - 2

    def test_superar_el_umbral_bloquea_al_usuario(self):
        for _ in range(_MAX_INTENTOS_LOGIN):
            registrar_intento_fallido("usuario_a_bloquear")
        bloqueado, segundos = verificar_bloqueo("usuario_a_bloquear")
        assert bloqueado is True
        assert segundos > 0

    def test_bloqueo_expirado_se_limpia_automaticamente(self):
        """Simula que el bloqueo ya venció (timestamp en el pasado) y
        confirma que verificar_bloqueo lo detecta y limpia."""
        hardening_mod._bloqueados["usuario_expirado"] = time.time() - 10
        bloqueado, segundos = verificar_bloqueo("usuario_expirado")
        assert bloqueado is False
        assert segundos == 0
        assert "usuario_expirado" not in hardening_mod._bloqueados

    def test_limpiar_intentos_exitosos_borra_historial_y_bloqueo(self):
        for _ in range(_MAX_INTENTOS_LOGIN):
            registrar_intento_fallido("usuario_a_limpiar")
        assert verificar_bloqueo("usuario_a_limpiar")[0] is True

        limpiar_intentos_exitosos("usuario_a_limpiar")

        assert verificar_bloqueo("usuario_a_limpiar")[0] is False
        assert intentos_restantes("usuario_a_limpiar") == _MAX_INTENTOS_LOGIN

    def test_ventana_deslizante_descarta_intentos_antiguos(self):
        """Intentos fuera de la ventana de 5 minutos no deben contar para
        el umbral de bloqueo."""
        ahora = time.time()
        hardening_mod._intentos_fallidos["usuario_ventana"] = [
            ahora - 400, ahora - 350,  # fuera de la ventana (300s)
        ]
        registrar_intento_fallido("usuario_ventana")
        # Solo el intento recién agregado debe contar (los 2 viejos se descartan)
        assert intentos_restantes("usuario_ventana") == _MAX_INTENTOS_LOGIN - 1

    def test_limpiar_intentos_de_usuario_sin_historial_no_falla(self):
        """pop(..., None) debe ser inofensivo si el usuario nunca falló."""
        limpiar_intentos_exitosos("usuario_que_nunca_fallo")


# ---------------------------------------------------------------------------
# 2 · Timeout de sesión
# ---------------------------------------------------------------------------

class TestTimeoutSesion:

    def test_sin_actividad_previa_no_hay_timeout(self, monkeypatch):
        monkeypatch.setattr(hardening_mod.st, "session_state", {})
        assert verificar_timeout_sesion() is False

    def test_sin_actividad_previa_minutos_restantes_es_el_maximo(self, monkeypatch):
        monkeypatch.setattr(hardening_mod.st, "session_state", {})
        assert minutos_restantes_sesion() == _TIMEOUT_INACTIVIDAD_SEG // 60

    def test_registrar_actividad_guarda_timestamp_actual(self, monkeypatch):
        estado = {}
        monkeypatch.setattr(hardening_mod.st, "session_state", estado)
        antes = time.time()
        registrar_actividad()
        assert estado["_sidoe_ultimo_acceso"] >= antes

    def test_actividad_reciente_no_expira(self, monkeypatch):
        estado = {"_sidoe_ultimo_acceso": time.time()}
        monkeypatch.setattr(hardening_mod.st, "session_state", estado)
        assert verificar_timeout_sesion() is False
        assert minutos_restantes_sesion() > 0

    def test_actividad_antigua_expira(self, monkeypatch):
        estado = {
            "_sidoe_ultimo_acceso": time.time() - _TIMEOUT_INACTIVIDAD_SEG - 60
        }
        monkeypatch.setattr(hardening_mod.st, "session_state", estado)
        assert verificar_timeout_sesion() is True
        assert minutos_restantes_sesion() == 0


# ---------------------------------------------------------------------------
# 3 · Saneamiento de texto y URLs
# ---------------------------------------------------------------------------

class TestSanitizarTexto:

    def test_valor_none_devuelve_cadena_vacia(self):
        assert sanitizar_texto(None) == ""

    def test_valor_vacio_devuelve_cadena_vacia(self):
        assert sanitizar_texto("") == ""

    def test_elimina_caracteres_de_control(self):
        resultado = sanitizar_texto("Texto\x00con\x01control\x1f")
        assert "\x00" not in resultado
        assert "\x01" not in resultado

    def test_conserva_tabs_y_saltos_de_linea(self):
        resultado = sanitizar_texto("Línea1\nLínea2\tTab", max_len=100)
        assert "\n" in resultado
        assert "\t" in resultado

    def test_trunca_al_maximo_permitido(self):
        resultado = sanitizar_texto("a" * 300, max_len=50)
        assert len(resultado) == 50

    def test_hace_strip_de_espacios_extremos(self):
        assert sanitizar_texto("   hola   ") == "hola"


class TestSanitizarUrl:

    def test_url_none_devuelve_vacio(self):
        assert sanitizar_url(None) == ""

    def test_url_vacia_devuelve_vacio(self):
        assert sanitizar_url("") == ""

    def test_url_http_valida_se_conserva(self):
        assert sanitizar_url("http://example.com/data") == "http://example.com/data"

    def test_url_https_valida_se_conserva(self):
        assert sanitizar_url("https://one.gob.do") == "https://one.gob.do"

    def test_url_sin_esquema_valido_se_rechaza(self):
        assert sanitizar_url("javascript:alert(1)") == ""

    def test_url_ftp_se_rechaza(self):
        assert sanitizar_url("ftp://servidor/archivo") == ""


class TestSanitizarUsernameVacio:
    """test_02_auth_seguridad.py cubre los rechazos por caracteres
    inválidos; aquí se cubre la rama de entrada vacía/None que ese
    archivo no ejercitaba."""

    def test_username_none_devuelve_vacio(self):
        from security.hardening import sanitizar_username
        assert sanitizar_username(None) == ""

    def test_username_cadena_vacia_devuelve_vacio(self):
        from security.hardening import sanitizar_username
        assert sanitizar_username("") == ""


class TestPasswordEsValida:

    def test_password_fuerte_es_valida(self):
        from security.hardening import password_es_valida
        assert password_es_valida("Contrasena!Fuerte99") is True

    def test_password_debil_no_es_valida(self):
        from security.hardening import password_es_valida
        assert password_es_valida("1234") is False


# ---------------------------------------------------------------------------
# 4 · Permisos de archivo de BD
# ---------------------------------------------------------------------------

class TestAsegurarPermisosDb:

    def test_archivo_inexistente_no_falla(self, tmp_path):
        ruta_falsa = str(tmp_path / "no_existe.db")
        asegurar_permisos_db(ruta_falsa)  # no debe lanzar excepción

    def test_windows_omite_verificacion(self, tmp_path, monkeypatch):
        archivo = tmp_path / "test.db"
        archivo.write_text("contenido")
        monkeypatch.setattr(os, "name", "nt")
        asegurar_permisos_db(str(archivo))  # no debe intentar chmod en Windows

    def test_corrige_permisos_incorrectos_a_600(self, tmp_path):
        archivo = tmp_path / "test.db"
        archivo.write_text("contenido")
        os.chmod(str(archivo), 0o644)  # permisos demasiado abiertos a propósito

        asegurar_permisos_db(str(archivo))

        permisos = stat.S_IMODE(os.stat(str(archivo)).st_mode)
        assert permisos == (stat.S_IRUSR | stat.S_IWUSR)

    def test_permisos_ya_correctos_no_falla(self, tmp_path):
        archivo = tmp_path / "test.db"
        archivo.write_text("contenido")
        os.chmod(str(archivo), stat.S_IRUSR | stat.S_IWUSR)

        asegurar_permisos_db(str(archivo))  # rama "ya correcto" — no debe fallar

        permisos = stat.S_IMODE(os.stat(str(archivo)).st_mode)
        assert permisos == (stat.S_IRUSR | stat.S_IWUSR)

    def test_error_al_cambiar_permisos_se_maneja_con_gracia(self, tmp_path, monkeypatch):
        archivo = tmp_path / "test.db"
        archivo.write_text("contenido")

        def _chmod_falla(*args, **kwargs):
            raise OSError("Permiso denegado (simulado)")

        monkeypatch.setattr(os, "chmod", _chmod_falla)
        asegurar_permisos_db(str(archivo))  # no debe propagar la excepción


# ---------------------------------------------------------------------------
# 5 · Auditoría de login fallido
# ---------------------------------------------------------------------------

class TestAuditarLoginFallido:

    def test_registra_intento_fallido_en_bd(self, sidoe_config):
        auditar_login_fallido("usuario_inexistente_xyz", "Usuario no encontrado")

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT accion, detalle FROM auditoria WHERE accion = 'LOGIN_FALLIDO' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert "usuario_inexistente_xyz" in row[1]
        assert "Usuario no encontrado" in row[1]

    def test_usa_motivo_por_defecto_si_no_se_especifica(self, sidoe_config):
        auditar_login_fallido("usuario_sin_motivo_explicito")

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT detalle FROM auditoria WHERE accion = 'LOGIN_FALLIDO' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert "Credenciales inválidas" in row[0]

    def test_error_de_bd_durante_insert_no_propaga_excepcion(self, sidoe_config, monkeypatch):
        """Si el INSERT falla (ej. tabla bloqueada), debe hacer rollback y
        loguear la advertencia, sin romper el flujo de login."""
        import data.database as db_mod

        conn_real = db_mod.obtener_conexion()

        class _CursorQueFalla:
            def execute(self, *a, **k):
                raise Exception("Fallo simulado de escritura")

        class _ConnFalsa:
            def cursor(self):
                return _CursorQueFalla()

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(
            hardening_mod, "obtener_conexion", lambda: _ConnFalsa(), raising=False
        )
        # auditar_login_fallido importa obtener_conexion localmente desde
        # data.database, así que se parchea ahí:
        monkeypatch.setattr(db_mod, "obtener_conexion", lambda: _ConnFalsa())
        auditar_login_fallido("usuario_x", "motivo_x")  # no debe lanzar
        conn_real.close()

    def test_bd_no_disponible_no_propaga_excepcion(self, monkeypatch):
        """Si obtener_conexion() en sí falla (BD no disponible), el except
        externo debe capturarlo silenciosamente."""
        import data.database as db_mod

        def _falla_conexion():
            raise Exception("BD no disponible (simulado)")

        monkeypatch.setattr(db_mod, "obtener_conexion", _falla_conexion)
        auditar_login_fallido("usuario_y", "motivo_y")  # no debe lanzar
