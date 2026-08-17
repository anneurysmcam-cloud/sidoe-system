"""
data/migraciones_historicas/migracion_backfill_indicadores_duplicados.py
===================================================
Migración dirigida sobre una BD YA POBLADA (producción, 855 indicadores):
backfill del campo ``indicadores_duplicados``, cruzando por ``codigo``
contra el Excel oficial (hoja "Demanda y Oferta", columna "Indicadores
duplicados").

Motivo
------
El ETL histórico (``data/migraciones_historicas/ETL_migracion.py``) tenía un mismatch de
mayúsculas: buscaba "Indicadores Duplicados" pero el header real del
Excel es "Indicadores duplicados" (d minúscula). Pandas es
case-sensitive, así que el campo se perdió silenciosamente para las
~106 filas del Excel que sí traen una referencia cruzada (ver commit
ce2dc7d, que corrige el ETL para migraciones futuras). Este script
corrige los datos que ya están en producción.

Confirmado con la jefa de Randy en ONE: cuando un indicador está
referenciado, comparte fuente y tratamiento metodológico con el
indicador al que referencia — sin excepciones. Por eso, después del
backfill, este script invoca ``sincronizar_indicadores_referenciados()``
(la MISMA función que usa la UI al guardar el formulario de edición)
sobre cada indicador afectado, para que el vínculo quede propagado de
forma bidireccional exactamente como si un editor lo hubiera guardado
a mano.

Alcance explícito (confirmado con Randy, 2026-07-24): esto NO
deduplica ni agrupa filas para conteos — cada indicador sigue
existiendo por separado en Consultas/Dashboard, ya que cada fila
representa una relación real e independiente indicador-generador de
demanda. Solo se sincroniza el campo de referencia cruzada.

Bug adicional descubierto y corregido en esta misma revisión: la columna
"Indicadores duplicados" no contiene el ``codigo`` literal del destino.
Cada generador usa códigos "pelados" en su propia columna ``Código`` (CMV:
``A.1``, ``A.2``…; ODS: ``1.1.1``…; END: ``1.1``…), pero cuando la
referencia cruza a un indicador de OTRO generador, el Excel antepone el
nombre del generador para desambiguar (ej. ``"CMV A.1"``, a veces con
texto descriptivo extra como ``"Componente ODS 11.a.1"``). El ``codigo``
real en la BD para ese destino es solo ``"A.1"`` / ``"11.a.1"``, sin el
prefijo. La primera versión de este script comparaba el string crudo
contra ``codigo`` y por eso nunca encontraba match. ``PNPSP`` no tiene
códigos individuales en el Excel (su columna Código es literalmente
``"NA"``); una referencia a solo ``"PNPSP"`` sin número no es resoluble a
un indicador puntual y se reporta aparte, no como error.

Ejecutar UNA SOLA VEZ sobre producción:
    python -m data.migraciones_historicas.migracion_backfill_indicadores_duplicados <ruta_excel_oficial>

Es seguro re-ejecutar: es idempotente (si se corre de nuevo contra el
mismo Excel, detecta que ya está sincronizado y no vuelve a escribir).
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
from models.crud_indicadores import sincronizar_indicadores_referenciados

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Generadores conocidos que anteponen su nombre al código cuando la
# referencia cruza de generador (ver docstring del módulo).
_PATRON_REFERENCIA_CON_GENERADOR = re.compile(r"\b(CMV|ODS|END)\s+([A-Za-z0-9.]+)")


def _resolver_codigo_destino(valor_crudo: str) -> str | None:
    """Devuelve el ``codigo`` real (sin prefijo de generador) a partir del
    valor crudo de la columna "Indicadores duplicados", o ``None`` si no es
    resoluble a un indicador puntual (ej. referencia genérica a "PNPSP").
    """
    m = _PATRON_REFERENCIA_CON_GENERADOR.search(valor_crudo)
    if m:
        return m.group(2).strip()
    if valor_crudo.strip().upper() == "PNPSP":
        return None
    # Valor sin prefijo de generador reconocido: se asume que ya es un
    # codigo "pelado" válido (ej. el propio generador de origen).
    return valor_crudo.strip()


def leer_referencias_excel(ruta_excel: str) -> dict[str, str]:
    """Lee la hoja "Demanda y Oferta" y devuelve ``{codigo: codigo_destino}``
    solo para los códigos que traen una referencia cruzada resoluble a un
    indicador puntual (ver ``_resolver_codigo_destino``).

    Agrupa por ``Código`` porque cada indicador puede tener varias filas
    (1:N con fuentes); si las filas de un mismo código traen valores
    distintos en "Indicadores duplicados", se usa el primero por orden
    de aparición y se deja registrado en el log para revisión manual —
    no debería ocurrir con datos consistentes, pero no debe frenar el
    resto del backfill.
    """
    df = pd.read_excel(ruta_excel, sheet_name="Demanda y Oferta", header=2)
    df.columns = df.columns.str.strip()

    referencias: dict[str, str] = {}
    inconsistentes: list[str] = []
    no_resolubles: list[tuple[str, str]] = []

    for codigo, grupo in df.groupby("Código"):
        codigo = str(codigo).strip()
        if not codigo or codigo == "nan":
            continue

        crudos = grupo["Indicadores duplicados"].dropna().astype(str).str.strip()
        crudos = crudos[crudos != ""]
        if crudos.empty:
            continue

        valores_unicos = set(crudos)
        if len(valores_unicos) > 1:
            inconsistentes.append(f"{codigo}: {sorted(valores_unicos)}")
        valor_crudo = crudos.iloc[0]

        codigo_destino = _resolver_codigo_destino(valor_crudo)
        if codigo_destino is None:
            no_resolubles.append((codigo, valor_crudo))
            continue

        referencias[codigo] = codigo_destino

    if inconsistentes:
        logger.warning(
            "%d código(s) con valores distintos entre sus filas de fuente "
            "(se usó el primero por orden de aparición, revisar manualmente): %s",
            len(inconsistentes), "; ".join(inconsistentes),
        )
    if no_resolubles:
        logger.warning(
            "%d código(s) con referencia no resoluble a un indicador puntual "
            "(ej. 'PNPSP' sin número específico, revisar manualmente): %s",
            len(no_resolubles), no_resolubles,
        )

    return referencias


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

    backup = f"{db_path}.bak_backfill_dup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup)
    logger.info("Respaldo creado: %s", backup)

    referencias = leer_referencias_excel(ruta_excel)
    logger.info("%d código(s) con referencia cruzada detectados en el Excel.", len(referencias))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    actualizados, ya_al_dia, no_encontrados = 0, 0, []

    for codigo, valor_ref in referencias.items():
        fila = cursor.execute(
            "SELECT id, indicador, generador_demanda_id, indicadores_duplicados "
            "FROM indicadores WHERE codigo = ?",
            (codigo,),
        ).fetchone()
        if not fila:
            no_encontrados.append(codigo)
            continue

        if (fila["indicadores_duplicados"] or "").strip() == valor_ref:
            ya_al_dia += 1
            continue

        texto_final = sincronizar_indicadores_referenciados(
            cursor,
            indicador_id=fila["id"],
            codigo=codigo,
            nombre=fila["indicador"],
            generador_demanda_id=fila["generador_demanda_id"],
            codigos_manuales=[valor_ref],
        )
        logger.info("codigo=%s -> indicadores_duplicados=%r", codigo, texto_final)
        actualizados += 1

    conn.commit()
    conn.close()

    if no_encontrados:
        logger.warning(
            "%d código(s) del Excel no se encontraron en la BD (indicador no "
            "migrado, o la referencia apunta a algo que no es un código de "
            "indicador individual, ej. 'PNPSP'): %s",
            len(no_encontrados), no_encontrados,
        )

    resumen = {
        "actualizados": actualizados,
        "ya_al_dia": ya_al_dia,
        "sin_match": no_encontrados,
        "backup": backup,
    }
    logger.info(
        "Backfill completado: %d actualizados, %d ya al día, %d sin match en la BD.",
        actualizados, ya_al_dia, len(no_encontrados),
    )
    print(
        f"✅ Backfill completado: {actualizados} actualizados, {ya_al_dia} ya al día, "
        f"{len(no_encontrados)} sin match en la BD (ver log). Respaldo: {backup}"
    )
    return resumen


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m data.migraciones_historicas.migracion_backfill_indicadores_duplicados <ruta_excel_oficial>")
        sys.exit(1)
    migrar(sys.argv[1])
