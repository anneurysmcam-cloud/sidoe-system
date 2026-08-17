"""
tests/test_17_app_modo_publico.py
===================================
TESTS DE REGRESIÓN — Punto 9: acceso público sin sesión

Valida que app.py:
  - Sin sesión iniciada, muestra directamente el menú público (Generar
    Consulta / Generar Ficha / Dashboard) sin exigir login.
  - Las opciones de Editor/Administrador NO aparecen sin sesión.
  - El login desde el expander del modo público funciona y transiciona
    correctamente al menú completo del rol autenticado.

Usa streamlit.testing.v1.AppTest para ejecutar app.py de punta a punta
(incluye el bootstrap de BD) y verificar que no lanza excepciones.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

OPCIONES_PUBLICAS = ["Generar Consulta", "Generar Ficha", "Dashboard"]


class TestModoPublico:

    def test_sin_sesion_muestra_menu_publico_sin_excepciones(self, sidoe_config):
        # La landing institucional es el primer render en modo público (ver
        # views/landing.py); se pasa directamente al menú de opciones para
        # validar el comportamiento histórico de este test, que es sobre el
        # radio público, no sobre la landing en sí.
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()

        assert not at.exception
        assert at.session_state["usuario"] is None
        assert len(at.sidebar.radio) == 1
        assert at.sidebar.radio[0].options == OPCIONES_PUBLICAS

    def test_sin_sesion_no_expone_opciones_de_editor_o_admin(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()

        opciones_visibles = set(at.sidebar.radio[0].options)
        opciones_privilegiadas = {
            "Crear Nuevo Indicador", "Actualizar Indicador", "Eliminar Indicador",
            "Indicadores Desactivados", "Ver Auditoría", "Administrar Usuarios",
            "Auxiliares",
        }
        assert opciones_visibles.isdisjoint(opciones_privilegiadas)

    def test_login_desde_modo_publico_transiciona_a_menu_completo(self, sidoe_config):
        from security.auth import registrar_usuario

        registrar_usuario("p9_editor_test", "ClaveSegura#2026", rol="editor")

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        assert not at.exception

        textos = at.sidebar.text_input
        assert len(textos) == 2
        textos[0].set_value("p9_editor_test")
        textos[1].set_value("ClaveSegura#2026")
        at.sidebar.button[0].click()
        at.run()

        assert not at.exception
        assert at.session_state["usuario"] is not None
        assert at.session_state["usuario"]["rol"] == "editor"
        assert "Crear Nuevo Indicador" in at.sidebar.radio[0].options

    def test_modo_administrador_ve_menu_completo_sin_excepciones(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["usuario"] = {
            "id": 1, "username": "admin_apptest", "rol": "administrador",
        }
        at.run()

        assert not at.exception
        opciones = set(at.sidebar.radio[0].options)
        # Reestructuración de roles (agosto-2026): Administrador ya no
        # administra indicadores ni Auxiliares — ver imagen "Creación de
        # Nuevos Roles y Reestructuración de estos" (jefa). Esas funciones
        # pasaron al rol supervisor (ver test_menu_supervisor abajo).
        assert {"Ver Auditoría", "Administrar Usuarios"} <= opciones
        assert "Auxiliares" not in opciones
        assert "Eliminar Indicador" not in opciones

    def test_modo_supervisor_ve_menu_completo_sin_excepciones(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["usuario"] = {
            "id": 1, "username": "supervisor_apptest", "rol": "supervisor",
        }
        at.run()

        assert not at.exception
        opciones = set(at.sidebar.radio[0].options)
        assert {
            "Crear Nuevo Indicador", "Actualizar Indicador", "Eliminar Indicador",
            "Aprobar Indicadores", "Indicadores Desactivados", "Auxiliares",
        } <= opciones
        assert "Ver Auditoría" not in opciones
        assert "Administrar Usuarios" not in opciones
