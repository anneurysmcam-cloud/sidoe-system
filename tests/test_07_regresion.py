"""
tests/test_07_regresion.py
===========================
TESTS DE REGRESIÓN

Protegen contra la reaparición de bugs ya identificados y corregidos.
Cada test está anotado con el bug que previene y la fecha de corrección.

Bugs cubiertos:
  [BUG-01] Lógica Subregistro invertida (original: Sí→6.667, corregido: Sí→0)
  [BUG-02] Colisión de subcadena en Estructura de datos (opción b clasificada como a)
  [BUG-03] Texto erróneo C1 Metodología (faltaba punto final en opción "criterio experto")
  [BUG-04] Opción espuria en C2.3 ("No requiere de articulación" no pertenece a C2.3)
  [BUG-05] Sensibilidad a espacios (strings con espacios al borde no se mapeaban)
  [BUG-06] División por cero en C3.2 (req=0 → ZeroDivisionError)
  [BUG-07] Score recalculado se descartaba antes del UPDATE en modificar_indicador
  [BUG-08] Armonización conceptual invertida (Sí debía penalizar, no premiar)
  [BUG-09] Articulación "No requiere de articulación" no daba puntos (faltaba en mapa)
  [BUG-10] ETL leía "Indicadores Duplicados" (D mayúscula) pero el header real del
           Excel oficial es "Indicadores duplicados" (d minúscula) → el campo se
           perdía silenciosamente para las 107 filas con referencias cruzadas
"""

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# [BUG-01] Lógica Subregistro invertida
# ---------------------------------------------------------------------------

class TestRegresionSubregistro:
    """[BUG-01] Presencia de subregistro PENALIZA (Sí→0, No→6.667).
    El bug original devolvía Sí→6.667 y No→0 (invertido).
    """

    def test_subregistro_si_da_0_no_invertido(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"subregistro_cobertura": "Sí"})
        assert r["subregistro_valor"] == 0.0, (
            "[BUG-01 REGRESIÓN] Subregistro 'Sí' debe dar 0 (penalización), no 6.667"
        )

    def test_subregistro_no_da_6_667_no_invertido(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"subregistro_cobertura": "No"})
        assert r["subregistro_valor"] == pytest.approx(6.667, rel=1e-3), (
            "[BUG-01 REGRESIÓN] Subregistro 'No' debe dar 6.667, no 0"
        )


# ---------------------------------------------------------------------------
# [BUG-02] Colisión de subcadena en Estructura de datos
# ---------------------------------------------------------------------------

class TestRegresionEstructuraDatos:
    """[BUG-02] La opción b) contiene la frase 'base de datos estructurada'.
    El bug original usaba subcadena y clasificaba b) como a) → 6.667 en lugar de 3.3335.
    """

    _B = (
        "b) No posee una base de datos estructurada, pero posee un "
        "formato para montar datos (Excel)"
    )

    def test_opcion_b_da_3_3335_no_6_667(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"estructura_datos": self._B})
        assert r["estructura_valor"] == pytest.approx(3.3335, rel=1e-3), (
            f"[BUG-02 REGRESIÓN] Opción b) debe dar 3.3335, obtuvo {r['estructura_valor']}"
        )

    def test_opcion_b_no_da_valor_de_opcion_a(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"estructura_datos": self._B})
        assert r["estructura_valor"] != pytest.approx(6.667, abs=0.1), (
            "[BUG-02 REGRESIÓN] Opción b) no debe clasificarse como opción a)"
        )


# ---------------------------------------------------------------------------
# [BUG-03] Texto erróneo C1 Metodología — punto final obligatorio
# ---------------------------------------------------------------------------

class TestRegresionC1Metodologia:
    """[BUG-03] La opción 'criterio experto' requiere punto final exacto.
    Sin el punto, la cadena no matchea y devuelve 0 en lugar de 7.5.
    """

    def test_criterio_experto_con_punto_da_7_5(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({
            "c1_metodologia": (
                "Indicador sin metodología definida, pero el método de cálculo se puede "
                "establecer mediante criterio experto."
            )
        })
        assert r["c1_valor"] == 7.5, (
            "[BUG-03 REGRESIÓN] Texto 'criterio experto.' (con punto) debe dar 7.5"
        )

    def test_criterio_experto_sin_punto_da_0(self):
        """Sin punto es texto diferente → no matchea → 0."""
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({
            "c1_metodologia": (
                "Indicador sin metodología definida, pero el método de cálculo se puede "
                "establecer mediante criterio experto"  # Sin punto
            )
        })
        assert r["c1_valor"] == 0.0


# ---------------------------------------------------------------------------
# [BUG-04] Opción espuria en C2.3
# ---------------------------------------------------------------------------

class TestRegresionC23:
    """[BUG-04] 'No requiere de articulación' no pertenece a C2.3.
    El bug original incluía esta opción en el mapa de C2.3 dando 10 puntos incorrectamente.
    """

    def test_articulacion_no_aplica_en_c23(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({
            "c23_periodicidad_establecida": "No requiere de articulación"
        })
        assert r["c23_valor"] == 0.0, (
            "[BUG-04 REGRESIÓN] 'No requiere de articulación' no debe dar puntos en C2.3"
        )

    def test_c23_solo_acepta_si_no(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        for val in ["Sí se articula", "Parcialmente", "Completamente", "No identificado"]:
            r = calcular_reglas_factibilidad({"c23_periodicidad_establecida": val})
            assert r["c23_valor"] == 0.0, (
                f"[BUG-04 REGRESIÓN] '{val}' no debe dar puntos en C2.3"
            )


# ---------------------------------------------------------------------------
# [BUG-05] Sensibilidad a espacios al borde
# ---------------------------------------------------------------------------

class TestRegresionWhitespace:
    """[BUG-05] Strings con espacios al borde deben normalizarse antes de mapear."""

    def test_si_con_espacio_izquierda_normalizado(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"c22_disponibilidad": " Sí"})
        assert r["c22_valor"] == 10.0

    def test_si_con_espacio_derecha_normalizado(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"c22_disponibilidad": "Sí "})
        assert r["c22_valor"] == 10.0

    def test_completamente_con_espacios_normalizado(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"c21_existencia_fuente": "  Completamente  "})
        assert r["c21_valor"] == 15.0

    def test_articulacion_con_espacios_normalizado(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"articulacion_fuentes": " Sí se articula "})
        assert r["articulacion_valor"] == pytest.approx(6.667, rel=1e-3)


# ---------------------------------------------------------------------------
# [BUG-06] División por cero en C3.2
# ---------------------------------------------------------------------------

class TestRegresionDivisionPorCero:
    """[BUG-06] req=0 causaba ZeroDivisionError en el cálculo de C3.2."""

    def test_req_cero_disp_cero_no_lanza_excepcion(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        # No debe lanzar excepción
        r = calcular_reglas_factibilidad({
            "num_desagregaciones_requeridas": 0,
            "num_desagregaciones_disponibles": 0,
        })
        assert r["c32_valor"] == 0.0

    def test_req_cero_disp_positivo_no_lanza_excepcion(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({
            "num_desagregaciones_requeridas": 0,
            "num_desagregaciones_disponibles": 3,
        })
        assert isinstance(r["c32_valor"], float)

    def test_req_none_disp_none_no_lanza_excepcion(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        try:
            calcular_reglas_factibilidad({
                "num_desagregaciones_requeridas": None,
                "num_desagregaciones_disponibles": None,
            })
        except Exception as e:
            pytest.fail(f"[BUG-06 REGRESIÓN] Lanzó excepción con None: {e}")


# ---------------------------------------------------------------------------
# [BUG-07] Score recalculado descartado en modificar_indicador
# ---------------------------------------------------------------------------

class TestRegresionScoreDescartado:
    """[BUG-07] El score recalculado se descartaba antes del UPDATE.
    El resultado del engine se calculaba pero no se pasaba al INSERT/UPDATE.
    """

    def test_modificar_actualiza_score_en_bd(self, sidoe_config):
        """Tras modificar factibilidad, el score en BD debe reflejar el nuevo cálculo."""
        from models.crud_indicadores import guardar_indicador, modificar_indicador
        import data.database as db_mod

        fact_max = {
            "c1_metodologia": "Indicador con metodología nacional o internacional definida",
            "c21_existencia_fuente": "Completamente",
            "c22_disponibilidad": "Sí",
            "c23_periodicidad_establecida": "Sí",
            "c31_posee_desagregacion": "Sí",
            "num_desagregaciones_requeridas": 1,
            "num_desagregaciones_disponibles": 1,
            "articulacion_fuentes": "No requiere de articulación",
            "armonizacion_conceptual": "No",
            "subregistro_cobertura": "No",
            "cobertura_territorial": "Sí",
            "estructura_datos": (
                "a) La fuente de información utiliza en el procesamiento "
                "una base de datos estructurada"
            ),
            "variables_calculo": "Sí",
        }
        fact_min = {
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
            "estructura_datos": "c) No posee ninguna de las anteriores",
            "variables_calculo": "No",
        }

        guardar_indicador(
            datos_indicador={
                "codigo": "REG-BUG07-01",
                "indicador": "Test regresión bug 07",
                "estado_indicador": "Activo",
                "generador_demanda_id": 1,
            },
            datos_fuentes=[{"nombre_fuente": "Fuente test"}],
            datos_factibilidad=fact_max,
            usuario_id=1,
        )

        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo='REG-BUG07-01'"
        ).fetchone()[0]
        score_inicial = conn.execute(
            "SELECT score_factibilidad_final FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()[0]
        conn.close()
        assert score_inicial == pytest.approx(100.002, rel=1e-3)

        # Modificar con factibilidad mínima
        modificar_indicador(
            id_indicador=ind_id,
            datos_indicador={},
            datos_factibilidad=fact_min,
            usuario_id=1,
        )

        conn = db_mod.obtener_conexion()
        score_final = conn.execute(
            "SELECT score_factibilidad_final FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()[0]
        conn.close()

        assert score_final == 0.0, (
            f"[BUG-07 REGRESIÓN] Score debería ser 0.0 tras modificar, "
            f"pero es {score_final} — el score recalculado fue descartado"
        )


# ---------------------------------------------------------------------------
# [BUG-08] Armonización conceptual invertida
# ---------------------------------------------------------------------------

class TestRegresionArmonizacion:
    """[BUG-08] 'Sí hay problemas de armonización' debe penalizar (dar 0).
    El bug original daba 6.667 cuando había problemas (invertido).
    """

    def test_armonizacion_si_penaliza(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"armonizacion_conceptual": "Sí"})
        assert r["armonizacion_valor"] == 0.0, (
            "[BUG-08 REGRESIÓN] Armonización 'Sí' debe dar 0 (penalización)"
        )

    def test_armonizacion_no_premia(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"armonizacion_conceptual": "No"})
        assert r["armonizacion_valor"] == pytest.approx(6.667, rel=1e-3), (
            "[BUG-08 REGRESIÓN] Armonización 'No' debe dar 6.667 (sin problemas)"
        )


# ---------------------------------------------------------------------------
# [BUG-09] Articulación "No requiere de articulación" no daba puntos
# ---------------------------------------------------------------------------

class TestRegresionArticulacion:
    """[BUG-09] 'No requiere de articulación' debe dar 6.667 (sin penalización).
    El bug original solo reconocía 'Sí se articula' y dejaba 0 para el resto.
    """

    def test_no_requiere_articulacion_da_6_667(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({
            "articulacion_fuentes": "No requiere de articulación"
        })
        assert r["articulacion_valor"] == pytest.approx(6.667, rel=1e-3), (
            "[BUG-09 REGRESIÓN] 'No requiere de articulación' debe dar 6.667"
        )

    def test_si_se_articula_da_6_667(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"articulacion_fuentes": "Sí se articula"})
        assert r["articulacion_valor"] == pytest.approx(6.667, rel=1e-3)

    def test_no_se_articula_da_0(self):
        from features.engine_factibilidad import calcular_reglas_factibilidad
        r = calcular_reglas_factibilidad({"articulacion_fuentes": "No se articula"})
        assert r["articulacion_valor"] == 0.0


# ---------------------------------------------------------------------------
# Regresión: Integridad de FK con CASCADE
# ---------------------------------------------------------------------------

class TestRegresionIntegridadCascade:
    """Verifica que ON DELETE CASCADE funciona correctamente en todas las tablas dependientes."""

    def test_eliminar_indicador_limpia_todas_las_tablas_dependientes(self, sidoe_config):
        from models.crud_indicadores import guardar_indicador, borrar_indicador
        import data.database as db_mod

        guardar_indicador(
            datos_indicador={
                "codigo": "REG-CASCADE-01",
                "indicador": "Test cascade regresión",
                "estado_indicador": "Activo",
                "generador_demanda_id": 1,
                "_ejes_politicas_extra": [(5, None)],
            },
            datos_fuentes=[
                {"nombre_fuente": "Fuente 1"},
                {"nombre_fuente": "Fuente 2"},
            ],
            datos_factibilidad={
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
                "estructura_datos": "c) No posee ninguna de las anteriores",
                "variables_calculo": "No",
            },
            usuario_id=1,
        )

        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo='REG-CASCADE-01'"
        ).fetchone()[0]
        conn.close()

        borrar_indicador(ind_id, usuario_id=1)

        conn = db_mod.obtener_conexion()
        chequeos = {
            "fuentes_indicador": conn.execute(
                "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
            ).fetchone()[0],
            "calculo_factibilidad": conn.execute(
                "SELECT COUNT(*) FROM calculo_factibilidad WHERE indicador_id=?", (ind_id,)
            ).fetchone()[0],
            "indicador_ejes_politicas": conn.execute(
                "SELECT COUNT(*) FROM indicador_ejes_politicas WHERE indicador_id=?", (ind_id,)
            ).fetchone()[0],
        }
        conn.close()

        for tabla, cnt in chequeos.items():
            assert cnt == 0, (
                f"[REGRESIÓN CASCADE] {tabla} tiene {cnt} filas huérfanas "
                f"para indicador_id={ind_id} ya eliminado"
            )


# ---------------------------------------------------------------------------
# Regresión: código único — no permite duplicados
# ---------------------------------------------------------------------------

class TestRegresionCodigoUnico:

    def test_codigo_duplicado_rechazado_siempre(self, sidoe_config):
        """El constraint UNIQUE en indicadores.codigo nunca debe romperse."""
        from models.crud_indicadores import guardar_indicador

        fact = {
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
            "estructura_datos": "c) No posee ninguna de las anteriores",
            "variables_calculo": "No",
        }

        ok1, _ = guardar_indicador(
            datos_indicador={
                "codigo": "REG-UNIQUE-01",
                "indicador": "Primero",
                "estado_indicador": "Activo",
                "generador_demanda_id": 1,
            },
            datos_fuentes=[{"nombre_fuente": "F1"}],
            datos_factibilidad=fact,
            usuario_id=1,
        )
        assert ok1 is True

        ok2, msg2 = guardar_indicador(
            datos_indicador={
                "codigo": "REG-UNIQUE-01",
                "indicador": "Segundo (debe fallar)",
                "estado_indicador": "Activo",
                "generador_demanda_id": 1,
            },
            datos_fuentes=[{"nombre_fuente": "F2"}],
            datos_factibilidad=fact,
            usuario_id=1,
        )
        assert ok2 is False, (
            "[REGRESIÓN] El segundo INSERT con código duplicado debió fallar"
        )


# ---------------------------------------------------------------------------
# [BUG-10] Mapeo case-sensitive de "Indicadores duplicados" en el ETL
# ---------------------------------------------------------------------------

class TestRegresionETLIndicadoresDuplicados:
    """[BUG-10] El header real del Excel oficial (hoja "Demanda y Oferta",
    fila 3) es "Indicadores duplicados" (d minúscula). El ETL buscaba
    "Indicadores Duplicados" (D mayúscula): como pandas es case-sensitive,
    ``Series.get`` devolvía siempre None y el campo quedaba vacío para las
    107 filas del Excel oficial que sí traen una referencia cruzada
    (ej. "CMV A.1", "ODS 3.4.2").
    """

    def _mock_read_excel(self, monkeypatch, valor_columna: str):
        """Sustituye pd.read_excel por DataFrames mínimos controlados."""
        df_dem = pd.DataFrame([{
            "Código": "REG-BUG10",
            "Indicador": "Indicador de prueba BUG-10",
            "Indicadores duplicados": valor_columna,
        }])
        df_fac = pd.DataFrame([{
            "Código": "REG-BUG10",
        }])

        def _fake_read_excel(ruta, sheet_name, header):
            return df_dem if sheet_name == "Demanda y Oferta" else df_fac

        monkeypatch.setattr("data.migraciones_historicas.ETL_migracion.pd.read_excel", _fake_read_excel)

    def test_indicadores_duplicados_se_mapea_con_header_real(
        self, monkeypatch, tmp_path
    ):
        """Con el header real (d minúscula), el valor debe llegar a guardar_indicador."""
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")  # solo necesita existir en disco

        self._mock_read_excel(monkeypatch, "CMV A.1")
        monkeypatch.setattr(etl, "resolver_o_crear_id", lambda *a, **k: 1)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)

        etl.migrar_historico_excel(archivo_excel=str(archivo_fantasma))

        assert capturado.get("indicadores_duplicados") == "CMV A.1", (
            "[BUG-10 REGRESIÓN] El ETL no mapeó 'Indicadores duplicados' del Excel "
            "(¿volvió el mismatch de mayúsculas 'Indicadores Duplicados'?)"
        )

    def test_indicadores_duplicados_vacio_cuando_no_hay_referencia(
        self, monkeypatch, tmp_path
    ):
        """Fila sin referencia cruzada (mayoría del Excel) debe migrar vacío, no error."""
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")

        self._mock_read_excel(monkeypatch, None)
        monkeypatch.setattr(etl, "resolver_o_crear_id", lambda *a, **k: 1)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)

        etl.migrar_historico_excel(archivo_excel=str(archivo_fantasma))

        assert capturado.get("indicadores_duplicados") == "", (
            "[BUG-10 REGRESIÓN] Fila sin referencia cruzada debe migrar cadena vacía"
        )
