"""
tests/test_11_utils_backup.py
==============================
Cobertura de utils/backup.py: backup consistente vía sqlite3 backup API,
rotación de backups antiguos y listado. Todo se ejecuta sobre archivos en
tmp_path, nunca sobre la BD de producción.
"""

import os
import sqlite3
import time

import pytest

from utils.backup import crear_backup_rotado, listar_backups


def _crear_db_minima(ruta: str) -> None:
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('dato')")
    conn.commit()
    conn.close()


def test_crear_backup_rotado_archivo_inexistente(tmp_path):
    ruta_falsa = str(tmp_path / "no_existe.db")
    with pytest.raises(FileNotFoundError):
        crear_backup_rotado(db_path=ruta_falsa)


def test_crear_backup_rotado_crea_archivo_valido(tmp_path):
    db_path = str(tmp_path / "prueba.db")
    _crear_db_minima(db_path)

    backup_path = crear_backup_rotado(db_path=db_path, max_backups=7)

    assert os.path.exists(backup_path)
    conn = sqlite3.connect(backup_path)
    fila = conn.execute("SELECT v FROM t").fetchone()
    conn.close()
    assert fila[0] == "dato"


def test_rotacion_elimina_backups_antiguos(tmp_path):
    db_path = str(tmp_path / "rotacion.db")
    _crear_db_minima(db_path)

    rutas = []
    for _ in range(5):
        rutas.append(crear_backup_rotado(db_path=db_path, max_backups=3))
        time.sleep(1.1)  # el timestamp tiene resolución de segundos

    backups_actuales = listar_backups(db_path=db_path)
    assert len(backups_actuales) <= 3
    # Los backups más recientes deben seguir existiendo
    nombres_actuales = {b["nombre"] for b in backups_actuales}
    assert os.path.basename(rutas[-1]) in nombres_actuales


def test_listar_backups_vacio_sin_backups(tmp_path):
    db_path = str(tmp_path / "sin_backups.db")
    _crear_db_minima(db_path)
    assert listar_backups(db_path=db_path) == []


def test_listar_backups_incluye_metadata(tmp_path):
    db_path = str(tmp_path / "meta.db")
    _crear_db_minima(db_path)
    crear_backup_rotado(db_path=db_path)

    backups = listar_backups(db_path=db_path)
    assert len(backups) == 1
    b = backups[0]
    assert set(b.keys()) == {"nombre", "ruta", "tamaño_mb", "fecha"}
    assert b["tamaño_mb"] >= 0
