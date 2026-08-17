"""
tests/test_28_utils_archivar_auditoria.py
===========================================
Cobertura de utils/archivar_auditoria.py: export CSV + purga de registros
de auditoría más antiguos que el umbral de retención. Todo se ejecuta sobre
archivos en tmp_path, nunca sobre la BD de producción.
"""

import csv
import os
import sqlite3

import pytest

from utils.archivar_auditoria import archivar_auditoria_antigua


def _crear_db_con_auditoria(ruta: str) -> None:
    conn = sqlite3.connect(ruta)
    conn.execute(
        "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, username TEXT)"
    )
    conn.execute(
        "CREATE TABLE auditoria (id INTEGER PRIMARY KEY, usuario_id INTEGER, "
        "accion TEXT, detalle TEXT, timestamp TEXT)"
    )
    conn.execute("INSERT INTO usuarios (id, username) VALUES (1, 'randy')")
    # 3 registros "antiguos" (hace 1000 días) y 2 "recientes" (hoy).
    for i in range(3):
        conn.execute(
            "INSERT INTO auditoria (usuario_id, accion, detalle, timestamp) "
            "VALUES (1, 'CREAR', ?, datetime('now', '-1000 days'))",
            (f"antiguo-{i}",),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO auditoria (usuario_id, accion, detalle, timestamp) "
            "VALUES (1, 'CREAR', ?, datetime('now'))",
            (f"reciente-{i}",),
        )
    conn.commit()
    conn.close()


def test_archivo_inexistente_lanza_error(tmp_path):
    ruta_falsa = str(tmp_path / "no_existe.db")
    with pytest.raises(FileNotFoundError):
        archivar_auditoria_antigua(db_path=ruta_falsa)


def test_sin_registros_elegibles_no_crea_csv(tmp_path):
    db_path = str(tmp_path / "sin_antiguos.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE usuarios (id INTEGER PRIMARY KEY, username TEXT)")
    conn.execute(
        "CREATE TABLE auditoria (id INTEGER PRIMARY KEY, usuario_id INTEGER, "
        "accion TEXT, detalle TEXT, timestamp TEXT)"
    )
    conn.commit()
    conn.close()

    ruta_csv, cantidad = archivar_auditoria_antigua(db_path=db_path, dias_retencion=730)
    assert ruta_csv is None
    assert cantidad == 0


def test_archiva_solo_registros_mas_antiguos_que_retencion(tmp_path):
    db_path = str(tmp_path / "prueba.db")
    _crear_db_con_auditoria(db_path)

    ruta_csv, cantidad = archivar_auditoria_antigua(
        db_path=db_path, dias_retencion=730, directorio_salida=str(tmp_path)
    )

    assert cantidad == 3
    assert os.path.exists(ruta_csv)

    with open(ruta_csv, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 3
    assert all("antiguo-" in fila["detalle"] for fila in filas)

    # Las filas archivadas ya no deben estar en la tabla activa; las
    # recientes sí deben permanecer intactas.
    conn = sqlite3.connect(db_path)
    restantes = conn.execute("SELECT detalle FROM auditoria ORDER BY detalle").fetchall()
    conn.close()
    detalles_restantes = {fila[0] for fila in restantes}
    assert detalles_restantes == {"reciente-0", "reciente-1"}


def test_es_idempotente_en_segunda_corrida(tmp_path):
    db_path = str(tmp_path / "prueba2.db")
    _crear_db_con_auditoria(db_path)

    archivar_auditoria_antigua(db_path=db_path, dias_retencion=730, directorio_salida=str(tmp_path))
    ruta_csv_2, cantidad_2 = archivar_auditoria_antigua(
        db_path=db_path, dias_retencion=730, directorio_salida=str(tmp_path)
    )

    assert ruta_csv_2 is None
    assert cantidad_2 == 0
