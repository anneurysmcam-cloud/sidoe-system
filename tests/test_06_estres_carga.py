"""
tests/test_06_estres_carga.py
==============================
TESTS DE ESTRÉS Y CARGA

Validan que el sistema mantiene rendimiento aceptable y estabilidad bajo
condiciones de volumen alto:
  - Creación masiva de indicadores (50 simultáneos)
  - Recálculo masivo del engine (855 indicadores)
  - Consultas pesadas sobre la BD con todos los indicadores
  - Exportación Excel de datasets grandes
  - Múltiples conexiones concurrentes a la BD
  - Integridad referencial bajo carga

Umbrales de rendimiento (configurables en TIMEOUT_*):
  - Creación de 50 indicadores < 30s
  - Recálculo de 855 scores < 5s
  - Consulta de vista resuelto < 3s
  - Exportación Excel 855 filas < 5s
"""

import time
import threading
import pytest


# Umbrales de tiempo máximos (segundos)
TIMEOUT_CREAR_50 = 30.0
TIMEOUT_RECALCULO_MASIVO = 5.0
TIMEOUT_CONSULTA_VISTA = 3.0
TIMEOUT_EXCEL_855 = 5.0
TIMEOUT_CONCURRENCIA = 15.0


# ---------------------------------------------------------------------------
# Estrés: Creación masiva de indicadores
# ---------------------------------------------------------------------------

class TestCreacionMasiva:

    def test_crear_50_indicadores_en_tiempo_razonable(self, sidoe_config):
        """Crear 50 indicadores completos (con fuente y factibilidad) < 30s."""
        from models.crud_indicadores import guardar_indicador

        fact = {
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

        inicio = time.time()
        errores = []
        for i in range(50):
            ok, msg = guardar_indicador(
                datos_indicador={
                    "codigo": f"ESTRES-{i:04d}",
                    "indicador": f"Indicador de estrés número {i}",
                    "estado_indicador": "Activo",
                    "generador_demanda_id": (i % 4) + 1,  # Rotar entre los 4 generadores
                },
                datos_fuentes=[{
                    "nombre_fuente": f"Fuente estrés {i}",
                    "institucion_productora": "ONE Test",
                }],
                datos_factibilidad=fact,
                usuario_id=1,
            )
            if not ok:
                errores.append(f"ESTRES-{i:04d}: {msg}")

        duracion = time.time() - inicio
        assert not errores, f"Fallos durante creación masiva: {errores[:5]}"
        assert duracion < TIMEOUT_CREAR_50, (
            f"Crear 50 indicadores tomó {duracion:.2f}s (máximo: {TIMEOUT_CREAR_50}s)"
        )

    def test_crear_50_indicadores_persisten_todos(self, sidoe_config):
        """Después de crear 50 indicadores, todos deben existir en BD."""
        from models.crud_indicadores import guardar_indicador
        import data.database as db_mod

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

        for i in range(50):
            guardar_indicador(
                datos_indicador={
                    "codigo": f"PERSIST-{i:04d}",
                    "indicador": f"Indicador persistencia {i}",
                    "estado_indicador": "Activo",
                    "generador_demanda_id": 1,
                },
                datos_fuentes=[{"nombre_fuente": f"F{i}"}],
                datos_factibilidad=fact,
                usuario_id=1,
            )

        conn = db_mod.obtener_conexion()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM indicadores WHERE codigo LIKE 'PERSIST-%'"
        ).fetchone()[0]
        conn.close()
        assert cnt == 50, f"Se crearon 50 indicadores pero solo persisten {cnt}"


# ---------------------------------------------------------------------------
# Estrés: Recálculo masivo del Engine
# ---------------------------------------------------------------------------

class TestRecalculoMasivo:

    def test_recalcular_855_scores_en_menos_de_5s(self, db_conn):
        """El engine puro debe recalcular 855 scores en < 5s."""
        from features.engine_factibilidad import calcular_reglas_factibilidad

        filas = db_conn.execute("""
            SELECT c1_metodologia, c21_existencia_fuente, c22_disponibilidad,
                   c23_periodicidad_establecida, c31_posee_desagregacion,
                   num_desagregaciones_requeridas, num_desagregaciones_disponibles,
                   articulacion_fuentes, armonizacion_conceptual, subregistro_cobertura,
                   cobertura_territorial, estructura_datos, variables_calculo
            FROM calculo_factibilidad
        """).fetchall()

        inicio = time.time()
        resultados = []
        for f in filas:
            r = calcular_reglas_factibilidad({
                "c1_metodologia": f[0],
                "c21_existencia_fuente": f[1],
                "c22_disponibilidad": f[2],
                "c23_periodicidad_establecida": f[3],
                "c31_posee_desagregacion": f[4],
                "num_desagregaciones_requeridas": f[5],
                "num_desagregaciones_disponibles": f[6],
                "articulacion_fuentes": f[7],
                "armonizacion_conceptual": f[8],
                "subregistro_cobertura": f[9],
                "cobertura_territorial": f[10],
                "estructura_datos": f[11],
                "variables_calculo": f[12],
            })
            resultados.append(r["score_factibilidad_final"])

        duracion = time.time() - inicio
        assert len(resultados) == len(filas)
        assert duracion < TIMEOUT_RECALCULO_MASIVO, (
            f"Recalcular {len(filas)} scores tomó {duracion:.2f}s "
            f"(máximo: {TIMEOUT_RECALCULO_MASIVO}s)"
        )

    def test_recalculo_sin_errores_en_datos_reales(self, db_conn):
        """El engine no debe lanzar excepciones con ningún dato de producción."""
        from features.engine_factibilidad import calcular_reglas_factibilidad

        filas = db_conn.execute("""
            SELECT indicador_id, c1_metodologia, c21_existencia_fuente, c22_disponibilidad,
                   c23_periodicidad_establecida, c31_posee_desagregacion,
                   num_desagregaciones_requeridas, num_desagregaciones_disponibles,
                   articulacion_fuentes, armonizacion_conceptual, subregistro_cobertura,
                   cobertura_territorial, estructura_datos, variables_calculo
            FROM calculo_factibilidad
        """).fetchall()

        excepciones = []
        for f in filas:
            try:
                calcular_reglas_factibilidad({
                    "c1_metodologia": f[1],
                    "c21_existencia_fuente": f[2],
                    "c22_disponibilidad": f[3],
                    "c23_periodicidad_establecida": f[4],
                    "c31_posee_desagregacion": f[5],
                    "num_desagregaciones_requeridas": f[6],
                    "num_desagregaciones_disponibles": f[7],
                    "articulacion_fuentes": f[8],
                    "armonizacion_conceptual": f[9],
                    "subregistro_cobertura": f[10],
                    "cobertura_territorial": f[11],
                    "estructura_datos": f[12],
                    "variables_calculo": f[13],
                })
            except Exception as e:
                excepciones.append(f"indicador_id={f[0]}: {e}")

        assert not excepciones, (
            f"El engine lanzó {len(excepciones)} excepciones:\n"
            + "\n".join(excepciones[:5])
        )


# ---------------------------------------------------------------------------
# Estrés: Consultas pesadas a la BD
# ---------------------------------------------------------------------------

@pytest.mark.requiere_bd_local
class TestConsultasPesadas:

    def test_consulta_vista_todos_indicadores_en_tiempo(self, db_conn):
        """Consultar la vista indicadores_resuelto completa < 3s."""
        inicio = time.time()
        rows = db_conn.execute("SELECT * FROM indicadores_resuelto").fetchall()
        duracion = time.time() - inicio
        assert len(rows) > 800
        assert duracion < TIMEOUT_CONSULTA_VISTA, (
            f"Consulta de vista completa tomó {duracion:.2f}s "
            f"(máximo: {TIMEOUT_CONSULTA_VISTA}s)"
        )

    def test_join_indicadores_fuentes_factibilidad(self, db_conn):
        """JOIN de las 3 tablas core < 3s."""
        inicio = time.time()
        rows = db_conn.execute("""
            SELECT i.codigo, i.indicador, fi.nombre_fuente,
                   cf.score_factibilidad_final, cf.categoria_factibilidad
            FROM indicadores i
            LEFT JOIN fuentes_indicador fi ON fi.indicador_id = i.id
            LEFT JOIN calculo_factibilidad cf ON cf.indicador_id = i.id
        """).fetchall()
        duracion = time.time() - inicio
        assert len(rows) > 0
        assert duracion < TIMEOUT_CONSULTA_VISTA

    def test_filtro_por_categoria_factibilidad(self, db_conn):
        """Filtrar indicadores por categoría de factibilidad < 1s."""
        inicio = time.time()
        rows = db_conn.execute("""
            SELECT i.codigo, cf.score_factibilidad_final
            FROM indicadores i
            JOIN calculo_factibilidad cf ON cf.indicador_id = i.id
            WHERE cf.categoria_factibilidad = 'Factibilidad I'
            ORDER BY cf.score_factibilidad_final DESC
        """).fetchall()
        duracion = time.time() - inicio
        assert len(rows) > 0
        assert duracion < 1.0

    def test_agrupacion_por_generador_demanda(self, db_conn):
        """GROUP BY sobre indicadores_resuelto < 2s."""
        inicio = time.time()
        rows = db_conn.execute("""
            SELECT ir.generador_demanda,
                   cf.categoria_factibilidad,
                   COUNT(*) as n,
                   AVG(cf.score_factibilidad_final) as avg_score
            FROM indicadores_resuelto ir
            JOIN calculo_factibilidad cf ON cf.indicador_id = ir.id
            GROUP BY ir.generador_demanda, cf.categoria_factibilidad
            ORDER BY ir.generador_demanda, cf.categoria_factibilidad
        """).fetchall()
        duracion = time.time() - inicio
        assert len(rows) > 0
        assert duracion < 2.0


# ---------------------------------------------------------------------------
# Estrés: Exportación Excel masiva
# ---------------------------------------------------------------------------

class TestExportacionMasiva:

    def test_excel_855_indicadores_en_tiempo(self, db_conn):
        """Exportar 855 indicadores a Excel < 5s."""
        import pandas as pd
        from tracking.export_excel import generar_excel_memoria

        rows = db_conn.execute("""
            SELECT ir.codigo, ir.indicador, ir.generador_demanda,
                   cf.score_factibilidad_final, cf.categoria_factibilidad
            FROM indicadores_resuelto ir
            JOIN calculo_factibilidad cf ON cf.indicador_id = ir.id
        """).fetchall()

        df = pd.DataFrame(rows, columns=[
            "Código", "Indicador", "Generador de Demanda", "Score", "Categoría"
        ])

        inicio = time.time()
        resultado = generar_excel_memoria(df, "Exportacion_Masiva")
        duracion = time.time() - inicio

        assert len(resultado) > 0
        assert duracion < TIMEOUT_EXCEL_855, (
            f"Exportar {len(rows)} filas tomó {duracion:.2f}s "
            f"(máximo: {TIMEOUT_EXCEL_855}s)"
        )

    def test_excel_tres_hojas_simultaneas(self):
        """Generar un Excel con 3 hojas de datos (como la exportación real del sistema)."""
        import io
        import pandas as pd
        import openpyxl

        dfs = {
            "Indicadores": pd.DataFrame({"A": range(100), "B": [f"ind_{i}" for i in range(100)]}),
            "Fuentes": pd.DataFrame({"X": range(200), "Y": [f"fte_{i}" for i in range(200)]}),
            "Factibilidad": pd.DataFrame({"Score": [float(i) for i in range(855)]}),
        }

        inicio = time.time()
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for nombre, df in dfs.items():
                df.to_excel(writer, index=False, sheet_name=nombre)
        raw = buffer.getvalue()
        duracion = time.time() - inicio

        assert len(raw) > 0
        assert duracion < 5.0

        wb = openpyxl.load_workbook(io.BytesIO(raw))
        assert set(wb.sheetnames) == {"Indicadores", "Fuentes", "Factibilidad"}


# ---------------------------------------------------------------------------
# Estrés: Concurrencia — múltiples lecturas simultáneas
# ---------------------------------------------------------------------------

class TestConcurrencia:

    def test_lecturas_concurrentes_no_corrompen_bd(self, sidoe_config):
        """10 hilos leyendo la BD simultáneamente no deben fallar ni dar datos corruptos."""
        import data.database as db_mod

        resultados = []
        errores = []

        def leer_indicadores():
            try:
                conn = db_mod.obtener_conexion()
                rows = conn.execute(
                    "SELECT COUNT(*) FROM indicadores"
                ).fetchone()
                resultados.append(rows[0])
                conn.close()
            except Exception as e:
                errores.append(str(e))

        inicio = time.time()
        hilos = [threading.Thread(target=leer_indicadores) for _ in range(10)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)
        duracion = time.time() - inicio

        assert not errores, f"Errores en lecturas concurrentes: {errores}"
        assert len(resultados) == 10
        # Todos los hilos deben ver el mismo conteo
        assert len(set(resultados)) == 1, (
            f"Conteos inconsistentes entre hilos: {resultados}"
        )
        assert duracion < TIMEOUT_CONCURRENCIA

    def test_wal_permite_escritura_durante_lectura(self, sidoe_config):
        """WAL mode permite que un escritor y múltiples lectores coexistan."""
        import data.database as db_mod

        errores = []

        def escritor():
            try:
                conn = db_mod.obtener_conexion()
                conn.execute(
                    "INSERT INTO auditoria (usuario_id, accion, detalle) "
                    "VALUES (1, 'TEST_WAL', 'test concurrencia')"
                )
                conn.commit()
                conn.close()
            except Exception as e:
                errores.append(f"Escritor: {e}")

        def lector():
            try:
                conn = db_mod.obtener_conexion()
                conn.execute("SELECT COUNT(*) FROM calculo_factibilidad").fetchone()
                conn.close()
            except Exception as e:
                errores.append(f"Lector: {e}")

        hilos = [threading.Thread(target=lector) for _ in range(5)]
        hilos.append(threading.Thread(target=escritor))
        hilos += [threading.Thread(target=lector) for _ in range(4)]

        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=10)

        assert not errores, f"Errores de concurrencia WAL: {errores}"


# ---------------------------------------------------------------------------
# Estrés: Integridad bajo carga
# ---------------------------------------------------------------------------

class TestIntegridadBajoCarga:

    def test_crear_y_eliminar_50_no_deja_huerfanos(self, sidoe_config):
        """Crear y luego eliminar 50 indicadores no debe dejar datos huérfanos."""
        from models.crud_indicadores import guardar_indicador, borrar_indicador
        import data.database as db_mod

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

        ids_creados = []
        for i in range(50):
            guardar_indicador(
                datos_indicador={
                    "codigo": f"LIMPIEZA-{i:04d}",
                    "indicador": f"Indicador limpieza {i}",
                    "estado_indicador": "Activo",
                    "generador_demanda_id": 1,
                },
                datos_fuentes=[{"nombre_fuente": f"F{i}"}],
                datos_factibilidad=fact,
                usuario_id=1,
            )
            conn = db_mod.obtener_conexion()
            row = conn.execute(
                "SELECT id FROM indicadores WHERE codigo=?", (f"LIMPIEZA-{i:04d}",)
            ).fetchone()
            conn.close()
            if row:
                ids_creados.append(row[0])

        # Eliminar todos
        for ind_id in ids_creados:
            borrar_indicador(ind_id, usuario_id=1)

        conn = db_mod.obtener_conexion()
        # No deben quedar fuentes huérfanas
        huerfanas_fuentes = conn.execute("""
            SELECT COUNT(*) FROM fuentes_indicador fi
            LEFT JOIN indicadores i ON fi.indicador_id = i.id
            WHERE i.id IS NULL
        """).fetchone()[0]
        # No deben quedar factibilidades huérfanas
        huerfanas_fact = conn.execute("""
            SELECT COUNT(*) FROM calculo_factibilidad cf
            LEFT JOIN indicadores i ON cf.indicador_id = i.id
            WHERE i.id IS NULL
        """).fetchone()[0]
        conn.close()

        assert huerfanas_fuentes == 0, f"{huerfanas_fuentes} fuentes huérfanas tras limpieza"
        assert huerfanas_fact == 0, f"{huerfanas_fact} factibilidades huérfanas tras limpieza"
