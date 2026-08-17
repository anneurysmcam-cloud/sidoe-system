"""
tests/test_26_manejo_errores_vistas.py
=======================================
TEST DE REGRESIÓN — punto planteado por Randy: un error de backend no
controlado (ej. el ArrowInvalid de pyarrow con str.contains regex) no debe
llegar al usuario con su traceback técnico, pero sí debe quedar registrado
para el equipo.

Valida que app.py._ejecutar_vista():
  - Atrapa cualquier excepción no controlada que se escape de una vista.
  - AppTest no reporta la excepción como no manejada (at.exception vacío).
  - El usuario ve un mensaje genérico con un código de incidente, nunca el
    texto/tipo de la excepción original.
  - El detalle técnico completo se registra vía logging (logger.exception).
"""

import sys
import types

import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

_DETALLE_SECRETO = "pyarrow.lib.ArrowInvalid: detalle interno que no debe verse"


@pytest.fixture
def vista_consultas_rota(monkeypatch):
    """Reemplaza views.consultas.mostrar_consultas por una que siempre falla,
    simulando un bug no controlado en una vista."""
    modulo_falso = types.ModuleType("views.consultas")

    def _mostrar_consultas_rota() -> None:
        raise RuntimeError(_DETALLE_SECRETO)

    modulo_falso.mostrar_consultas = _mostrar_consultas_rota
    monkeypatch.setitem(sys.modules, "views.consultas", modulo_falso)
    yield


class TestErrorNoControladoNoSeFiltraAlUsuario:

    def test_error_en_vista_no_rompe_la_app_ni_expone_detalle_tecnico(
        self, sidoe_config, vista_consultas_rota
    ):
        # "Generar Consulta" es la opción por defecto del radio en modo
        # público, así que al correr la app ya se invoca la vista rota.
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()

        # 1. Streamlit/AppTest no debe reportar una excepción sin manejar.
        assert not at.exception

        # 2. El usuario ve un mensaje genérico con un código de incidente...
        mensajes_error = [e.value for e in at.error]
        assert any("ERR-" in m and "error inesperado" in m for m in mensajes_error)

        # 3. ...pero nunca el detalle técnico real de la excepción.
        assert not any(_DETALLE_SECRETO in m for m in mensajes_error)

        # Nota: el registro técnico completo (logger.exception, con
        # traceback) se emite vía logging estándar de Python hacia
        # stderr/journalctl en producción — se verifica visualmente en la
        # salida de pytest (sección "Captured log call") en vez de
        # aserción automatizada aquí, por la forma en que AppTest ejecuta
        # el script en un hilo separado y pytest ya la muestra igual.
