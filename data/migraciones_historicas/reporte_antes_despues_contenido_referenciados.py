"""
data/migraciones_historicas/reporte_antes_despues_contenido_referenciados.py
=========================================================
Reporte de SOLO LECTURA (no escribe nada en la BD) para revisar, antes de
correr ``data/migraciones_historicas/migracion_backfill_contenido_referenciados.py`` sobre
producción, qué le pasaría exactamente a cada par de indicadores
referenciados: cómo luce el lado que va a ser SOBRESCRITO ("antes") vs.
cómo quedaría después de que se sobrescriba con el contenido del lado que
gana ("después" = una copia exacta de lo que el ganador tiene ahora mismo).

Reutiliza EXACTAMENTE la misma función de decisión que el backfill real
(``data.migracion_backfill_contenido_referenciados.resolver_direccion``):
pares únicos sin dirección, exclusiones conocidas, "gana el de mayor
score_factibilidad_final", y desempate por generador ODS en caso de
empate exacto -- para que este reporte sea un preview fiel de lo que el
backfill va a hacer, no una aproximación con lógica distinta.

Uso:
    python -m data.migraciones_historicas.reporte_antes_despues_contenido_referenciados <ruta_excel_oficial> [ruta_salida.csv]

Si no se indica ruta de salida, se genera
``reporte_backfill_contenido_<timestamp>.csv`` en el directorio actual.
"""

import csv
import os
import sqlite3
import sys
from datetime import datetime

from config import DB_PATH
from data.migraciones_historicas.migracion_backfill_contenido_referenciados import (
    EXCLUSIONES_CONOCIDAS,
    _CAMPOS_CRITERIO,
    _pares_unicos,
    resolver_direccion,
)
from data.migraciones_historicas.migracion_backfill_indicadores_duplicados import (
    leer_referencias_excel,
)

_ENCABEZADOS = [
    "codigo_gana", "indicador_gana",
    "codigo_pierde", "indicador_pierde",
    "estado", "motivo",
    "fuentes_pierde_antes", "fuentes_pierde_despues", "fuentes_cambian",
    "categoria_pierde_antes", "categoria_pierde_despues", "criterios_cambian",
]


def _texto_fuentes(cursor, indicador_id: int) -> str:
    filas = cursor.execute(
        "SELECT nombre_fuente, institucion_productora FROM fuentes_indicador "
        "WHERE indicador_id = ? ORDER BY id",
        (indicador_id,),
    ).fetchall()
    return " | ".join(
        f"{(nombre or '(sin nombre)')} — {(institucion or '(sin institución)')}"
        for nombre, institucion in filas
    ) or "(sin fuentes)"


def _categoria(cursor, indicador_id: int) -> str | None:
    fila = cursor.execute(
        "SELECT categoria_factibilidad FROM calculo_factibilidad WHERE indicador_id = ?",
        (indicador_id,),
    ).fetchone()
    return fila[0] if fila else None


def _criterios(cursor, indicador_id: int):
    fila = cursor.execute(
        f"SELECT {', '.join(_CAMPOS_CRITERIO)} FROM calculo_factibilidad WHERE indicador_id = ?",
        (indicador_id,),
    ).fetchone()
    return tuple(fila) if fila else None


def _fila_vacia(codigo_a: str, codigo_b: str, estado: str, motivo: str = "",
                 nombre_a: str = "", nombre_b: str = "") -> dict:
    return {
        "codigo_gana": codigo_a, "indicador_gana": nombre_a,
        "codigo_pierde": codigo_b, "indicador_pierde": nombre_b,
        "estado": estado, "motivo": motivo,
        "fuentes_pierde_antes": "", "fuentes_pierde_despues": "", "fuentes_cambian": "",
        "categoria_pierde_antes": "", "categoria_pierde_despues": "", "criterios_cambian": "",
    }


def generar_reporte(ruta_excel: str, ruta_salida: str | None = None, db_path: str | None = None) -> str | None:
    db_path = db_path or DB_PATH

    if not os.path.exists(db_path):
        print(f"❌ Base de datos no encontrada: {db_path}")
        return None
    if not os.path.exists(ruta_excel):
        print(f"❌ Excel oficial no encontrado: {ruta_excel}")
        return None

    ruta_salida = ruta_salida or f"reporte_backfill_contenido_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    referencias = leer_referencias_excel(ruta_excel)
    pares = _pares_unicos(referencias)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    filas_reporte = []
    for par in pares:
        codigo_a, codigo_b = sorted(par)

        if par in EXCLUSIONES_CONOCIDAS:
            filas_reporte.append(_fila_vacia(codigo_a, codigo_b, "EXCLUIDO"))
            continue

        fila_a = cursor.execute("SELECT id, indicador FROM indicadores WHERE codigo = ?", (codigo_a,)).fetchone()
        fila_b = cursor.execute("SELECT id, indicador FROM indicadores WHERE codigo = ?", (codigo_b,)).fetchone()
        if not fila_a or not fila_b:
            faltante = codigo_a if not fila_a else codigo_b
            filas_reporte.append(_fila_vacia(
                codigo_a, codigo_b, f"SIN MATCH EN BD ({faltante})", "",
                fila_a["indicador"] if fila_a else "", fila_b["indicador"] if fila_b else "",
            ))
            continue

        decision = resolver_direccion(cursor, codigo_a, codigo_b, fila_a["id"], fila_b["id"])

        if decision["tipo"] == "sin_contenido":
            filas_reporte.append(_fila_vacia(
                codigo_a, codigo_b, "SIN CONTENIDO (ningún lado tiene factibilidad)", "",
                fila_a["indicador"], fila_b["indicador"],
            ))
            continue
        if decision["tipo"] == "ya_al_dia":
            filas_reporte.append(_fila_vacia(
                codigo_a, codigo_b, "YA AL DÍA", "", fila_a["indicador"], fila_b["indicador"],
            ))
            continue
        if decision["tipo"] == "ambiguo":
            filas_reporte.append(_fila_vacia(
                codigo_a, codigo_b, "AMBIGUO (empate, sin desempate posible)", "",
                fila_a["indicador"], fila_b["indicador"],
            ))
            continue

        id_gana, codigo_gana = decision["id_origen"], decision["codigo_origen"]
        codigo_pierde = decision["codigo_destino"]
        id_pierde = fila_b["id"] if codigo_pierde == codigo_b else fila_a["id"]
        nombre_gana = fila_a["indicador"] if codigo_gana == codigo_a else fila_b["indicador"]
        nombre_pierde = fila_b["indicador"] if codigo_pierde == codigo_b else fila_a["indicador"]

        fuentes_antes = _texto_fuentes(cursor, id_pierde)
        fuentes_despues = _texto_fuentes(cursor, id_gana)
        categoria_antes = _categoria(cursor, id_pierde)
        categoria_despues = _categoria(cursor, id_gana)
        criterios_pierde = _criterios(cursor, id_pierde)
        criterios_gana = _criterios(cursor, id_gana)

        filas_reporte.append({
            "codigo_gana": codigo_gana, "indicador_gana": nombre_gana,
            "codigo_pierde": codigo_pierde, "indicador_pierde": nombre_pierde,
            "estado": "OK", "motivo": decision["motivo"],
            "fuentes_pierde_antes": fuentes_antes,
            "fuentes_pierde_despues": fuentes_despues,
            "fuentes_cambian": "SI" if fuentes_antes != fuentes_despues else "NO",
            "categoria_pierde_antes": categoria_antes or "(sin factibilidad calculada)",
            "categoria_pierde_despues": categoria_despues or "(sin factibilidad calculada)",
            "criterios_cambian": "SI" if criterios_pierde != criterios_gana else "NO",
        })

    conn.close()

    with open(ruta_salida, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_ENCABEZADOS)
        writer.writeheader()
        writer.writerows(filas_reporte)

    con_cambios = sum(
        1 for r in filas_reporte
        if r["estado"] == "OK" and (r["fuentes_cambian"] == "SI" or r["criterios_cambian"] == "SI")
    )
    otros_estados = {}
    for r in filas_reporte:
        if r["estado"] != "OK" or (r["fuentes_cambian"] == "NO" and r["criterios_cambian"] == "NO"):
            clave = r["estado"] if r["estado"] != "OK" else "SIN CAMBIOS (ya idéntico)"
            otros_estados[clave] = otros_estados.get(clave, 0) + 1

    resumen_estados = ", ".join(f"{v} {k}" for k, v in otros_estados.items())
    print(
        f"✅ Reporte generado: {ruta_salida} ({len(filas_reporte)} pares, "
        f"{con_cambios} con cambios reales" + (f", {resumen_estados}" if resumen_estados else "") + ")"
    )
    return ruta_salida


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(
            "Uso: python -m data.migraciones_historicas.reporte_antes_despues_contenido_referenciados "
            "<ruta_excel_oficial> [ruta_salida.csv]"
        )
        sys.exit(1)
    ruta_excel_arg = sys.argv[1]
    ruta_salida_arg = sys.argv[2] if len(sys.argv) == 3 else None
    generar_reporte(ruta_excel_arg, ruta_salida_arg)
