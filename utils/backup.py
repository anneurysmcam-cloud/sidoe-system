"""
utils/backup.py
===============
Utilidades de backup automático de la base de datos SQLite de SIDOE.

Estrategia:
- Copia el archivo ``.db`` a ``<db_path>.bak_YYYYMMDD_HHMMSS`` usando la API
  de SQLite Online Backup (``conn.backup()``) para garantizar consistencia
  incluso con WAL activo y escrituras concurrentes.
- Mantiene solo los ``MAX_BACKUPS`` backups más recientes, eliminando los más
  antiguos automáticamente (rotación).
- Diseñado para ejecutarse periódicamente desde el scheduler de ONE (cron,
  systemd timer) o manualmente desde la UI de administración.

Uso desde CLI:
    python -m utils.backup

Uso desde código:
    from utils.backup import crear_backup_rotado
    ruta = crear_backup_rotado()
"""

import glob
import logging
import os
import sqlite3
from datetime import datetime

from config import DB_PATH

logger = logging.getLogger(__name__)

# Número máximo de archivos de backup a mantener
MAX_BACKUPS: int = 7


def crear_backup_rotado(
    db_path: str = DB_PATH,
    max_backups: int = MAX_BACKUPS,
) -> str:
    """Crea un backup consistente de la BD y elimina los más antiguos.

    Usa ``sqlite3.Connection.backup()`` que es seguro con WAL y lecturas/
    escrituras simultáneas — no hace una simple copia de archivo.

    Args:
        db_path:     Ruta al archivo SQLite de origen.
        max_backups: Número máximo de backups a conservar.

    Returns:
        Ruta absoluta del archivo de backup recién creado.

    Raises:
        FileNotFoundError: Si ``db_path`` no existe.
        OSError: Si no hay espacio o permisos para escribir el backup.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak_{timestamp}"

    logger.info("Iniciando backup: '%s' → '%s'", db_path, backup_path)

    # Backup consistente vía la API de SQLite (safe con WAL)
    src_conn = sqlite3.connect(db_path)
    dst_conn = sqlite3.connect(backup_path)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    # Ajustar permisos del backup (solo owner puede leer)
    if os.name != "nt":
        import stat
        os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)

    logger.info("Backup creado correctamente: '%s'", backup_path)

    # Rotación: eliminar backups que excedan max_backups
    _rotar_backups(db_path, max_backups)

    return backup_path


def _rotar_backups(db_path: str, max_backups: int) -> None:
    """Elimina los backups más antiguos si se supera el límite.

    Args:
        db_path:     Ruta base del archivo ``.db`` (sin el sufijo ``.bak_*``).
        max_backups: Número máximo de archivos de backup a conservar.
    """
    patron = f"{db_path}.bak_*"
    backups = sorted(glob.glob(patron))   # orden lexicográfico = cronológico

    exceso = len(backups) - max_backups
    if exceso <= 0:
        return

    for ruta_antigua in backups[:exceso]:
        try:
            os.remove(ruta_antigua)
            logger.info("Backup antiguo eliminado (rotación): '%s'", ruta_antigua)
        except OSError as exc:
            logger.warning("No se pudo eliminar backup '%s': %s", ruta_antigua, exc)


def listar_backups(db_path: str = DB_PATH) -> list[dict]:
    """Devuelve lista de backups existentes con nombre, tamaño y fecha.

    Args:
        db_path: Ruta base del archivo ``.db``.

    Returns:
        Lista de dicts con claves ``nombre``, ``ruta``, ``tamaño_mb``, ``fecha``.
    """
    patron = f"{db_path}.bak_*"
    resultado = []
    for ruta in sorted(glob.glob(patron), reverse=True):
        try:
            stat = os.stat(ruta)
            resultado.append({
                "nombre": os.path.basename(ruta),
                "ruta": ruta,
                "tamaño_mb": round(stat.st_size / 1_048_576, 2),
                "fecha": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except OSError:
            pass
    return resultado


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ruta = crear_backup_rotado()
    print(f"Backup creado: {ruta}")
    print("\nBackups disponibles:")
    for b in listar_backups():
        print(f"  {b['fecha']}  {b['tamaño_mb']} MB  {b['nombre']}")
