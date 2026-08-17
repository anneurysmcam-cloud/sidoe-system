"""
tests/test_33_landing_publica.py
=================================
TEST DE REGRESIÓN — landing institucional (views/landing.py) mostrada al
entrar sin sesión.

Valida que:
  - Sin sesión y sin haber pasado por la landing, se muestra la landing y
    NO el radio de opciones públicas (el flujo se corta antes con st.stop()).
  - Pulsar cualquiera de los 3 accesos rápidos marca `landing_dismissed` y
    preselecciona la opción de menú correspondiente.
  - Con `landing_dismissed=True` ya seteado (visitante que volvió a entrar
    en la misma sesión de navegador), se salta la landing directamente.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


class TestLandingPublica:

    def test_sin_sesion_muestra_landing_y_no_el_radio(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()

        assert not at.exception
        assert at.session_state["usuario"] is None
        assert at.session_state["landing_dismissed"] is False
        assert len(at.sidebar.radio) == 0
        etiquetas_landing = {"🔍 Consultar indicadores", "📊 Ver dashboard", "📄 Generar ficha"}
        assert etiquetas_landing <= {b.label for b in at.button}

    def test_click_en_dashboard_preselecciona_esa_opcion(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()

        boton_dashboard = next(
            b for b in at.button if "dashboard" in b.label.lower()
        )
        boton_dashboard.click().run()

        assert not at.exception
        assert at.session_state["landing_dismissed"] is True
        assert at.session_state["opcion_publica_preseleccionada"] == "Dashboard"
        assert len(at.sidebar.radio) == 1
        assert at.sidebar.radio[0].value == "Dashboard"

    def test_landing_dismissed_previo_salta_directo_al_menu(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()

        assert not at.exception
        assert len(at.sidebar.radio) == 1
        assert at.sidebar.radio[0].options == [
            "Generar Consulta", "Generar Ficha", "Dashboard",
        ]
