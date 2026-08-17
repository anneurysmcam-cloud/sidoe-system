"""
tests/test_44_config_variables_entorno.py
===========================================
Cobertura del Hallazgo C del Informe de Auditoría Arquitectónica (agosto
2026): soporte de variables de entorno en config.py para DB_PATH,
UMBRAL_ELIMINACIONES_AUTOBLOQUEO y SIDOE_ENV.

Verifica dos cosas:
1. Sin variables de entorno definidas, el comportamiento es idéntico al
   valor fijo anterior (default preservado).
2. Con la variable de entorno definida, config.py la respeta.

Se reimporta el módulo tras (des)establecer las variables de entorno
porque config.py las lee una sola vez, a nivel de módulo, al importarse.
"""

import importlib
import os

import config as config_mod


def _reimportar_config():
    return importlib.reload(config_mod)


def test_db_path_default_sin_variable_de_entorno(monkeypatch):
    monkeypatch.delenv("SIDOE_DB_PATH", raising=False)
    cfg = _reimportar_config()
    assert cfg.DB_PATH == os.path.join(cfg.BASE_DIR, "sidoe.db")


def test_db_path_respeta_variable_de_entorno(monkeypatch, tmp_path):
    ruta_custom = str(tmp_path / "otra.db")
    monkeypatch.setenv("SIDOE_DB_PATH", ruta_custom)
    cfg = _reimportar_config()
    assert cfg.DB_PATH == ruta_custom


def test_umbral_eliminaciones_default_sin_variable_de_entorno(monkeypatch):
    monkeypatch.delenv("SIDOE_UMBRAL_ELIMINACIONES_AUTOBLOQUEO", raising=False)
    cfg = _reimportar_config()
    assert cfg.UMBRAL_ELIMINACIONES_AUTOBLOQUEO == 5


def test_umbral_eliminaciones_respeta_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("SIDOE_UMBRAL_ELIMINACIONES_AUTOBLOQUEO", "9")
    cfg = _reimportar_config()
    assert cfg.UMBRAL_ELIMINACIONES_AUTOBLOQUEO == 9


def test_sidoe_env_default_es_dev(monkeypatch):
    monkeypatch.delenv("SIDOE_ENV", raising=False)
    cfg = _reimportar_config()
    assert cfg.SIDOE_ENV == "dev"


def test_sidoe_env_respeta_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("SIDOE_ENV", "produccion")
    cfg = _reimportar_config()
    assert cfg.SIDOE_ENV == "produccion"


def teardown_module(_module):
    # Dejar config.py en su estado por defecto para no afectar otros
    # módulos de test que se importan después en el mismo proceso pytest.
    os.environ.pop("SIDOE_DB_PATH", None)
    os.environ.pop("SIDOE_UMBRAL_ELIMINACIONES_AUTOBLOQUEO", None)
    os.environ.pop("SIDOE_ENV", None)
    _reimportar_config()
