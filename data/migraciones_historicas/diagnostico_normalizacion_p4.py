"""
data/migraciones_historicas/diagnostico_normalizacion_p4.py
=============================================================
Reubicado desde ``data/diagnostico_normalizacion_p4.py`` junto con los
demás scripts de un solo uso (Hallazgo #10 del informe de revisión de
código de agosto 2026).

Script de DIAGNÓSTICO (solo lectura, no modifica nada) para el punto 4 de
la lista de pendientes: convertir 'institucion_productora' y
'nombre_fuente' (fuentes_indicador) y 'area_misional_one' (indicadores)
en variables categóricas vía Auxiliares.

Como hoy son texto libre y ya hay ~855 indicadores / ~1,077 fuentes en
producción, es probable que existan variaciones de escritura (mayúsculas,
espacios extra, acentos, abreviaturas) para lo que institucionalmente es
el mismo valor. Este script agrupa los valores existentes por una forma
normalizada (sin importar mayúsculas/espacios) para detectar esos casos
ANTES de diseñar el catálogo Auxiliar y la migración de backfill — así
evitamos crear entradas duplicadas en el Auxiliar.

USO (ejecutar contra una COPIA de la base de datos, nunca la real):
    python data/migraciones_historicas/diagnostico_normalizacion_p4.py [ruta_a_la_bd_copia.db]

Si no se pasa ruta, usa config.DB_PATH (la BD configurada por defecto).

Salida: para cada uno de los 3 campos, lista los grupos donde existe más
de una variante de escritura para lo que parece ser el mismo valor,
mostrando cada variante tal cual está guardada y cuántas filas la usan.
También reporta valores NULL/vacíos y el conteo total de valores únicos.

No requiere Streamlit ni ninguna dependencia fuera de la librería estándar
+ sqlite3, así que puede correr en cualquier máquina con Python 3.10+.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict


def _normalizar(valor: str) -> str:
    """Forma normalizada para AGRUPAR (no para guardar): minúsculas, sin
    acentos, espacios múltiples colapsados, sin espacios al borde."""
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", valor)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto.strip().lower())
    return texto


def _reportar_campo(cursor: sqlite3.Cursor, tabla: str, columna: str, titulo: str) -> None:
    filas = cursor.execute(
        f"SELECT {columna}, COUNT(*) FROM {tabla} GROUP BY {columna}"
    ).fetchall()

    total_filas = sum(c for _, c in filas)
    nulos_o_vacios = sum(c for v, c in filas if v is None or str(v).strip() == "")
    valores_no_vacios = [(v, c) for v, c in filas if v is not None and str(v).strip() != ""]

    print(f"\n{'=' * 70}")
    print(f"{titulo}  ({tabla}.{columna})")
    print(f"{'=' * 70}")
    print(f"Total de filas: {total_filas}")
    print(f"Filas NULL/vacías: {nulos_o_vacios}")
    print(f"Valores distintos (tal cual guardados, sin normalizar): {len(valores_no_vacios)}")

    grupos: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for valor, cuenta in valores_no_vacios:
        grupos[_normalizar(valor)].append((valor, cuenta))

    grupos_con_variantes = {k: v for k, v in grupos.items() if len(v) > 1}

    if not grupos_con_variantes:
        print("No se detectaron variantes de escritura para el mismo valor normalizado.")
        return

    print(
        f"\n⚠️  {len(grupos_con_variantes)} valores tienen MÁS DE UNA variante de "
        f"escritura (candidatos a normalizar antes de crear el Auxiliar):\n"
    )
    # Ordenado por impacto: el grupo con más filas totales afectadas primero
    for _, variantes in sorted(
        grupos_con_variantes.items(), key=lambda kv: -sum(c for _, c in kv[1])
    ):
        variantes_ordenadas = sorted(variantes, key=lambda vc: -vc[1])
        total_grupo = sum(c for _, c in variantes_ordenadas)
        print(f"  · Grupo ({total_grupo} filas en total):")
        for valor, cuenta in variantes_ordenadas:
            print(f"      {cuenta:>5}  {valor!r}")


def main() -> None:
    if len(sys.argv) > 1:
        ruta_bd = sys.argv[1]
    else:
        from config import DB_PATH
        ruta_bd = DB_PATH

    print(f"Analizando (solo lectura): {ruta_bd}")
    print(
        "RECORDATORIO: este script debe correr contra una COPIA de la base de "
        "datos, nunca contra el archivo en uso por la aplicación."
    )

    conn = sqlite3.connect(f"file:{ruta_bd}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        _reportar_campo(
            cursor, "indicadores", "area_misional_one",
            "Área misional ONE",
        )
        _reportar_campo(
            cursor, "fuentes_indicador", "institucion_productora",
            "Institución productora",
        )
        _reportar_campo(
            cursor, "fuentes_indicador", "nombre_fuente",
            "Nombre de la fuente",
        )
    finally:
        conn.close()

    print(f"\n{'=' * 70}")
    print(
        "Fin del diagnóstico. Comparte esta salida completa para diseñar el "
        "mapeo de normalización y el catálogo Auxiliar de cada campo."
    )


if __name__ == "__main__":
    main()
