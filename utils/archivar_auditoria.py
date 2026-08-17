"""
utils/archivar_auditoria.py
============================
Política de retención para la tabla ``auditoria``.

Contexto:
- ``auditoria`` solo crece con el tiempo (cada CREAR/ACTUALIZAR/ELIMINAR de
  indicadores y usuarios agrega una fila; nunca se borra desde la app).
- views/ver_auditoria.py ya pagina a nivel SQL (LIMIT/OFFSET), así que un
  crecimiento indefinido no rompe la vista, pero sigue infando el tamaño del
  archivo .db y el tiempo de los COUNT(*)/backups a largo plazo.

Estrategia (igual de conservadora que utils/backup.py: nunca se pierde
información, solo se mueve):
- Exporta a CSV las filas de auditoría con ``timestamp`` anterior a
  ``dias_retencion`` días.
- Solo después de un export exitoso y verificado, elimina esas filas de la
  tabla ``auditoria``.
- El CSV exportado queda junto a los backups de BD, con el mismo esquema de
  nombre por fecha, para que hereden la misma política de retención de
  disco/backup externo que ya exista en la infraestructura de ONE.

Uso desde CLI (pensado para un cron/systemd timer mensual o trimestral,
NUNCA automático en cada arranque de la app):
    python -m utils.archivar_auditoria --dias 365

Uso desde código:
    from utils.archivar_auditoria import archivar_auditoria_antigua
    ruta_csv, filas_archivadas = archivar_auditoria_antigua(dias_retencion=365)
"""

import argparse
import csv
import logging
import os
import sqlite3
from datetime import datetime

from config import DB_PATH

logger = logging.getLogger(__name__)

# Retención por defecto: 1 año. Umbral confirmado por la jefa de Randy en
# ONE (decisión institucional, no técnica) — reemplaza el valor de partida
# de 730 días usado antes de contar con esa confirmación.
DIAS_RETENCION_DEFAULT: int = 365


def archivar_auditoria_antigua(
    db_path: str = DB_PATH,
    dias_retencion: int = DIAS_RETENCION_DEFAULT,
    directorio_salida: str | None = None,
) -> tuple[str | None, int]:
    """Exporta a CSV y elimina de ``auditoria`` las filas más antiguas que
    ``dias_retencion`` días.

    Args:
        db_path:           Ruta al archivo SQLite.
        dias_retencion:    Días a conservar en la tabla activa; todo lo más
                            antiguo se archiva.
        directorio_salida: Carpeta donde escribir el CSV. Por defecto, la
                            misma carpeta que ``db_path``.

    Returns:
        Tupla ``(ruta_csv, filas_archivadas)``. Si no hay filas que archivar,
        devuelve ``(None, 0)`` y no toca el archivo CSV.

    Raises:
        FileNotFoundError: Si ``db_path`` no existe.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")

    directorio_salida = directorio_salida or os.path.dirname(os.path.abspath(db_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_csv = os.path.join(directorio_salida, f"auditoria_archivada_{timestamp}.csv")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        cursor = conn.execute(
            """
            SELECT a.id, a.timestamp, a.usuario_id, u.username, a.accion, a.detalle
            FROM auditoria a
            LEFT JOIN usuarios u ON u.id = a.usuario_id
            WHERE a.timestamp < datetime('now', ? || ' days')
            ORDER BY a.timestamp ASC
            """,
            (f"-{dias_retencion}",),
        )
        filas = cursor.fetchall()

        if not filas:
            logger.info(
                "Nada que archivar: no hay registros de auditoría con más de %d días.",
                dias_retencion,
            )
            return None, 0

        # 1. Exportar primero — nunca se borra sin haber escrito el archivo.
        with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "timestamp", "usuario_id", "username", "accion", "detalle"])
            writer.writerows(filas)

        # 2. Verificar que el CSV escrito tiene exactamente las filas esperadas
        #    antes de borrar nada — protección contra un disco lleno a mitad
        #    de escritura u otro fallo silencioso.
        with open(ruta_csv, newline="", encoding="utf-8") as f:
            lineas_csv = sum(1 for _ in f) - 1  # -1 por el encabezado
        if lineas_csv != len(filas):
            raise IOError(
                f"Verificación de export falló: se esperaban {len(filas)} filas, "
                f"el CSV tiene {lineas_csv}. No se eliminará nada de la BD."
            )

        # 3. Borrar de la tabla activa solo lo ya confirmado en el CSV.
        ids_archivados = [fila[0] for fila in filas]
        placeholders = ",".join("?" * len(ids_archivados))
        conn.execute(f"DELETE FROM auditoria WHERE id IN ({placeholders})", ids_archivados)
        conn.commit()

        if os.name != "nt":
            import stat
            os.chmod(ruta_csv, stat.S_IRUSR | stat.S_IWUSR)

        logger.info(
            "Archivado completo: %d registro(s) exportado(s) a '%s' y eliminados de auditoria.",
            len(filas), ruta_csv,
        )
        return ruta_csv, len(filas)
    finally:
        conn.close()


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archiva a CSV y purga registros antiguos de la tabla auditoria."
    )
    parser.add_argument(
        "--dias", type=int, default=DIAS_RETENCION_DEFAULT,
        help=f"Días de retención en la tabla activa (default: {DIAS_RETENCION_DEFAULT}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parsear_args()
    ruta, cantidad = archivar_auditoria_antigua(dias_retencion=args.dias)
    if ruta:
        print(f"Archivados {cantidad} registro(s) en: {ruta}")
    else:
        print("No había registros elegibles para archivar.")
