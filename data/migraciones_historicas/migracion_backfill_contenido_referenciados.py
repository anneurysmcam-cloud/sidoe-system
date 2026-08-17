"""
data/migraciones_historicas/migracion_backfill_contenido_referenciados.py
=====================================================
Migración dirigida sobre una BD YA POBLADA: alinea fuente y factibilidad
para los indicadores que el backfill anterior
(``data/migraciones_historicas/migracion_backfill_indicadores_duplicados.py``, commit 89a1178) ya
vinculó vía ``indicadores_duplicados``, pero cuyo CONTENIDO (fuente,
criterios de factibilidad) todavía no estaba sincronizado porque
``sincronizar_contenido_referenciados()`` no existía en ese momento (ver
commit f4d27b7, que la agrega e integra automáticamente a guardar/modificar
indicador desde la UI).

``resolver_direccion()`` es la ÚNICA función que decide, para cada par,
quién gana. Tanto este backfill como
``data.reporte_antes_despues_contenido_referenciados`` la importan y usan
tal cual -- así el reporte es un preview fiel de lo que el backfill real
va a hacer, nunca una aproximación con lógica distinta.

Reglas de resolución (confirmadas con Randy, 2026-07-25)
-----------------------------------------------------------
1. Pares en ``EXCLUSIONES_CONOCIDAS`` no se tocan (ver más abajo).
2. Si ninguno de los dos tiene factibilidad calculada: nada que propagar.
3. Si el ``score_factibilidad_final`` difiere: gana el de mayor puntaje.
4. Si empatan exactamente Y el contenido (fuentes + criterios) ya es
   idéntico: no hay nada que hacer ("ya al día").
5. Si empatan pero el contenido difiere: gana el lado del generador ODS
   (catálogo internacional estándar) sobre CMV/END/PNPSP. Si ninguno de
   los dos es ODS (o -- caso que no debería ocurrir -- ambos lo son),
   queda como "ambiguo" para revisión manual.

Motivo de la regla 3 sobre la regla original ("gana quien aparece
primero en el Excel"): varios pares están registrados en AMBAS
direcciones dentro del propio Excel, así que "el orden en que aparece"
era un accidente de iteración, no una decisión de negocio, y en 5 casos
eso degradaba la categoría de factibilidad del lado "perdedor" según qué
dirección se procesara último.

Exclusiones conocidas
----------------------
``EXCLUSIONES_CONOCIDAS`` documenta pares que el Excel marca como
"Indicadores duplicados" pero que Randy confirmó que NO deben
sincronizarse -- son relaciones parciales (ej. "toma un componente de"),
no duplicados reales. Confirmado en el propio texto del Excel: la fila
de A.24 dice explícitamente "(Toma un componente del 11.a.1 de los
ODS)" -- comparte un componente, no es el mismo indicador. El verdadero
duplicado de "11.a.1" es "G.6" (descripciones prácticamente idénticas).
Esta lista es específica de la versión actual del Excel; el departamento
está preparando una versión limpia de la matriz, así que esta lista
debería revisarse/vaciarse cuando esa versión esté disponible.

Ejecutar UNA SOLA VEZ sobre producción (después del backfill de vínculos):
    python -m data.migraciones_historicas.migracion_backfill_contenido_referenciados <ruta_excel_oficial>

Es seguro re-ejecutar: sincronizar_contenido_referenciados() es idempotente
(reemplaza con el mismo resultado si se corre de nuevo contra datos sin
cambios).
"""

import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from config import DB_PATH
from data.migraciones_historicas.migracion_backfill_indicadores_duplicados import (
    leer_referencias_excel,
)
from models.crud_indicadores import sincronizar_contenido_referenciados

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Ver docstring del módulo. Confirmado con Randy 2026-07-25 contra el texto
# literal del Excel oficial vigente a esa fecha.
EXCLUSIONES_CONOCIDAS: frozenset[frozenset[str]] = frozenset({
    frozenset({"A.24", "11.a.1"}),  # "Toma un componente de", no es duplicado real.
})

# Mismos 13 criterios crudos que toma el Engine (ver
# models.crud_indicadores._CAMPOS_CRITERIO_FACTIBILIDAD). Redefinido aquí
# para que este módulo sea standalone y no dependa de un símbolo privado
# de otro módulo.
_CAMPOS_CRITERIO = (
    "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
    "c23_periodicidad_establecida", "c31_posee_desagregacion",
    "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
    "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
    "cobertura_territorial", "estructura_datos", "variables_calculo",
)


def _pares_unicos(referencias: dict[str, str]) -> set[frozenset[str]]:
    """Colapsa el diccionario {origen: destino} (que puede traer ambas
    direcciones del mismo par) a un set de pares sin dirección."""
    return {frozenset({codigo, destino}) for codigo, destino in referencias.items()}


def _score(cursor, indicador_id: int) -> float | None:
    fila = cursor.execute(
        "SELECT score_factibilidad_final FROM calculo_factibilidad WHERE indicador_id = ?",
        (indicador_id,),
    ).fetchone()
    return fila[0] if fila else None


def _generador(cursor, indicador_id: int) -> str | None:
    """Nombre del generador de demanda (ODS/CMV/END/PNPSP), resuelto vía la
    vista híbrida ``indicadores_resuelto``, sin espacios ni mayúsculas
    inconsistentes (el Excel trae valores como "ODS\\xa0" con espacio raro)."""
    fila = cursor.execute(
        "SELECT generador_demanda FROM indicadores_resuelto WHERE id = ?", (indicador_id,)
    ).fetchone()
    if not fila or fila[0] is None:
        return None
    return fila[0].strip().upper()


def _contenido_identico(cursor, id_a: int, id_b: int) -> bool:
    """True si fuentes y criterios de factibilidad ya son idénticos entre
    ambos indicadores -- usado para distinguir un empate real (score igual
    por coincidencia, contenido distinto) de un par que ya quedó
    sincronizado en una corrida anterior (score igual PORQUE el contenido
    ya es el mismo)."""
    fuentes_a = cursor.execute(
        "SELECT nombre_fuente, institucion_productora FROM fuentes_indicador "
        "WHERE indicador_id = ? ORDER BY id", (id_a,),
    ).fetchall()
    fuentes_b = cursor.execute(
        "SELECT nombre_fuente, institucion_productora FROM fuentes_indicador "
        "WHERE indicador_id = ? ORDER BY id", (id_b,),
    ).fetchall()
    if [tuple(f) for f in fuentes_a] != [tuple(f) for f in fuentes_b]:
        return False

    criterios_a = cursor.execute(
        f"SELECT {', '.join(_CAMPOS_CRITERIO)} FROM calculo_factibilidad WHERE indicador_id = ?",
        (id_a,),
    ).fetchone()
    criterios_b = cursor.execute(
        f"SELECT {', '.join(_CAMPOS_CRITERIO)} FROM calculo_factibilidad WHERE indicador_id = ?",
        (id_b,),
    ).fetchone()
    return (tuple(criterios_a) if criterios_a else None) == (tuple(criterios_b) if criterios_b else None)


def resolver_direccion(cursor, codigo_a: str, codigo_b: str, id_a: int, id_b: int) -> dict:
    """Única fuente de verdad sobre quién gana en un par. Ver reglas en el
    docstring del módulo. Devuelve uno de:
      {"tipo": "sin_contenido"}
      {"tipo": "ya_al_dia"}
      {"tipo": "ambiguo"}
      {"tipo": "gana", "id_origen", "codigo_origen", "codigo_destino", "motivo"}
    """
    score_a, score_b = _score(cursor, id_a), _score(cursor, id_b)

    if score_a is None and score_b is None:
        return {"tipo": "sin_contenido"}

    if score_a == score_b:
        if _contenido_identico(cursor, id_a, id_b):
            return {"tipo": "ya_al_dia"}

        gen_a, gen_b = _generador(cursor, id_a), _generador(cursor, id_b)
        if gen_a == "ODS" and gen_b != "ODS":
            return {"tipo": "gana", "id_origen": id_a, "codigo_origen": codigo_a,
                     "codigo_destino": codigo_b, "motivo": "empate_desempatado_por_ods"}
        if gen_b == "ODS" and gen_a != "ODS":
            return {"tipo": "gana", "id_origen": id_b, "codigo_origen": codigo_b,
                     "codigo_destino": codigo_a, "motivo": "empate_desempatado_por_ods"}
        return {"tipo": "ambiguo"}

    if (score_a or 0) > (score_b or 0):
        return {"tipo": "gana", "id_origen": id_a, "codigo_origen": codigo_a,
                 "codigo_destino": codigo_b, "motivo": "mayor_factibilidad"}
    return {"tipo": "gana", "id_origen": id_b, "codigo_origen": codigo_b,
             "codigo_destino": codigo_a, "motivo": "mayor_factibilidad"}


def migrar(
    ruta_excel: str,
    db_path: str | None = None,
    exclusiones: frozenset[frozenset[str]] = EXCLUSIONES_CONOCIDAS,
) -> dict:
    """Ejecuta el backfill de contenido completo. ``db_path`` es opcional
    (por defecto ``config.DB_PATH``); parametrizarlo permite testear contra
    una BD temporal sin tocar producción. ``exclusiones`` permite pasar una
    lista distinta a la conocida (por defecto ``EXCLUSIONES_CONOCIDAS``).
    Devuelve un resumen para logging/tests.
    """
    db_path = db_path or DB_PATH

    if not os.path.exists(db_path):
        logger.error("Base de datos no encontrada: %s", db_path)
        return {"error": "db_no_encontrada"}
    if not os.path.exists(ruta_excel):
        logger.error("Excel oficial no encontrado: %s", ruta_excel)
        return {"error": "excel_no_encontrado"}

    backup = f"{db_path}.bak_backfill_contenido_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup)
    logger.info("Respaldo creado: %s", backup)

    referencias = leer_referencias_excel(ruta_excel)
    pares = _pares_unicos(referencias)
    logger.info("%d código(s) origen detectados en el Excel -> %d par(es) único(s).",
                len(referencias), len(pares))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    propagados: list[dict] = []
    sin_match: list[list[str]] = []
    sin_contenido: list[list[str]] = []
    ambiguos: list[list[str]] = []
    excluidos: list[list[str]] = []
    ya_al_dia: list[list[str]] = []

    for par in pares:
        codigo_a, codigo_b = sorted(par)

        if par in exclusiones:
            excluidos.append([codigo_a, codigo_b])
            continue

        fila_a = cursor.execute("SELECT id FROM indicadores WHERE codigo = ?", (codigo_a,)).fetchone()
        fila_b = cursor.execute("SELECT id FROM indicadores WHERE codigo = ?", (codigo_b,)).fetchone()
        if not fila_a or not fila_b:
            sin_match.append([codigo_a, codigo_b])
            continue

        decision = resolver_direccion(cursor, codigo_a, codigo_b, fila_a["id"], fila_b["id"])

        if decision["tipo"] == "sin_contenido":
            sin_contenido.append([codigo_a, codigo_b])
            continue
        if decision["tipo"] == "ya_al_dia":
            ya_al_dia.append([codigo_a, codigo_b])
            continue
        if decision["tipo"] == "ambiguo":
            ambiguos.append([codigo_a, codigo_b])
            continue

        destinos = sincronizar_contenido_referenciados(cursor, decision["id_origen"])
        if destinos:
            logger.info(
                "par=(%s, %s) -> gana %s (%s) -> propagado a: %s",
                codigo_a, codigo_b, decision["codigo_origen"], decision["motivo"], destinos,
            )
            propagados.append({
                "origen": decision["codigo_origen"], "destinos": destinos,
                "motivo": decision["motivo"],
            })

    conn.commit()
    conn.close()

    if sin_match:
        logger.warning("%d par(es) con algún código no encontrado en la BD: %s",
                        len(sin_match), sin_match)
    if sin_contenido:
        logger.warning(
            "%d par(es) donde NINGUNO de los dos lados tiene factibilidad "
            "calculada (nada que propagar, revisar manualmente): %s",
            len(sin_contenido), sin_contenido,
        )
    if ambiguos:
        logger.warning(
            "%d par(es) EMPATADOS en score_factibilidad_final, con contenido "
            "distinto, y NINGUNO de los dos lados es ODS (no se pudo aplicar "
            "el desempate) -- no se propagó automáticamente, requieren "
            "decisión manual: %s",
            len(ambiguos), ambiguos,
        )
    if excluidos:
        logger.info("%d par(es) excluidos explícitamente (ver EXCLUSIONES_CONOCIDAS): %s",
                     len(excluidos), excluidos)

    resumen = {
        "propagados": propagados,
        "sin_match": sin_match,
        "sin_contenido": sin_contenido,
        "ambiguos": ambiguos,
        "excluidos": excluidos,
        "ya_al_dia": ya_al_dia,
        "backup": backup,
    }
    logger.info(
        "Backfill de contenido completado: %d par(es) propagados, %d ya al día, "
        "%d sin match, %d sin contenido, %d ambiguos, %d excluidos.",
        len(propagados), len(ya_al_dia), len(sin_match), len(sin_contenido),
        len(ambiguos), len(excluidos),
    )
    print(
        f"✅ Backfill de contenido completado: {len(propagados)} par(es) propagados, "
        f"{len(ya_al_dia)} ya al día, {len(sin_match)} sin match en la BD, "
        f"{len(sin_contenido)} sin contenido, {len(ambiguos)} ambiguos (empate sin "
        f"desempate posible, revisar manualmente), {len(excluidos)} excluidos. "
        f"Respaldo: {backup}"
    )
    return resumen


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m data.migraciones_historicas.migracion_backfill_contenido_referenciados <ruta_excel_oficial>")
        sys.exit(1)
    migrar(sys.argv[1])
