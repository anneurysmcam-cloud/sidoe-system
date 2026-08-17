"""
tests/test_10_utils_helpers.py
===============================
Cobertura de utils/helpers.py (funciones puras, sin BD ni Streamlit).
"""

import pandas as pd

from utils.helpers import (
    formatear_fecha,
    generar_codigo,
    limpiar_texto,
    normalizar_columnas,
    porcentaje,
    resumen_dataframe,
    validar_campos,
    validar_dataframe,
)


def test_validar_campos_todos_validos():
    assert validar_campos(["a", "b", 1, True]) is True


def test_validar_campos_con_none_o_vacio():
    assert validar_campos(["a", None]) is False
    assert validar_campos(["a", "   "]) is False
    assert validar_campos([]) is True


def test_formatear_fecha_valida():
    assert formatear_fecha("2026-07-12") == "12/07/2026"


def test_formatear_fecha_invalida_devuelve_original():
    assert formatear_fecha("no-es-fecha") == "no-es-fecha"


def test_limpiar_texto_normaliza_espacios():
    assert limpiar_texto("  hola   mundo  ") == "hola mundo"


def test_limpiar_texto_none_devuelve_vacio():
    assert limpiar_texto(None) == ""


def test_porcentaje_formatea_correctamente():
    assert porcentaje(0.856) == "85.6%"
    assert porcentaje(0.5, decimales=0) == "50%" or porcentaje(0.5, decimales=0) == "50.0%"


def test_porcentaje_valor_invalido_devuelve_na():
    assert porcentaje("no-numero") == "N/A"


def test_normalizar_columnas():
    df = pd.DataFrame({"Nombre Completo": [1], " Otra Col ": [2]})
    resultado = normalizar_columnas(df)
    assert "nombre_completo" in resultado.columns
    assert "otra_col" in resultado.columns


def test_validar_dataframe_vacio_y_none():
    assert validar_dataframe(None) is False
    assert validar_dataframe(pd.DataFrame()) is False
    assert validar_dataframe(pd.DataFrame({"a": [1]})) is True


def test_generar_codigo_formato():
    codigo = generar_codigo()
    assert codigo.startswith("IND-")
    partes = codigo.split("-")
    assert len(partes) == 3


def test_generar_codigo_prefijo_personalizado():
    codigo = generar_codigo(prefix="FTE")
    assert codigo.startswith("FTE-")


def test_resumen_dataframe():
    df = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
    resumen = resumen_dataframe(df)
    assert resumen["total_registros"] == 3
    assert resumen["columnas"] == ["col1", "col2"]
    assert len(resumen["primeras_filas"]) == 3
