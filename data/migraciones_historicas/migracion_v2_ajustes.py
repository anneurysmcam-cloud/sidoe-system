"""
data/migraciones_historicas/migracion_v2_ajustes.py
============================
Migración de ajuste sobre una base de datos YA POBLADA:

1. Elimina la columna ioe_status (y su ioe_valor) de calculo_factibilidad.
2. Remapea c22_disponibilidad de valores legados del Engine a los oficiales
   de la matriz (Sí/No).
3. Recalcula score y categoría con el Engine actualizado.

Ejecutar UNA SOLA VEZ sobre producción:
    python -m data.migraciones_historicas.migracion_v2_ajustes

Es seguro re-ejecutar: si el esquema ya está actualizado solo recalcula.
"""

import logging
import os
import shutil
import sqlite3
from datetime import datetime

from config import DB_PATH
from features.engine_factibilidad import calcular_reglas_factibilidad

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Remapeo de valores legados de c22_disponibilidad → vocabulario oficial
_REMAPEO_C22 = {
    "Microtado": "Sí",
    "Microdato": "Sí",
    "Dato estadístico": "Sí",
    "No disponible": "No",
    "No": "No",
    "Sí": "Sí",
    "Si": "Sí",
}

_CAMPOS_CRITERIO = [
    "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
    "c23_periodicidad_establecida", "c31_posee_desagregacion",
    "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
    "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
    "cobertura_territorial", "estructura_datos", "variables_calculo",
]


def _columnas(cursor, tabla: str) -> list[str]:
    """Devuelve la lista de nombres de columna de ``tabla`` según ``PRAGMA table_info``."""
    return [f[1] for f in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]


def migrar() -> None:
    """Ejecuta la migración v2 completa: elimina ``ioe_status`` si aún existe,
    remapea los valores legados de ``c22_disponibilidad`` al vocabulario
    oficial y recalcula score/categoría de todos los indicadores con el
    Engine actualizado. Crea un respaldo de la BD antes de modificar nada
    y es seguro re-ejecutar."""
    if not os.path.exists(DB_PATH):
        logger.error("Base de datos no encontrada: %s", DB_PATH)
        return

    backup = f"{DB_PATH}.bak_v2ajust_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(DB_PATH, backup)
    logger.info("Respaldo creado: %s", backup)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cols_actuales = _columnas(cursor, "calculo_factibilidad")
    tiene_ioe_status = "ioe_status" in cols_actuales

    if tiene_ioe_status:
        logger.info("Reconstruyendo calculo_factibilidad sin ioe_status...")
        conn.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute(
            "ALTER TABLE calculo_factibilidad RENAME TO calculo_factibilidad_v1;"
        )
        cursor.execute("""
            CREATE TABLE calculo_factibilidad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER UNIQUE NOT NULL,
                c1_metodologia TEXT, c21_existencia_fuente TEXT,
                c22_disponibilidad TEXT, c23_periodicidad_establecida TEXT,
                c31_posee_desagregacion TEXT,
                num_desagregaciones_requeridas INTEGER DEFAULT 0,
                num_desagregaciones_disponibles INTEGER DEFAULT 0,
                articulacion_fuentes TEXT, armonizacion_conceptual TEXT,
                subregistro_cobertura TEXT, cobertura_territorial TEXT,
                estructura_datos TEXT, variables_calculo TEXT,
                c1_valor REAL, c21_valor REAL, c22_valor REAL, c23_valor REAL,
                c31_valor REAL, c32_valor REAL, articulacion_valor REAL,
                armonizacion_valor REAL, subregistro_valor REAL,
                cobertura_valor REAL, estructura_valor REAL, variables_valor REAL,
                score_factibilidad_final REAL, categoria_factibilidad TEXT,
                calc_timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (indicador_id) REFERENCES indicadores(id) ON DELETE CASCADE
            )
        """)
        cols_comunes = [
            c for c in _CAMPOS_CRITERIO + [
                "indicador_id", "c1_valor", "c21_valor", "c22_valor", "c23_valor",
                "c31_valor", "c32_valor", "articulacion_valor", "armonizacion_valor",
                "subregistro_valor", "cobertura_valor", "estructura_valor", "variables_valor",
                "score_factibilidad_final", "categoria_factibilidad",
            ]
            if c in cols_actuales
        ]
        cursor.execute(
            f"INSERT INTO calculo_factibilidad ({', '.join(cols_comunes)}) "
            f"SELECT {', '.join(cols_comunes)} FROM calculo_factibilidad_v1;"
        )
        cursor.execute("DROP TABLE calculo_factibilidad_v1;")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON;")
        logger.info("Reconstrucción sin ioe_status completada.")

    # Remapeo de c22_disponibilidad
    logger.info("Remapeando c22_disponibilidad a vocabulario oficial...")
    for viejo, nuevo in _REMAPEO_C22.items():
        if viejo != nuevo:
            cursor.execute(
                "UPDATE calculo_factibilidad SET c22_disponibilidad = ? "
                "WHERE c22_disponibilidad = ?",
                (nuevo, viejo),
            )
    conn.commit()

    # Recálculo completo con el Engine actualizado
    logger.info("Recalculando scores con el Engine actualizado...")
    filas = cursor.execute(
        f"SELECT indicador_id, {', '.join(_CAMPOS_CRITERIO)} FROM calculo_factibilidad"
    ).fetchall()

    actualizados = 0
    for fila in filas:
        datos = {campo: fila[campo] for campo in _CAMPOS_CRITERIO}
        resultado = calcular_reglas_factibilidad(datos)
        cursor.execute("""
            UPDATE calculo_factibilidad SET
                c1_valor=?, c21_valor=?, c22_valor=?, c23_valor=?,
                c31_valor=?, c32_valor=?, articulacion_valor=?,
                armonizacion_valor=?, subregistro_valor=?, cobertura_valor=?,
                estructura_valor=?, variables_valor=?,
                score_factibilidad_final=?, categoria_factibilidad=?
            WHERE indicador_id=?
        """, (
            resultado["c1_valor"], resultado["c21_valor"], resultado["c22_valor"],
            resultado["c23_valor"], resultado["c31_valor"], resultado["c32_valor"],
            resultado["articulacion_valor"], resultado["armonizacion_valor"],
            resultado["subregistro_valor"], resultado["cobertura_valor"],
            resultado["estructura_valor"], resultado["variables_valor"],
            resultado["score_factibilidad_final"], resultado["categoria_factibilidad"],
            fila["indicador_id"],
        ))
        actualizados += 1
    conn.commit()
    conn.close()

    logger.info(
        "Migración v2 completada: %d scores recalculados.", actualizados
    )
    print(f"✅ Migración completada: {actualizados} scores recalculados.")


if __name__ == "__main__":
    migrar()
