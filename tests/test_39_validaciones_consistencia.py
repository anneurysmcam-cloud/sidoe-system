"""tests/test_39_validaciones_consistencia.py
===============================================
Cobertura de views/_validaciones_consistencia.py: funciones puras, sin
Streamlit ni BD. Cubre el "formulario de consistencia" enviado por Randy
(agosto-2026): reglas de coherencia entre campos relacionados del
formulario de indicadores, complementarias a las ya cubiertas en
test_16_crear_indicador_validaciones.py (_campos_vacios,
_errores_consistencia_sin_fuente).
"""

from views._validaciones_consistencia import (
    errores_desagregacion,
    errores_ficha_tecnica_metodologia,
    errores_fuente_sin_fuente,
    errores_ioe_ra_cuestionario_global,
    errores_metodo_calculo,
    errores_requerimiento_clasificacion,
)

# ---------------------------------------------------------------------------
# errores_requerimiento_clasificacion
# ---------------------------------------------------------------------------

def test_req_clasificacion_si_con_especificar_y_uso_valido_no_da_error():
    assert errores_requerimiento_clasificacion("Si", "Clasificación XYZ", "Sí") == []
    assert errores_requerimiento_clasificacion("Si", "Clasificación XYZ", "No") == []


def test_req_clasificacion_si_sin_especificar_da_error():
    errores = errores_requerimiento_clasificacion("Si", "", "Sí")
    assert any("Especificar clasificación" in e for e in errores)


def test_req_clasificacion_si_con_uso_no_requerida_da_error():
    errores = errores_requerimiento_clasificacion("Si", "Algo", "No requerida")
    assert any("Uso de Clasificaciones" in e for e in errores)


def test_req_clasificacion_no_con_todo_cerrado_no_da_error():
    assert errores_requerimiento_clasificacion("No", "", "No requerida") == []


def test_req_clasificacion_no_con_especificar_lleno_da_error():
    errores = errores_requerimiento_clasificacion("No", "Algo", "No requerida")
    assert any("debe quedar vacío" in e for e in errores)


def test_req_clasificacion_no_con_uso_distinto_da_error():
    errores = errores_requerimiento_clasificacion("No", "", "Sí")
    assert any("No requerida" in e for e in errores)


def test_req_clasificacion_no_identificada_consistente_no_da_error():
    assert errores_requerimiento_clasificacion("No identificada", "", "No identificada") == []


def test_req_clasificacion_no_identificada_inconsistente_da_error():
    errores = errores_requerimiento_clasificacion("No identificada", "Algo", "Sí")
    assert len(errores) == 2  # especificar lleno + uso distinto


# ---------------------------------------------------------------------------
# errores_fuente_sin_fuente
# ---------------------------------------------------------------------------

def _fuente_no_aplica(**overrides):
    base = {
        "nombre_f_txt": "No aplica", "tipo_txt": "No aplica",
        "inst_f_txt": "No aplica", "per_f_txt": "No aplica",
        "ioe_txt": "No", "ra_txt": "No", "cal_txt": "No aplica",
        "hiper_f": "No aplica", "anio_f": "No aplica",
        "sexo_f_txt": "No aplica", "edad_f_txt": "No aplica",
        "terr_f_txt": "No aplica", "disc_f_txt": "No aplica", "ning_f_txt": "No aplica",
    }
    base.update(overrides)
    return base


def test_fuente_sin_fuente_no_valida_si_hay_fuente():
    assert errores_fuente_sin_fuente("Completamente", **_fuente_no_aplica()) == []


def test_fuente_sin_fuente_todo_no_aplica_no_da_error():
    assert errores_fuente_sin_fuente("No hay fuente", **_fuente_no_aplica()) == []


def test_fuente_sin_fuente_detecta_campo_no_forzado():
    errores = errores_fuente_sin_fuente("No hay fuente", **_fuente_no_aplica(tipo_txt="Encuesta"))
    assert any("Tipo de fuente" in e for e in errores)


def test_fuente_sin_fuente_detecta_ioe_ra_no_forzados():
    errores = errores_fuente_sin_fuente("No hay fuente", **_fuente_no_aplica(ioe_txt="Si", ra_txt="Si"))
    assert any("IOE" in e for e in errores)
    assert any("RA" in e for e in errores)


# ---------------------------------------------------------------------------
# errores_ioe_ra_cuestionario_global
# ---------------------------------------------------------------------------

def test_ioe_ra_cuestionario_global_con_no_no_da_error():
    assert errores_ioe_ra_cuestionario_global("Completamente", "Cuestionario global", "No", "No") == []


def test_ioe_ra_cuestionario_global_con_si_da_error():
    errores = errores_ioe_ra_cuestionario_global("Completamente", "Cuestionario global", "Si", "Si")
    assert len(errores) == 2


def test_ioe_ra_no_aplica_si_tipo_distinto():
    assert errores_ioe_ra_cuestionario_global("Completamente", "Encuesta", "Si", "Si") == []


def test_ioe_ra_no_duplica_si_ya_no_hay_fuente():
    # errores_fuente_sin_fuente ya cubre este caso; esta función se abstiene
    # para no duplicar el mensaje de error.
    assert errores_ioe_ra_cuestionario_global("No hay fuente", "Cuestionario global", "Si", "Si") == []


# ---------------------------------------------------------------------------
# errores_metodo_calculo
# ---------------------------------------------------------------------------

def test_metodo_calculo_definido_no_exige_nada():
    assert errores_metodo_calculo("Definido", "Casos", "Total") == []


def test_metodo_calculo_no_con_campos_vacios_no_da_error():
    assert errores_metodo_calculo("No", "", "") == []
    assert errores_metodo_calculo("No aplica", "", "") == []


def test_metodo_calculo_no_con_campos_llenos_da_error():
    errores = errores_metodo_calculo("No", "Casos", "Total")
    assert len(errores) == 2


# ---------------------------------------------------------------------------
# errores_ficha_tecnica_metodologia
# ---------------------------------------------------------------------------

C1_DEFINIDA = "Indicador con metodología nacional o internacional definida"
C1_EXPERTO = (
    "Indicador sin metodología definida, pero el método de cálculo se puede establecer "
    "mediante criterio experto."
)
C1_NO_CUMPLE = "No cumple con los criterios anteriores"


def test_ficha_definido_exige_c1_definida():
    assert errores_ficha_tecnica_metodologia("Definido", C1_DEFINIDA) == []
    assert errores_ficha_tecnica_metodologia("Definido", C1_NO_CUMPLE) != []


def test_ficha_por_definir_bloquea_definida_y_no_cumple():
    assert errores_ficha_tecnica_metodologia("Por definir", C1_EXPERTO) == []
    assert errores_ficha_tecnica_metodologia("Por definir", C1_DEFINIDA) != []
    assert errores_ficha_tecnica_metodologia("Por definir", C1_NO_CUMPLE) != []


def test_ficha_no_exige_c1_no_cumple():
    assert errores_ficha_tecnica_metodologia("No", C1_NO_CUMPLE) == []
    assert errores_ficha_tecnica_metodologia("No", C1_DEFINIDA) != []


# ---------------------------------------------------------------------------
# errores_desagregacion
# ---------------------------------------------------------------------------

def test_desagregacion_si_con_requerido_mayor_a_cero_no_da_error():
    assert errores_desagregacion("Sí", 2, 0) == []


def test_desagregacion_si_con_requerido_cero_da_error():
    errores = errores_desagregacion("Sí", 0, 0)
    assert any("mayor a 0" in e for e in errores)


def test_desagregacion_si_sin_fuente_exige_disponible_cero():
    # El indicador sí requiere desagregación aunque no exista fuente: se
    # permite req_num > 0, pero disp_num debe ser 0 (dato real).
    assert errores_desagregacion("Sí", 3, 0, exist_txt="No hay fuente") == []
    errores = errores_desagregacion("Sí", 3, 2, exist_txt="No hay fuente")
    assert any("disponibles en la fuente" in e for e in errores)


def test_desagregacion_no_exige_requerido_cero():
    assert errores_desagregacion("No", 0, 5) == []
    errores = errores_desagregacion("No", 1, 0)
    assert any("quedar en 0" in e for e in errores)


def test_desagregacion_no_es_requerida_exige_centinela_uno_con_fuente():
    assert errores_desagregacion("No es requerida", 1, 1) == []
    errores = errores_desagregacion("No es requerida", 0, 0)
    assert errores


def test_desagregacion_no_es_requerida_sin_fuente_exige_disponible_cero():
    assert errores_desagregacion("No es requerida", 1, 0, exist_txt="No hay fuente") == []
    errores = errores_desagregacion("No es requerida", 1, 1, exist_txt="No hay fuente")
    assert any("disponibles en la fuente" in e for e in errores)
