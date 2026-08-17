"""
tests/test_38_eliminacion_masiva_ti.py
=========================================
Script data/migraciones_historicas/eliminacion_masiva_indicadores.py —
protocolo de eliminación masiva para TI, por fuera de la UI y del
auto-bloqueo de supervisor (ver tests/test_37_...).
"""

import importlib
import sys

import pytest

from models.crud_auxiliares import opciones_selectbox
from models.crud_indicadores import guardar_indicador
from security.auth import registrar_usuario

DATOS_FACTIBILIDAD_MINIMA = {
    "c1_metodologia": "No cumple con los criterios anteriores",
    "c21_existencia_fuente": "No hay fuente",
    "c22_disponibilidad": "No",
    "c23_periodicidad_establecida": "No",
    "c31_posee_desagregacion": "No",
    "num_desagregaciones_requeridas": 0,
    "num_desagregaciones_disponibles": 0,
    "articulacion_fuentes": "No se articula",
    "armonizacion_conceptual": "Sí",
    "subregistro_cobertura": "Sí",
    "cobertura_territorial": "No",
    "estructura_datos": "No posee ninguna de las anteriores",
    "variables_calculo": "No",
}


def _crear_usuario(username: str, rol: str) -> int:
    from data.database import obtener_conexion
    registrar_usuario(username, "ClaveSegura123!", rol=rol)
    conn = obtener_conexion()
    uid = conn.execute(
        "SELECT id FROM usuarios WHERE username = ?", (username,)
    ).fetchone()[0]
    conn.close()
    return uid


def _crear_indicador(codigo: str) -> int:
    from data.database import obtener_conexion
    _, mapa_eje = opciones_selectbox("eje")
    _, mapa_politica = opciones_selectbox("politica_gobierno")
    datos = {
        "codigo": codigo, "indicador": f"Indicador {codigo}",
        "estado_indicador": "Activo", "estado_publicacion": "publicado",
        "generador_demanda_id": 1,
        "eje_id": next(iter(mapa_eje.values())),
        "politica_gobierno_id": next(iter(mapa_politica.values())),
    }
    ok, msg = guardar_indicador(
        datos_indicador=datos, datos_fuentes=[],
        datos_factibilidad=DATOS_FACTIBILIDAD_MINIMA, usuario_id=1,
    )
    assert ok, msg
    conn = obtener_conexion()
    iid = conn.execute(
        "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
    ).fetchone()[0]
    conn.close()
    return iid


@pytest.fixture
def script_main(sidoe_config):
    """Importa el script DESPUÉS de que sidoe_config ya parchó
    obtener_conexion/DB_PATH, para que sus imports a nivel de módulo
    (`from config import DB_PATH`, `from data.database import
    obtener_conexion`) capturen las versiones ya parcheadas — mismo
    cuidado que el resto de la suite con módulos de importación tardía
    (ver el comentario largo en tests/conftest.py sobre este mismo tema)."""
    nombre_mod = "data.migraciones_historicas.eliminacion_masiva_indicadores"
    sys.modules.pop(nombre_mod, None)
    mod = importlib.import_module(nombre_mod)
    yield mod
    sys.modules.pop(nombre_mod, None)


def _correr_con_argv(mod, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eliminacion_masiva_indicadores.py", *argv])
    return mod.main()


class TestEliminacionMasivaTI:
    def test_simulacion_no_toca_la_bd(self, sidoe_config, script_main, monkeypatch):
        uid_admin = _crear_usuario("p38_admin_a", "administrador")
        _crear_indicador("P38-A1")
        _crear_indicador("P38-A2")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_admin), "--codigos", "P38-A1", "P38-A2"],
            monkeypatch,
        )
        assert codigo == 0

        from data.database import obtener_conexion
        conn = obtener_conexion()
        restantes = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo IN ('P38-A1', 'P38-A2')"
        ).fetchone()[0]
        conn.close()
        assert restantes == 2, "El modo simulación no debe eliminar nada."

    def test_confirmar_elimina_y_crea_backup(self, sidoe_config, script_main, monkeypatch):
        from config import DB_PATH
        import glob

        uid_admin = _crear_usuario("p38_admin_b", "administrador")
        _crear_indicador("P38-B1")
        _crear_indicador("P38-B2")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_admin), "--codigos", "P38-B1", "P38-B2", "--confirmar"],
            monkeypatch,
        )
        assert codigo == 0

        from data.database import obtener_conexion
        conn = obtener_conexion()
        restantes = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo IN ('P38-B1', 'P38-B2')"
        ).fetchone()[0]
        fila_log = conn.execute(
            "SELECT COUNT(*) FROM auditoria WHERE usuario_id = ? AND accion = 'ELIMINAR'",
            (uid_admin,),
        ).fetchone()[0]
        conn.close()
        assert restantes == 0, "Con --confirmar sí debe eliminar."
        assert fila_log == 2, "Cada eliminación debe quedar en la auditoría."
        assert glob.glob(f"{DB_PATH}.bak_*"), "Debe crear un backup antes de eliminar."

    def test_no_afecta_contador_de_supervisor(self, sidoe_config, script_main, monkeypatch):
        """El punto entero del script: ejecutar por fuera de la UI no debe
        incrementar eliminaciones_recientes de NADIE, ni siquiera si hay
        un supervisor en el sistema con eliminaciones previas cerca del
        umbral."""
        uid_admin = _crear_usuario("p38_admin_c", "administrador")
        uid_supervisor = _crear_usuario("p38_supervisor_c", "supervisor")
        for i in range(6):
            _crear_indicador(f"P38-C{i}")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_admin), "--codigos"]
            + [f"P38-C{i}" for i in range(6)] + ["--confirmar"],
            monkeypatch,
        )
        assert codigo == 0

        from data.database import obtener_conexion
        conn = obtener_conexion()
        activo, contador = conn.execute(
            "SELECT activo, eliminaciones_recientes FROM usuarios WHERE id = ?",
            (uid_supervisor,),
        ).fetchone()
        conn.close()
        assert activo == 1
        assert contador == 0, (
            "Eliminar 6 indicadores (más que el umbral) vía el script de "
            "TI no debe tocar el contador de un supervisor ajeno a la "
            "operación."
        )

    def test_rechaza_usuario_id_que_no_es_administrador(self, sidoe_config, script_main, monkeypatch):
        uid_supervisor = _crear_usuario("p38_supervisor_d", "supervisor")
        _crear_indicador("P38-D1")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_supervisor), "--codigos", "P38-D1", "--confirmar"],
            monkeypatch,
        )
        assert codigo == 1

        from data.database import obtener_conexion
        conn = obtener_conexion()
        restante = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo = 'P38-D1'"
        ).fetchone()[0]
        conn.close()
        assert restante == 1, "No debe eliminar nada si el usuario no es administrador."

    def test_codigos_inexistentes_no_rompen_la_corrida(self, sidoe_config, script_main, monkeypatch):
        uid_admin = _crear_usuario("p38_admin_e", "administrador")
        _crear_indicador("P38-E1")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_admin), "--codigos", "P38-E1", "NO-EXISTE-999", "--confirmar"],
            monkeypatch,
        )
        assert codigo == 0

        from data.database import obtener_conexion
        conn = obtener_conexion()
        restante = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo = 'P38-E1'"
        ).fetchone()[0]
        conn.close()
        assert restante == 0

    def test_lee_codigos_desde_archivo(self, sidoe_config, script_main, monkeypatch, tmp_path):
        uid_admin = _crear_usuario("p38_admin_f", "administrador")
        _crear_indicador("P38-F1")
        _crear_indicador("P38-F2")

        archivo = tmp_path / "codigos.txt"
        archivo.write_text("# comentario\nP38-F1\n\nP38-F2\n", encoding="utf-8")

        codigo = _correr_con_argv(
            script_main,
            ["--usuario-id", str(uid_admin), "--archivo", str(archivo), "--confirmar"],
            monkeypatch,
        )
        assert codigo == 0

        from data.database import obtener_conexion
        conn = obtener_conexion()
        restantes = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo IN ('P38-F1', 'P38-F2')"
        ).fetchone()[0]
        conn.close()
        assert restantes == 0
