"""
data/migraciones_historicas/fix_auditoria_fk.py
========================
Corrige el FOREIGN KEY de la tabla ``auditoria`` si todavía apunta a
``usuarios_old`` (residuo de un renombrado previo de tabla con ALTER TABLE
... RENAME TO en SQLite, que reescribe el SQL de las tablas dependientes con
el nombre antiguo).

Ejecutar UNA SOLA VEZ sobre la BD de producción:
    python -m data.migraciones_historicas.fix_auditoria_fk

Es seguro re-ejecutar: si el FK ya está correcto, no hace nada.
"""

import logging
import os
import shutil
import sqlite3
from datetime import datetime

from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _sql_de_auditoria(cursor) -> str:
    """Devuelve el SQL de creación (``CREATE TABLE ...``) registrado para la
    tabla ``auditoria`` en ``sqlite_master``, o cadena vacía si no existe."""
    fila = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='auditoria'"
    ).fetchone()
    return fila[0] if fila else ""


def corregir() -> None:
    """Reconstruye la tabla ``auditoria`` con el FOREIGN KEY apuntando a
    ``usuarios`` (en vez de ``usuarios_old``), preservando todos los
    registros existentes. No hace nada si el FK ya es correcto. Crea un
    respaldo de la BD antes de modificarla."""
    if not os.path.exists(DB_PATH):
        logger.error("No se encontró la base de datos: %s", DB_PATH)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    sql_actual = _sql_de_auditoria(cursor)
    if "usuarios_old" not in sql_actual:
        logger.info("El FK de auditoria ya es correcto. No se requiere acción.")
        conn.close()
        return

    backup_path = f"{DB_PATH}.bak_fixfk_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(DB_PATH, backup_path)
    logger.info("Respaldo creado en: %s", backup_path)

    conn.execute("PRAGMA foreign_keys = OFF;")
    logger.info("Reconstruyendo tabla auditoria con FK correcto (→ usuarios)...")

    cursor.execute("ALTER TABLE auditoria RENAME TO auditoria_old;")
    cursor.execute("""
        CREATE TABLE auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            detalle TEXT,
            timestamp DATETIME DEFAULT (datetime('now')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    """)
    cursor.execute("INSERT INTO auditoria SELECT * FROM auditoria_old;")
    cursor.execute("DROP TABLE auditoria_old;")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.close()
    logger.info("FK de auditoria corregido exitosamente.")


if __name__ == "__main__":
    corregir()
