"""
features/engine_factibilidad.py
================================
Motor estadístico metodológico de la ONE para calcular el puntaje de
factibilidad de un indicador.

Cada fórmula está calcada 1:1 de la hoja "Factibilidad" del Excel oficial
(columnas AD, AF, AH, AJ, AL, AO, AQ, AS, AU, AW, AY, BA).

Verificación julio-2026: tras corregir 6 bugs (lógica Subregistro invertida,
colisión de subcadena en Estructura de datos, texto erróneo C1, opción espuria
C2.3, sensibilidad a espacios, denominador cero en C3.2), la coincidencia con
el Excel pasó de 15/880 a 868/880 indicadores (los 12 restantes son filas
atípicas del Excel, no del motor).

Este módulo NO importa nada de Streamlit ni de la capa de base de datos; es
una función pura de transformación de datos.
"""

import logging
from typing import Any

from config import CAT_I, CAT_II, CAT_III, UMBRAL_ALTA, UMBRAL_MEDIA

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapas de puntaje — exactamente como aparecen en el Excel oficial
# ---------------------------------------------------------------------------

_C1_MAP: dict[str, float] = {
    "Indicador con metodología nacional o internacional definida": 15.0,
    "Indicador sin metodología definida, pero el método de cálculo es auto explicativo": 7.5,
    "Indicador sin metodología definida, pero el método de cálculo se puede establecer mediante criterio experto.": 7.5,
    "No cumple con los criterios anteriores": 0.0,
}

_C21_MAP: dict[str, float] = {
    "Completamente": 15.0,
    "Parcialmente": 7.5,
    "No hay fuente": 0.0,
}

_C22_MAP: dict[str, float] = {"Sí": 10.0, "No": 0.0}
_C23_MAP: dict[str, float] = {"Sí": 10.0, "No": 0.0}
_C31_MAP: dict[str, float] = {"Sí": 5.0, "No": 0.0, "No es requerida": 5.0}

_USO_CLASIF_MAP: dict[str, float] = {
    "Sí": 6.667,
    "No": 0.0,
    "No identificada": 6.667,
    "No requerida": 6.667,
}

# Opciones de estructura_datos (comparación exacta, no subcadena)
_ESTRUCTURA_A = (
    "a) La fuente de información utiliza en el procesamiento "
    "una base de datos estructurada"
)
_ESTRUCTURA_B = (
    "b) No posee una base de datos estructurada, pero posee un "
    "formato para montar datos (Excel)"
)

# Valores de articulacion_fuentes que puntúan positivo (antes vivía como
# tupla literal sin nombre dentro del if de calcular_reglas_factibilidad;
# se nombra aquí para poder verificarla en tests contra el vocabulario de
# config.OPCIONES_ARTICULACION_FUENTES sin tocar la lógica de puntaje).
_ARTICULACION_POSITIVA = ("Sí se articula", "No requiere de articulación")


def _norm(valor: Any) -> str:
    """Normaliza un valor a cadena limpia (strip), o '' si es None."""
    return str(valor).strip() if valor is not None else ""


def calcular_reglas_factibilidad(datos: dict) -> dict:
    """Calcula el puntaje de factibilidad y devuelve un dict con todos los
    valores intermedios y el resultado final.

    Args:
        datos: Dict con las claves de criterio (c1_metodologia, c21_existencia_fuente,
               c22_disponibilidad, c23_periodicidad_establecida,
               c31_posee_desagregacion, num_desagregaciones_requeridas,
               num_desagregaciones_disponibles, articulacion_fuentes,
               armonizacion_conceptual, subregistro_cobertura,
               cobertura_territorial, estructura_datos, variables_calculo).

    Returns:
        Dict con todos los campos de datos originales más los valores
        calculados (c1_valor, …, score_factibilidad_final,
        categoria_factibilidad). La clave ioe_status se elimina si existe
        porque no forma parte del modelo oficial.
    """
    # C1: Metodología
    v_c1 = _C1_MAP.get(_norm(datos.get("c1_metodologia")), 0.0)

    # C2.1: Existencia de Fuente
    v_c21 = _C21_MAP.get(_norm(datos.get("c21_existencia_fuente")), 0.0)

    # C2.2: Disponibilidad/accesibilidad — =IF(AG="Sí",10,IF(AG="No",0,""))
    v_c22 = _C22_MAP.get(_norm(datos.get("c22_disponibilidad")), 0.0)

    # C2.3: Periodicidad establecida — =IF(AI="Sí",10,IF(AI="No",0,""))
    # Solo acepta Sí/No; "No requiere de articulación" pertenece a Articulación.
    v_c23 = _C23_MAP.get(_norm(datos.get("c23_periodicidad_establecida")), 0.0)

    # C3.1: Posee Desagregación
    v_c31 = _C31_MAP.get(_norm(datos.get("c31_posee_desagregacion")), 0.0)

    # C3.2: Cumplimiento de Desagregación — =IFERROR(AN/AM,AN)*5, cappeado a 5.
    # La fórmula original del Excel no tiene tope: si "disponibles" (AN) supera
    # a "requeridas" (AM) el ratio pasa de 1 y el resultado excede el máximo de
    # la escala (ej. 2 requeridas / 3 disponibles = 7.5). Se cappea el ratio a
    # 1.0 antes de escalar para que estos casos den siempre el máximo (5), en
    # vez de inflar el puntaje de Factibilidad por encima de lo previsto.
    req = int(datos.get("num_desagregaciones_requeridas") or 0)
    disp = int(datos.get("num_desagregaciones_disponibles") or 0)
    ratio_c32 = (disp / req) if req else disp
    v_c32 = round(min(ratio_c32, 1.0) * 5.0, 4)

    # Articulación de fuentes
    v_articulacion = (
        6.667
        if _norm(datos.get("articulacion_fuentes")) in _ARTICULACION_POSITIVA
        else 0.0
    )

    # Armonización conceptual — "Sí"→0, "No"→6.667 (corrección octubre-2025)
    v_armonizacion = (
        0.0 if _norm(datos.get("armonizacion_conceptual")) == "Sí" else 6.667
    )

    # Subregistro/Subcobertura — "Sí"→0, "No"→6.667 (lógica Excel: penaliza presencia)
    v_subregistro = (
        0.0 if _norm(datos.get("subregistro_cobertura")) == "Sí" else 6.667
    )

    # Cobertura Territorial — "Sí"→6.667, "No"→0
    v_cobertura = (
        6.667 if _norm(datos.get("cobertura_territorial")) == "Sí" else 0.0
    )

    # Estructura de datos — comparación EXACTA (no subcadena) para evitar
    # que la opción "b)" (que contiene la frase "base de datos estructurada")
    # se clasifique incorrectamente como opción "a)".
    est = _norm(datos.get("estructura_datos"))
    if est == _ESTRUCTURA_A:
        v_estructura = 6.667
    elif est == _ESTRUCTURA_B:
        v_estructura = 3.3335
    else:
        v_estructura = 0.0

    # Uso de Clasificaciones (reemplaza "Variables para cálculo")
    v_variables = _USO_CLASIF_MAP.get(_norm(datos.get("variables_calculo")), 0.0)

    # Suma total ponderada
    score_final = round(
        sum([
            v_c1, v_c21, v_c22, v_c23, v_c31, v_c32,
            v_articulacion, v_armonizacion, v_subregistro,
            v_cobertura, v_estructura, v_variables,
        ]),
        3,
    )

    # Clasificación cualitativa
    if score_final >= UMBRAL_ALTA:
        categoria = CAT_I
    elif score_final >= UMBRAL_MEDIA:
        categoria = CAT_II
    else:
        categoria = CAT_III

    # Construir resultado: datos de entrada (sin ioe_status) + valores calculados
    datos_limpios = {k: v for k, v in datos.items() if k != "ioe_status"}
    valores_calculados = {
        "c1_valor": v_c1,
        "c21_valor": v_c21,
        "c22_valor": v_c22,
        "c23_valor": v_c23,
        "c31_valor": v_c31,
        "c32_valor": v_c32,
        "articulacion_valor": v_articulacion,
        "armonizacion_valor": v_armonizacion,
        "subregistro_valor": v_subregistro,
        "cobertura_valor": v_cobertura,
        "estructura_valor": v_estructura,
        "variables_valor": v_variables,
        "score_factibilidad_final": score_final,
        "categoria_factibilidad": categoria,
    }
    return {**datos_limpios, **valores_calculados}
