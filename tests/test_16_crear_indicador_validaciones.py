"""
tests/test_16_crear_indicador_validaciones.py
===============================================
Cobertura de las validaciones del formulario de registro
(views/crear_indicador.py): funciones puras, sin Streamlit ni BD.

Cubre las mejoras solicitadas tras la reunión con la jefa de ONE:
  - Punto 1: consistencia "no hay fuente" -> criterios de factibilidad en "No".
  - Punto 2/3: campos obligatorios y reporte de cuáles quedaron vacíos.
"""

from views.crear_indicador import (
    VALOR_SIN_FUENTE,
    _campos_vacios,
    _errores_consistencia_sin_fuente,
)


# ---------------------------------------------------------------------------
# _campos_vacios (puntos 2 y 3)
# ---------------------------------------------------------------------------

def test_campos_vacios_detecta_none():
    faltantes = _campos_vacios({"Campo A": None, "Campo B": "valor"})
    assert faltantes == ["Campo A"]


def test_campos_vacios_detecta_texto_en_blanco():
    faltantes = _campos_vacios({"Campo A": "   ", "Campo B": "valor"})
    assert faltantes == ["Campo A"]


def test_campos_vacios_sin_faltantes():
    faltantes = _campos_vacios({"Campo A": "valor", "Campo B": 1})
    assert faltantes == []


def test_campos_vacios_lista_todos_los_faltantes_no_solo_el_primero():
    faltantes = _campos_vacios({"A": None, "B": "", "C": "ok", "D": None})
    assert set(faltantes) == {"A", "B", "D"}


def test_campos_vacios_no_marca_valor_numerico_cero_como_vacio():
    # 0 es un valor legítimo (ej. desagregaciones requeridas = 0), no debe
    # tratarse como campo vacío.
    faltantes = _campos_vacios({"Campo numérico": 0})
    assert faltantes == []


# ---------------------------------------------------------------------------
# _errores_consistencia_sin_fuente (punto 1)
# ---------------------------------------------------------------------------

def _criterios_todos_negativos(**overrides):
    base = {
        "c21": VALOR_SIN_FUENTE,
        "c22": "No",
        "c23": "No",
        "c31": "No",
        "art": "No se articula",
        "arm": "No",
        "sub": "No",
        "cob": "No",
        "est": "No posee ninguna de las anteriores",
        "var": "No",
    }
    base.update(overrides)
    return base


def test_sin_fuente_no_valida_si_si_hay_fuente():
    errores = _errores_consistencia_sin_fuente("Completamente", {})
    assert errores == []


def test_sin_fuente_no_valida_si_parcialmente():
    errores = _errores_consistencia_sin_fuente("Parcialmente", {})
    assert errores == []


def test_sin_fuente_todos_negativos_no_da_error():
    errores = _errores_consistencia_sin_fuente(
        VALOR_SIN_FUENTE, _criterios_todos_negativos()
    )
    assert errores == []


def test_sin_fuente_detecta_un_criterio_no_negativo():
    criterios = _criterios_todos_negativos(c22="Sí")
    errores = _errores_consistencia_sin_fuente(VALOR_SIN_FUENTE, criterios)
    assert len(errores) == 1
    assert "C2.2 Disponibilidad/accesibilidad" in errores[0]
    assert "Sí" in errores[0]


def test_sin_fuente_detecta_multiples_criterios_no_negativos():
    criterios = _criterios_todos_negativos(
        c22="Sí", art="Sí se articula", var="No identificada"
    )
    errores = _errores_consistencia_sin_fuente(VALOR_SIN_FUENTE, criterios)
    assert len(errores) == 3


def test_sin_fuente_detecta_c21_inconsistente_con_existencia_de_fuente():
    # exist_txt del componente de Fuente dice "No hay fuente" pero el
    # criterio C2.1 del Engine (tab Factibilidad) quedó en otro valor.
    criterios = _criterios_todos_negativos(c21="Parcialmente")
    errores = _errores_consistencia_sin_fuente(VALOR_SIN_FUENTE, criterios)
    assert any("C2.1 Existencia fuente" in e for e in errores)
