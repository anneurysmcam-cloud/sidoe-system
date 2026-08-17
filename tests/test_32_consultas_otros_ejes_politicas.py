"""
tests/test_32_consultas_otros_ejes_politicas.py
================================================
TEST DE REGRESIÓN — Consultas: columna 'otros_ejes_politicas' del export a Excel.

Contexto del bug reportado:
  El export a Excel de Consultas mostraba tanto las columnas 'eje' /
  'politica_gobierno' (el par principal) como 'ejes_politicas_todos' (TODOS
  los pares, incluyendo ese mismo principal). Para el caso común de un
  indicador con un solo eje/política, la información quedaba duplicada:
  'eje'='Eje 1: Institucional', 'ejes_politicas_todos'='Eje 1: Institucional
  / Política 1.1'.

Fix: la columna se renombra a 'otros_ejes_politicas' y se calcula desde la
  nueva vista SQL 'ejes_politicas_secundarios_por_indicador', que excluye
  explícitamente (por ID, no por texto) el par que coincide con el
  eje_id/politica_gobierno_id principal del indicador. Así:
  - Un indicador con un solo eje/política -> columna vacía (None/NaN).
  - Un indicador con varios pares -> columna solo trae el 2do en adelante,
    nunca repite el principal.
"""

import pandas as pd

from models.crud_auxiliares import opciones_selectbox
from models.crud_indicadores import guardar_indicador
from views.consultas import _query_indicadores

DATOS_FACTIBILIDAD_MINIMA = {
    "c1_metodologia": "No cumple con los criterios anteriores",
    "c21_existencia_fuente": "No hay fuente",
    "c22_disponibilidad": "No",
    "c23_periodicidad_establecida": "No",
    "c31_posee_desagregacion": "No",
    "num_desagregaciones_requeridas": 0,
    "num_desagregaciones_disponibles": 0,
    "articulacion_fuentes": "No se articula",
    "armonizacion_conceptual": "Sí",
    "subregistro_cobertura": "Sí",
    "cobertura_territorial": "No",
    "estructura_datos": "No posee ninguna de las anteriores",
    "variables_calculo": "No",
}


def _crear_indicador(
    codigo: str, eje_id, politica_id, ejes_politicas_extra=None
) -> None:
    datos = {
        "codigo": codigo,
        "indicador": f"Indicador {codigo}",
        "estado_indicador": "Activo",
        "generador_demanda_id": 1,
        "eje_id": eje_id,
        "politica_gobierno_id": politica_id,
    }
    if ejes_politicas_extra:
        datos["_ejes_politicas_extra"] = ejes_politicas_extra
    ok, msg = guardar_indicador(
        datos_indicador=datos,
        datos_fuentes=[],
        datos_factibilidad=DATOS_FACTIBILIDAD_MINIMA,
        usuario_id=1,
    )
    assert ok, f"No se pudo crear el indicador de prueba {codigo}: {msg}"


def _fila_consultas(codigo: str) -> pd.Series:
    from data.database import obtener_conexion

    conn = obtener_conexion()
    try:
        df = pd.read_sql_query(_query_indicadores(es_publico=False), conn)
    finally:
        conn.close()
    filas = df[df["codigo"] == codigo]
    assert len(filas) == 1, f"Se esperaba 1 fila para {codigo}, hubo {len(filas)}"
    return filas.iloc[0]


class TestOtrosEjesPoliticasConsultas:
    def test_indicador_con_un_solo_eje_no_repite_info_en_otros_ejes_politicas(
        self, sidoe_config
    ):
        """Punto 4: un indicador con un único par eje/política no debe traer
        nada en 'otros_ejes_politicas' — ese único par ya está en las
        columnas 'eje' y 'politica_gobierno'."""
        _, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        eje_id = next(iter(mapa_eje.values()))
        politica_id = next(iter(mapa_politica.values()))

        _crear_indicador("P32-UNICO", eje_id, politica_id)

        fila = _fila_consultas("P32-UNICO")
        assert pd.isna(fila["otros_ejes_politicas"]) or fila["otros_ejes_politicas"] in (
            None, ""
        ), (
            "otros_ejes_politicas debería quedar vacío cuando el indicador "
            f"solo tiene un eje/política, pero trajo: {fila['otros_ejes_politicas']!r}"
        )

    def test_indicador_con_varios_ejes_politicas_solo_trae_los_secundarios(
        self, sidoe_config
    ):
        """Punto 4: un indicador con varios pares eje/política debe traer en
        'otros_ejes_politicas' los pares adicionales (2do en adelante), pero
        NUNCA el par principal (para no duplicar lo que ya está en las
        columnas 'eje'/'politica_gobierno')."""
        textos_eje, mapa_eje = opciones_selectbox("eje")
        textos_politica, mapa_politica = opciones_selectbox("politica_gobierno")
        assert len(textos_eje) >= 2 and len(textos_politica) >= 2, (
            "Se necesitan al menos 2 valores de eje/política sembrados para "
            "esta prueba de regresión."
        )

        eje_principal_texto, eje_secundario_texto = textos_eje[0], textos_eje[1]
        pol_principal_texto, pol_secundario_texto = textos_politica[0], textos_politica[1]
        eje_principal_id = mapa_eje[eje_principal_texto]
        pol_principal_id = mapa_politica[pol_principal_texto]
        eje_secundario_id = mapa_eje[eje_secundario_texto]
        pol_secundario_id = mapa_politica[pol_secundario_texto]

        _crear_indicador(
            "P32-MULTI",
            eje_principal_id,
            pol_principal_id,
            ejes_politicas_extra=[(eje_secundario_id, pol_secundario_id)],
        )

        fila = _fila_consultas("P32-MULTI")
        otros = fila["otros_ejes_politicas"] or ""

        par_principal = f"{eje_principal_texto} / {pol_principal_texto}"
        par_secundario = f"{eje_secundario_texto} / {pol_secundario_texto}"

        assert par_secundario in otros, (
            f"El par secundario '{par_secundario}' debería aparecer en "
            f"otros_ejes_politicas, pero el valor fue: {otros!r}"
        )
        assert par_principal not in otros, (
            f"El par principal '{par_principal}' NO debería repetirse en "
            f"otros_ejes_politicas (ya está en las columnas eje/politica_gobierno), "
            f"pero el valor fue: {otros!r}"
        )
