"""
tests/test_42_form_indicador_shared_puro.py
=============================================
Tests unitarios de las funciones PURAS extraídas de
views/_form_indicador_shared.py (Hallazgo #2 del informe de revisión de
código de agosto 2026): construir_datos_indicador(), construir_datos_
factibilidad() y construir_datos_fuente().

No requieren streamlit.testing.v1.AppTest ni fixture de BD -- son
funciones puras que arman diccionarios a partir de valores ya leídos del
formulario. La cobertura de integración de estos formularios (con
streamlit) sigue viviendo en test_25_form_indicador_apptest.py.
"""

from views._form_indicador_shared import (
    construir_datos_factibilidad,
    construir_datos_fuente,
    construir_datos_indicador,
)


# ---------------------------------------------------------------------------
# construir_datos_indicador
# ---------------------------------------------------------------------------

def _kwargs_indicador_minimos(**overrides) -> dict:
    base = dict(
        codigo="A.1", estado_indicador="Activo", estado_publicacion="borrador",
        referencias_manuales=[], ejes_politicas_extra=[],
        eje_id=1, politica_gobierno_id=2, generador_demanda_id=3,
        indicador="Nombre del indicador",
        dominio_actividad_estadistica_id=4, subdominio_actividad_estadistica_id=5,
        area_misional_one_id=6, sector_ioe_id=7,
        metodo_calculo_id=8, ficha_tecnica_id=9,
        numerador="Num", denominador="Den", unidad_medida="%",
        requerimiento_clasificacion_id=10, especificar_clasificacion="",
        sexo_id=11, edad_id=12, territorio_id=13, discapacidad_id=14,
        nivel_ingreso_id=15, periodicidad_indicador_id=16,
        ente_responsable_metodologia="ONE", alcance_metodologico_id=17,
    )
    base.update(overrides)
    return base


def test_construir_datos_indicador_incluye_todas_las_claves_esperadas():
    datos = construir_datos_indicador(**_kwargs_indicador_minimos())
    claves_esperadas = {
        "codigo", "estado_indicador", "estado_publicacion",
        "_referencias_manuales", "_ejes_politicas_extra",
        "eje_id", "politica_gobierno_id", "generador_demanda_id", "indicador",
        "dominio_actividad_estadistica_id", "subdominio_actividad_estadistica_id",
        "area_misional_one_id", "sector_ioe_id",
        "metodo_calculo_id", "ficha_tecnica_id",
        "numerador", "denominador", "unidad_medida",
        "requerimiento_clasificacion_id", "especificar_clasificacion",
        "sexo_id", "edad_id", "territorio_id", "discapacidad_id",
        "nivel_ingreso_id", "periodicidad_indicador_id",
        "ente_responsable_metodologia", "alcance_metodologico_id",
    }
    assert set(datos.keys()) == claves_esperadas


def test_construir_datos_indicador_mapea_valores_correctamente():
    datos = construir_datos_indicador(**_kwargs_indicador_minimos(
        codigo="B.2", indicador="Otro nombre", eje_id=99,
    ))
    assert datos["codigo"] == "B.2"
    assert datos["indicador"] == "Otro nombre"
    assert datos["eje_id"] == 99


def test_construir_datos_indicador_preserva_campos_privados_de_orquestacion():
    """_referencias_manuales y _ejes_politicas_extra son campos que
    modificar_indicador()/guardar_indicador() hacen pop() antes de tocar
    la tabla `indicadores` -- deben viajar en el dict tal cual."""
    datos = construir_datos_indicador(**_kwargs_indicador_minimos(
        referencias_manuales=["A.1", "A.2"],
        ejes_politicas_extra=[(1, 2), (3, None)],
    ))
    assert datos["_referencias_manuales"] == ["A.1", "A.2"]
    assert datos["_ejes_politicas_extra"] == [(1, 2), (3, None)]


def test_construir_datos_indicador_exige_kwargs():
    """Todos los parámetros son keyword-only (riesgo de bug silencioso
    con ~28 posicionales) -- llamar posicionalmente debe fallar."""
    import pytest
    with pytest.raises(TypeError):
        construir_datos_indicador("A.1", "Activo")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# construir_datos_factibilidad
# ---------------------------------------------------------------------------

def test_construir_datos_factibilidad_incluye_todas_las_claves_esperadas():
    datos = construir_datos_factibilidad(
        c1_metodologia="M1", c21_existencia_fuente="Sí",
        c22_disponibilidad="Sí", c23_periodicidad_establecida="Sí",
        c31_posee_desagregacion="Sí",
        num_desagregaciones_requeridas=3, num_desagregaciones_disponibles=2,
        articulacion_fuentes="Alta", armonizacion_conceptual="Sí",
        subregistro_cobertura="No", cobertura_territorial="Sí",
        estructura_datos="Estructurado", variables_calculo="Uso de clasificaciones",
    )
    claves_esperadas = {
        "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
        "c23_periodicidad_establecida", "c31_posee_desagregacion",
        "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
        "articulacion_fuentes", "armonizacion_conceptual",
        "subregistro_cobertura", "cobertura_territorial",
        "estructura_datos", "variables_calculo",
    }
    assert set(datos.keys()) == claves_esperadas
    assert datos["num_desagregaciones_requeridas"] == 3
    assert datos["num_desagregaciones_disponibles"] == 2


# ---------------------------------------------------------------------------
# construir_datos_fuente
# ---------------------------------------------------------------------------

def test_construir_datos_fuente_incluye_todas_las_claves_esperadas():
    datos = construir_datos_fuente(
        existencia_fuente_id=1, nombre_fuente_id=2, tipo_fuente_id=3,
        institucion_productora_id=4, periodicidad_id=5,
        sexo_id=6, edad_id=7, territorio_id=8, discapacidad_id=9,
        nivel_ingreso_socioeconomico_id=10,
        ioe_id=11, ra_id=12, calculado_datos_agregados_id=13,
        hipervinculo_ultimo_calculo="https://one.gob.do",
        anio_ultimo_dato_disponible="2025",
        comentarios="Sin comentarios",
    )
    claves_esperadas = {
        "existencia_fuente_id", "nombre_fuente_id", "tipo_fuente_id",
        "institucion_productora_id", "periodicidad_id",
        "sexo_id", "edad_id", "territorio_id", "discapacidad_id",
        "nivel_ingreso_socioeconomico_id",
        "ioe_id", "ra_id", "calculado_datos_agregados_id",
        "hipervinculo_ultimo_calculo", "anio_ultimo_dato_disponible",
        "comentarios",
    }
    assert set(datos.keys()) == claves_esperadas
    assert datos["hipervinculo_ultimo_calculo"] == "https://one.gob.do"


def test_construir_datos_fuente_acepta_ids_nulos_por_campos_opcionales():
    """Muchos selectbox de fuente son opcionales (opcional=True en
    selectbox_auxiliar); el builder no debe validar ni rechazar None."""
    datos = construir_datos_fuente(
        existencia_fuente_id=None, nombre_fuente_id=1, tipo_fuente_id=None,
        institucion_productora_id=None, periodicidad_id=None,
        sexo_id=None, edad_id=None, territorio_id=None, discapacidad_id=None,
        nivel_ingreso_socioeconomico_id=None,
        ioe_id=None, ra_id=None, calculado_datos_agregados_id=None,
        hipervinculo_ultimo_calculo="", anio_ultimo_dato_disponible="",
        comentarios="",
    )
    assert datos["existencia_fuente_id"] is None
    assert datos["nombre_fuente_id"] == 1
