"""
data/migraciones_historicas/migracion_backfill_pnpsp_campos_demanda.py
========================================================================
Migración dirigida sobre una BD YA POBLADA: backfill de tres campos de
indicadores PNPSP-### que el ETL histórico (``data/migraciones_historicas/ETL_migracion.py``,
función ``migrar_pnpsp_faltantes``) leía de la hoja equivocada del Excel.

Motivo
------
"Requerimiento de clasificacion", "Especificar clasificacion" e
"Indicadores duplicados" NO existen en la hoja "Factibilidad" -- solo
viven en la hoja "Demanda y Oferta". La migración PNPSP construye
``datos_indicador`` a partir de ``primera = grupo.iloc[0]``, una fila de
"Factibilidad", así que ``primera.get(...)`` para esas tres columnas
devolvía siempre ``None`` -> cada campo caía al valor por defecto:
"No" para "Requerimiento de clasificacion" (374/374 indicadores PNPSP,
100%) y cadena vacía para los otros dos.

Confirmado contra el Excel oficial: dentro de cada "Orden" (agrupador
PNPSP), estos tres campos son consistentes entre las filas de fuente en
Demanda y Oferta en 366 de 374 casos (98%); para los 8 restantes se toma
la primera fila por orden de aparición y se deja registrado en el log
para revisión manual, siguiendo el mismo criterio que usa el ETL para
el resto de los campos indicador-nivel.

Ver corrección correspondiente en ``data/migraciones_historicas/ETL_migracion.py`` (para que
migraciones futuras desde cero no repitan el problema) y el precedente
de ``migracion_backfill_indicadores_duplicados.py`` (bug distinto,
mismatch de mayúsculas, que además no cubría PNPSP porque agrupa por
"Código" y PNPSP no tiene código individual en el Excel).

Para "Indicadores duplicados" se reutiliza
``sincronizar_indicadores_referenciados()`` (la misma función que usa la
UI al guardar el formulario), igual que hace el backfill hermano, para
que el vínculo quede propagado de forma bidireccional.

Ejecutar UNA SOLA VEZ sobre producción:
    python -m data.migraciones_historicas.migracion_backfill_pnpsp_campos_demanda <ruta_excel_oficial>

Es seguro re-ejecutar: es idempotente (si el valor ya coincide con el
Excel, se salta).
"""

import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

import pandas as pd

from config import DB_PATH
from data.migraciones_historicas.ETL_migracion import _req_clas, _str
from models.crud_auxiliares import resolver_o_crear_id
from models.crud_indicadores import sincronizar_indicadores_referenciados

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def leer_campos_pnpsp_excel(ruta_excel: str) -> dict[str, dict]:
    """Lee la hoja "Demanda y Oferta" y devuelve, por ``codigo`` PNPSP-###,
    los valores crudos de los tres campos afectados, resolviendo el código
    igual que ``migrar_pnpsp_faltantes`` (por posición de "Orden" único,
    ordenado ascendente).
    """
    df_fac = pd.read_excel(ruta_excel, sheet_name="Factibilidad", header=3)
    df_dem = pd.read_excel(ruta_excel, sheet_name="Demanda y Oferta", header=2)
    df_fac.columns = df_fac.columns.str.strip()
    df_dem.columns = df_dem.columns.str.strip()

    df_fac["_gen"] = df_fac["Generador de demanda"].astype(str).str.strip()
    pnpsp = df_fac[df_fac["_gen"] == "PNPSP"].copy()
    pnpsp = pnpsp[pnpsp["Orden"].notna()]

    ordenes_unicos = sorted(pnpsp["Orden"].unique())
    rango_codigo = {orden: idx + 1 for idx, orden in enumerate(ordenes_unicos)}

    resultado: dict[str, dict] = {}
    inconsistentes: list[str] = []

    for orden in ordenes_unicos:
        codigo = f"PNPSP-{rango_codigo[orden]:03d}"
        filas_fuente = df_dem[df_dem["Orden"] == orden]
        if filas_fuente.empty:
            continue

        for campo in ("Requerimiento de clasificacion", "Especificar clasificacion", "Indicadores duplicados"):
            vals = filas_fuente[campo].apply(_str).unique()
            if len([v for v in vals if v]) > 1 or (len(vals) > 1 and any(vals)):
                inconsistentes.append(f"{codigo}.{campo}: {sorted(set(vals))}")

        primera_dem = filas_fuente.iloc[0]
        resultado[codigo] = {
            "requerimiento_clasificacion": _req_clas(primera_dem.get("Requerimiento de clasificacion")),
            "especificar_clasificacion": _str(primera_dem.get("Especificar clasificacion")),
            "indicadores_duplicados": _str(primera_dem.get("Indicadores duplicados")),
        }

    if inconsistentes:
        logger.warning(
            "%d campo(s) con valores distintos entre filas de fuente del mismo Orden "
            "(se usó la primera fila por orden de aparición, revisar manualmente): %s",
            len(inconsistentes), "; ".join(inconsistentes),
        )

    return resultado


def migrar(ruta_excel: str, db_path: str | None = None) -> dict:
    """Ejecuta el backfill completo. ``db_path`` es opcional (por defecto
    ``config.DB_PATH``); parametrizarlo permite testear contra una BD
    temporal sin tocar producción. Devuelve un resumen para logging/tests.
    """
    db_path = db_path or DB_PATH

    if not os.path.exists(db_path):
        logger.error("Base de datos no encontrada: %s", db_path)
        return {"error": "db_no_encontrada"}
    if not os.path.exists(ruta_excel):
        logger.error("Excel oficial no encontrado: %s", ruta_excel)
        return {"error": "excel_no_encontrado"}

    backup = f"{db_path}.bak_backfill_pnpsp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup)
    logger.info("Respaldo creado: %s", backup)

    campos_por_codigo = leer_campos_pnpsp_excel(ruta_excel)
    logger.info("%d código(s) PNPSP con datos de Demanda y Oferta detectados.", len(campos_por_codigo))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    actualizados = ya_al_dia = 0
    no_encontrados: list[str] = []

    for codigo, campos in campos_por_codigo.items():
        fila = cursor.execute(
            "SELECT id, indicador AS nombre_indicador, generador_demanda_id, "
            "especificar_clasificacion, indicadores_duplicados, "
            "requerimiento_clasificacion_id "
            "FROM indicadores WHERE codigo = ?",
            (codigo,),
        ).fetchone()
        if not fila:
            no_encontrados.append(codigo)
            continue

        cambio = False

        nuevo_req_id = resolver_o_crear_id("requerimiento_clasificacion", campos["requerimiento_clasificacion"])
        if nuevo_req_id != fila["requerimiento_clasificacion_id"]:
            cursor.execute(
                "UPDATE indicadores SET requerimiento_clasificacion_id = ? WHERE id = ?",
                (nuevo_req_id, fila["id"]),
            )
            cambio = True

        if (fila["especificar_clasificacion"] or "").strip() != campos["especificar_clasificacion"]:
            cursor.execute(
                "UPDATE indicadores SET especificar_clasificacion = ? WHERE id = ?",
                (campos["especificar_clasificacion"], fila["id"]),
            )
            cambio = True

        if campos["indicadores_duplicados"]:
            m = re.search(r"\b(CMV|ODS|END)\s+([A-Za-z0-9.]+)", campos["indicadores_duplicados"])
            codigo_destino = m.group(2).strip() if m else campos["indicadores_duplicados"].strip()
            if (fila["indicadores_duplicados"] or "").strip() != codigo_destino:
                # No resoluble a un código puntual de forma genérica aquí (el
                # backfill hermano ya resuelve prefijos de generador); PNPSP
                # solo trae 3 referencias no vacías en total y todas apuntan a
                # códigos END con formato "END X.YY", compatible con
                # sincronizar_indicadores_referenciados vía codigos_manuales.
                texto_final = sincronizar_indicadores_referenciados(
                    cursor,
                    indicador_id=fila["id"],
                    codigo=codigo,
                    nombre=fila["nombre_indicador"],
                    generador_demanda_id=fila["generador_demanda_id"],
                    codigos_manuales=[codigo_destino],
                )
                logger.info("codigo=%s -> indicadores_duplicados=%r", codigo, texto_final)
                cambio = True

        if cambio:
            actualizados += 1
        else:
            ya_al_dia += 1

    conn.commit()
    conn.close()

    if no_encontrados:
        logger.warning(
            "%d código(s) PNPSP del Excel no se encontraron en la BD: %s",
            len(no_encontrados), no_encontrados,
        )

    resumen = {
        "actualizados": actualizados,
        "ya_al_dia": ya_al_dia,
        "sin_match": no_encontrados,
        "backup": backup,
    }
    logger.info(
        "Backfill PNPSP completado: %d actualizados, %d ya al día, %d sin match en la BD.",
        actualizados, ya_al_dia, len(no_encontrados),
    )
    print(
        f"✅ Backfill PNPSP completado: {actualizados} actualizados, {ya_al_dia} ya al día, "
        f"{len(no_encontrados)} sin match en la BD (ver log). Respaldo: {backup}"
    )
    return resumen


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m data.migraciones_historicas.migracion_backfill_pnpsp_campos_demanda <ruta_excel_oficial>")
        sys.exit(1)
    migrar(sys.argv[1])
