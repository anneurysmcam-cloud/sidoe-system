"""
tests/conftest.py
=================
Fixtures compartidos para toda la suite de tests SIDOE.

Estrategia de aislamiento
--------------------------
- Cada sesión de test opera sobre una copia temporal de sidoe.db en /tmp.
- La función-level fixture ``db_test`` devuelve la ruta de la copia y la
  limpia al finalizar (teardown), garantizando tests idempotentes.
- Nunca se toca la BD de producción.
"""

import os
import shutil
import sqlite3
import sys

import pytest

# Asegurar que el raíz del proyecto está en sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Ruta a la BD de producción (solo lectura — nunca se modifica)
# ---------------------------------------------------------------------------

PROD_DB = os.path.join(PROJECT_ROOT, "sidoe.db")


def _asegurar_prod_db_inicializada() -> None:
    """Garantiza que ``PROD_DB`` exista con el esquema completo antes de que
    la fixture ``db_path`` la copie a un directorio temporal.

    Antes del Hallazgo #4 (informe de revisión de código de agosto 2026),
    ``sidoe.db`` se autocreaba como efecto secundario del primer `import
    data.database` durante la colección de tests de pytest -- ningún test
    dependía de esto explícitamente, simplemente "funcionaba" porque el
    import ocurría antes de que cualquier fixture corriera. Ahora que
    `inicializar_base_datos()` es explícita (ya no se ejecuta al importar),
    este es el único lugar de la suite que debe invocarla: una vez, contra
    la ruta real de ``PROD_DB``, antes de la primera copia a un directorio
    temporal. Si ``PROD_DB`` ya existe (checkout local con datos migrados),
    no se toca -- inicializar_base_datos() es idempotente de todas formas.
    """
    import data.database as db_mod

    db_mod.inicializar_base_datos()


_asegurar_prod_db_inicializada()


# ---------------------------------------------------------------------------
# Fixture: copia temporal de la BD por cada función de test
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Copia sidoe.db a un directorio temporal y devuelve la ruta.

    Al salir del test, la copia se elimina automáticamente (tmp_path es
    gestionado por pytest).

    Además siembra un usuario de prueba con id=1 si no existe: varios flujos
    de escritura (registrar_log) requieren un usuario_id válido por la FK
    auditoria.usuario_id -> usuarios(id). ``PROD_DB`` ya queda inicializada
    con el esquema completo por ``_asegurar_prod_db_inicializada()`` (nivel
    de módulo, arriba) antes de que cualquier test corra, así que sin este
    seed toda operación de escritura con usuario_id=1 fallaría con FOREIGN
    KEY constraint failed. Si PROD_DB ya trae un usuario real con id=1
    (copia de trabajo local con datos migrados), no se toca.
    """
    dest = tmp_path / "sidoe_test.db"
    shutil.copy2(PROD_DB, dest)
    _sembrar_usuario_test(str(dest))
    return str(dest)


def _sembrar_usuario_test(path: str) -> None:
    """Inserta un usuario id=1 idempotente para satisfacer FKs de auditoría en tests."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        existe = conn.execute("SELECT 1 FROM usuarios WHERE id = 1").fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO usuarios (id, username, password_hash, rol, activo) "
                "VALUES (1, '_test_seed_admin', 'no-es-un-hash-valido-solo-test', "
                "'administrador', 1)"
            )
            conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_conn(db_path):
    """Conexión SQLite abierta sobre la BD temporal con FK habilitadas.
    
    Se cierra automáticamente al finalizar el test.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Fixture: parchea DB_PATH de config para que apunte a la copia temporal
# ---------------------------------------------------------------------------

@pytest.fixture
def sidoe_config(db_path, monkeypatch):
    """Redirige TODAS las llamadas a obtener_conexion() a la BD temporal.

    Tras el Hallazgo B (Informe de Auditoría Arquitectónica, agosto 2026),
    todo módulo de producción accede a la conexión vía
    ``from data import database as db_mod`` + ``db_mod.obtener_conexion()``,
    en vez del patrón anterior ``from data.database import obtener_conexion``.
    La diferencia importa para tests: ``from X import Y`` copia la
    referencia a la función en el momento del import, así que parchear
    ``data.database.obtener_conexion`` no afectaba a un módulo que ya se
    hubiera importado con el patrón viejo -- de ahí que antes hiciera falta
    barrer manualmente ``sys.modules`` y parchear cada módulo por separado
    (ver historial de git de este archivo, commit anterior al cierre del
    Hallazgo B, para el barrido completo).

    Con ``db_mod.obtener_conexion()``, en cambio, todo módulo mantiene una
    referencia VIVA al mismo objeto ``data.database`` y resuelve el
    atributo en el momento de la llamada, no del import. Parchear
    ``obtener_conexion`` una sola vez aquí, sobre ``db_mod``, es visible de
    inmediato para absolutamente todo el sistema (``models/*``,
    ``views/*``, ``security/*``, scripts de ``data/migraciones_historicas/``),
    sin necesidad de un barrido de ``sys.modules``.
    """
    import config
    import data.database as db_mod

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)

    def patched_obtener():
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    monkeypatch.setattr(db_mod, "obtener_conexion", patched_obtener)

    # models.crud_auxiliares cachea lecturas de catálogos con @st.cache_data
    # (caché en memoria, vive durante todo el proceso de pytest). Sin esto,
    # un test podría recibir resultados cacheados de la BD temporal de un
    # test anterior en vez de leer la BD temporal propia.
    import models.crud_auxiliares as crud_aux

    crud_aux.listar_categorias.clear()
    crud_aux.obtener_categoria_por_clave.clear()
    crud_aux.obtener_categoria_por_id.clear()
    crud_aux.obtener_valores.clear()

    return db_path


# ---------------------------------------------------------------------------
# Datos de prueba reutilizables
# ---------------------------------------------------------------------------

DATOS_INDICADOR_MINIMO = {
    "codigo": "TEST-001",
    "indicador": "Indicador de prueba automatizada",
    "estado_indicador": "Activo",
    "generador_demanda_id": 1,   # END
    "eje_id": None,
    "politica_gobierno_id": None,
}

DATOS_FACTIBILIDAD_MAX = {
    "c1_metodologia": (
        "Indicador con metodología nacional o internacional definida"
    ),
    "c21_existencia_fuente": "Completamente",
    "c22_disponibilidad": "Sí",
    "c23_periodicidad_establecida": "Sí",
    "c31_posee_desagregacion": "Sí",
    "num_desagregaciones_requeridas": 1,
    "num_desagregaciones_disponibles": 1,
    "articulacion_fuentes": "No requiere de articulación",
    "armonizacion_conceptual": "No",
    "subregistro_cobertura": "No",
    "cobertura_territorial": "Sí",
    "estructura_datos": (
        "a) La fuente de información utiliza en el procesamiento "
        "una base de datos estructurada"
    ),
    "variables_calculo": "Sí",
}

DATOS_FACTIBILIDAD_CERO = {
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
    "estructura_datos": "c) No posee ninguna de las anteriores",
    "variables_calculo": "No",
}

DATOS_FUENTE_MINIMA = {
    "nombre_fuente": "Fuente de prueba",
    "existencia_fuente_id": None,
    "tipo_fuente_id": None,
    "institucion_productora": "Institución TEST",
}
