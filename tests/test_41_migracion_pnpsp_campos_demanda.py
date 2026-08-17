"""
tests/test_41_migracion_pnpsp_campos_demanda.py
=================================================
[BUG] "Requerimiento de clasificacion", "Especificar clasificacion" e
"Indicadores duplicados" NO existen en la hoja "Factibilidad" del Excel
oficial -- solo viven en "Demanda y Oferta". La migración PNPSP
(``migrar_pnpsp_faltantes``) construía ``datos_indicador`` leyendo esos
tres campos de ``primera`` (una fila de Factibilidad), así que
``primera.get(...)`` devolvía siempre ``None`` -> "No" (requerimiento) o
cadena vacía (los otros dos), para el 100% de los indicadores PNPSP.
Confirmado contra el Excel oficial real: 374/374 indicadores PNPSP
tenían ``requerimiento_clasificacion_id`` fijo en "No" antes del fix.

Cubre:
1. data/migraciones_historicas/ETL_migracion.py::migrar_pnpsp_faltantes -- ahora resuelve estos
   tres campos desde Demanda y Oferta (por "Orden"), igual que hace con
   las fuentes.
2. data/migraciones_historicas/migracion_backfill_pnpsp_campos_demanda.py
   -- backfill dirigido para BDs PNPSP ya migradas con el bug (aplica el
   mismo criterio + sincronización bidireccional de indicadores_duplicados,
   que guardar_indicador() sobrescribe con solo auto-detección al crear).
"""

import sqlite3

import openpyxl
import pandas as pd
import pytest

from data.migraciones_historicas.migracion_backfill_pnpsp_campos_demanda import (
    leer_campos_pnpsp_excel,
    migrar as migrar_backfill_pnpsp,
)


# ---------------------------------------------------------------------------
# Parte 1: data/migraciones_historicas/ETL_migracion.py::migrar_pnpsp_faltantes (mock de pd.read_excel)
# ---------------------------------------------------------------------------

class TestETLPnpspResuelveCamposDeDemanda:

    def _mock_read_excel(self, monkeypatch, req_clas, esp_clas, dup):
        df_fac = pd.DataFrame([{
            "Código": "NA",
            "Generador de demanda": "PNPSP",
            "Orden": 1.0,
            "Indicador": "Indicador PNPSP de prueba",
            "Eje": "Eje X",
            "Politica de gobierno": "Politica X",
            "Sector IOE": "Social",
            "C1. Existencia de Metodología establecida o definida": "No cumple con los criterios anteriores",
            "C2.1 Existencia (fuente de datos)": "No hay fuente",
            "C2.2. Disponibilidad /accesibilidad": "No",
            "C2.3 Periodicidad establecida": "No",
            "C3.1 Posee algún tipo desagregación requerida": "No",
            "Numero de desagregaciones requeridas por el indicador": 0,
            "Numero de desagregaciones disponibles en la fuente": 0,
            "Articulación de fuentes": "No se articula",
            "Definiciones o armonización conceptual (Requiere y no tiene)": "No",
            "Subregistro  y/o Subcobertura": "No",
            "Cobertura Territorial": "No",
            "Uso de Clasificaciones": "No",
            "Estructura de datos": "No posee ninguna de las anteriores",
        }])
        df_dem = pd.DataFrame([{
            "Orden": 1.0,
            "Código": "NA",
            "Requerimiento de clasificacion": req_clas,
            "Especificar clasificacion": esp_clas,
            "Indicadores duplicados": dup,
            "Existencia de Fuente": "No hay fuente",
        }])

        def _fake_read_excel(ruta, sheet_name, header):
            return df_fac if sheet_name == "Factibilidad" else df_dem

        monkeypatch.setattr("data.migraciones_historicas.ETL_migracion.pd.read_excel", _fake_read_excel)
        return df_dem

    def test_requerimiento_clasificacion_se_lee_de_demanda_no_de_factibilidad(
        self, monkeypatch, tmp_path
    ):
        """[BUG REGRESIÓN] Antes del fix, este campo quedaba fijo en 'No'
        para TODOS los indicadores PNPSP porque se leía de Factibilidad,
        donde la columna no existe."""
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")
        self._mock_read_excel(monkeypatch, req_clas="Sí", esp_clas="", dup=None)

        llamadas_resolver = []

        def _fake_resolver(categoria, texto, *a, **k):
            llamadas_resolver.append((categoria, texto))
            return 1

        monkeypatch.setattr(etl, "resolver_o_crear_id", _fake_resolver)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)
        monkeypatch.setattr(
            etl.db_mod, "obtener_conexion",
            lambda: _ConexionFalsaSinCodigos(),
        )

        etl.migrar_pnpsp_faltantes(archivo_excel=str(archivo_fantasma))

        llamadas_req_clas = [t for cat, t in llamadas_resolver if cat == "requerimiento_clasificacion"]
        assert llamadas_req_clas == ["Si"], (
            "[BUG REGRESIÓN] requerimiento_clasificacion debe resolverse con el valor "
            "normalizado de Demanda ('Sí' -> 'Si'), no quedar fijo en 'No'."
        )

    def test_especificar_clasificacion_se_lee_de_demanda(self, monkeypatch, tmp_path):
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")
        self._mock_read_excel(
            monkeypatch, req_clas="No", esp_clas="CIE-10", dup=None
        )
        monkeypatch.setattr(etl, "resolver_o_crear_id", lambda *a, **k: 1)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)
        monkeypatch.setattr(etl.db_mod, "obtener_conexion", lambda: _ConexionFalsaSinCodigos())

        etl.migrar_pnpsp_faltantes(archivo_excel=str(archivo_fantasma))

        assert capturado.get("especificar_clasificacion") == "CIE-10", (
            "[BUG REGRESIÓN] especificar_clasificacion debe migrar el valor real de "
            "Demanda, no quedar vacío."
        )

    def test_indicadores_duplicados_crudo_se_lee_de_demanda(self, monkeypatch, tmp_path):
        """El valor crudo pasado a datos_indicador debe venir de Demanda.
        (Su persistencia final en BD, dado que guardar_indicador() lo
        sobrescribe vía sincronizar_indicadores_referenciados(), se cubre
        en la parte 2 con el backfill)."""
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")
        self._mock_read_excel(monkeypatch, req_clas="No", esp_clas="", dup="END 2.35")
        monkeypatch.setattr(etl, "resolver_o_crear_id", lambda *a, **k: 1)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)
        monkeypatch.setattr(etl.db_mod, "obtener_conexion", lambda: _ConexionFalsaSinCodigos())

        etl.migrar_pnpsp_faltantes(archivo_excel=str(archivo_fantasma))

        assert capturado.get("indicadores_duplicados") == "END 2.35"

    def test_sin_filas_de_fuente_en_demanda_no_rompe(self, monkeypatch, tmp_path):
        """Si por algún motivo un Orden PNPSP no tiene ninguna fila en
        Demanda (no debería ocurrir en el Excel real, ver validación de
        migración), debe degradar sin lanzar excepción."""
        import data.migraciones_historicas.ETL_migracion as etl

        archivo_fantasma = tmp_path / "fake.xlsx"
        archivo_fantasma.write_text("")

        df_fac = pd.DataFrame([{
            "Código": "NA", "Generador de demanda": "PNPSP", "Orden": 1.0,
            "Indicador": "Indicador huérfano de fuente",
            "C1. Existencia de Metodología establecida o definida": "No cumple con los criterios anteriores",
            "C2.1 Existencia (fuente de datos)": "No hay fuente",
            "C2.2. Disponibilidad /accesibilidad": "No",
            "C2.3 Periodicidad establecida": "No",
            "C3.1 Posee algún tipo desagregación requerida": "No",
            "Numero de desagregaciones requeridas por el indicador": 0,
            "Numero de desagregaciones disponibles en la fuente": 0,
            "Articulación de fuentes": "No se articula",
            "Definiciones o armonización conceptual (Requiere y no tiene)": "No",
            "Subregistro  y/o Subcobertura": "No",
            "Cobertura Territorial": "No",
            "Uso de Clasificaciones": "No",
            "Estructura de datos": "No posee ninguna de las anteriores",
        }])
        df_dem = pd.DataFrame([{"Orden": 999.0, "Código": "NA", "Existencia de Fuente": "No hay fuente"}])

        def _fake_read_excel(ruta, sheet_name, header):
            return df_fac if sheet_name == "Factibilidad" else df_dem

        monkeypatch.setattr("data.migraciones_historicas.ETL_migracion.pd.read_excel", _fake_read_excel)
        monkeypatch.setattr(etl, "resolver_o_crear_id", lambda *a, **k: 1)

        capturado = {}

        def _fake_guardar_indicador(datos_indicador, *a, **k):
            capturado.update(datos_indicador)
            return True, "ok"

        monkeypatch.setattr(etl, "guardar_indicador", _fake_guardar_indicador)
        monkeypatch.setattr(etl.db_mod, "obtener_conexion", lambda: _ConexionFalsaSinCodigos())

        etl.migrar_pnpsp_faltantes(archivo_excel=str(archivo_fantasma))
        assert capturado.get("especificar_clasificacion") == ""


class _ConexionFalsaSinCodigos:
    """Simula obtener_conexion() devolviendo cero códigos existentes."""

    def cursor(self):
        return self

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Parte 2: backfill sobre una BD PNPSP ya migrada con el bug
# ---------------------------------------------------------------------------

def _crear_excel_prueba(ruta):
    wb = openpyxl.Workbook()
    ws_fac = wb.active
    ws_fac.title = "Factibilidad"
    encabezados_fac = {"C4": "Código", "D4": "Generador de demanda", "E4": "Orden", "F4": "Indicador"}
    for celda, valor in encabezados_fac.items():
        ws_fac[celda] = valor
    ws_fac["C5"] = "NA"
    ws_fac["D5"] = "PNPSP"
    ws_fac["E5"] = 1
    ws_fac["F5"] = "Indicador PNPSP con clasificación y referencia"
    ws_fac["C6"] = "NA"
    ws_fac["D6"] = "PNPSP"
    ws_fac["E6"] = 2
    ws_fac["F6"] = "Indicador PNPSP sin referencia"

    ws_dem = wb.create_sheet("Demanda y Oferta")
    encabezados_dem = {
        "C3": "Orden", "D3": "Código",
        "E3": "Requerimiento de clasificacion", "F3": "Especificar clasificacion",
        "G3": "Indicadores duplicados",
    }
    for celda, valor in encabezados_dem.items():
        ws_dem[celda] = valor
    ws_dem["C4"] = 1
    ws_dem["D4"] = "NA"
    ws_dem["E4"] = "Sí"
    ws_dem["F4"] = "CIE-10"
    ws_dem["G4"] = "END 2.35"
    ws_dem["C5"] = 2
    ws_dem["D5"] = "NA"
    ws_dem["E5"] = "No"
    ws_dem["F5"] = ""
    ws_dem["G5"] = None

    wb.save(ruta)


def _sembrar_indicadores_pnpsp_con_bug(db_path):
    """Simula el estado que dejaba el ETL viejo: requerimiento_clasificacion
    fijo en 'No', especificar_clasificacion/indicadores_duplicados vacíos.

    Usa resolver_o_crear_id() (ya redirigido a db_path por el fixture
    sidoe_config del que depende este helper) para crear la categoría y
    el valor auxiliar "No" con el esquema real de auxiliares_categorias,
    en vez de asumir columnas.
    """
    from models.crud_auxiliares import resolver_o_crear_id

    id_no = resolver_o_crear_id("requerimiento_clasificacion", "No")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        "INSERT INTO indicadores (codigo, indicador, estado_indicador, "
        "requerimiento_clasificacion_id, especificar_clasificacion, indicadores_duplicados) "
        "VALUES ('PNPSP-001', 'Indicador PNPSP con clasificación y referencia', 'Activo', ?, '', NULL)",
        (id_no,),
    )
    conn.execute(
        "INSERT INTO indicadores (codigo, indicador, estado_indicador, "
        "requerimiento_clasificacion_id, especificar_clasificacion, indicadores_duplicados) "
        "VALUES ('PNPSP-002', 'Indicador PNPSP sin referencia', 'Activo', ?, '', NULL)",
        (id_no,),
    )
    # Destino real de la referencia cruzada "END 2.35" -> codigo pelado "2.35".
    conn.execute(
        "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
        "VALUES ('2.35', 'Indicador END destino de la referencia', 'Activo')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def excel_prueba_pnpsp(tmp_path):
    ruta = tmp_path / "excel_pnpsp_prueba.xlsx"
    _crear_excel_prueba(ruta)
    return str(ruta)


class TestLeerCamposPnpspExcel:

    def test_resuelve_valores_por_orden(self, excel_prueba_pnpsp):
        campos = leer_campos_pnpsp_excel(excel_prueba_pnpsp)
        assert campos["PNPSP-001"]["requerimiento_clasificacion"] == "Si"
        assert campos["PNPSP-001"]["especificar_clasificacion"] == "CIE-10"
        assert campos["PNPSP-001"]["indicadores_duplicados"] == "END 2.35"

    def test_sin_referencia_queda_vacio(self, excel_prueba_pnpsp):
        campos = leer_campos_pnpsp_excel(excel_prueba_pnpsp)
        assert campos["PNPSP-002"]["indicadores_duplicados"] == ""
        assert campos["PNPSP-002"]["requerimiento_clasificacion"] == "No"


class TestBackfillPnpspCamposDemanda:

    def test_corrige_requerimiento_clasificacion(self, sidoe_config, excel_prueba_pnpsp):
        """[BUG REGRESIÓN] Antes del fix, quedaba fijo en 'No' para el 100%
        de los PNPSP, sin importar el valor real en Demanda."""
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)

        resumen = migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)
        assert not resumen.get("error")

        conn = sqlite3.connect(sidoe_config)
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT av.valor FROM indicadores i "
            "JOIN auxiliares_valores av ON av.id = i.requerimiento_clasificacion_id "
            "WHERE i.codigo = 'PNPSP-001'"
        ).fetchone()
        assert fila["valor"] == "Si"
        conn.close()

    def test_corrige_especificar_clasificacion(self, sidoe_config, excel_prueba_pnpsp):
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)
        migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)

        conn = sqlite3.connect(sidoe_config)
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT especificar_clasificacion FROM indicadores WHERE codigo = 'PNPSP-001'"
        ).fetchone()
        assert fila["especificar_clasificacion"] == "CIE-10"
        conn.close()

    def test_resuelve_indicadores_duplicados_con_prefijo_de_generador(
        self, sidoe_config, excel_prueba_pnpsp
    ):
        """'END 2.35' en el Excel -> codigo real '2.35' en la BD, con
        sincronización bidireccional (igual criterio que el backfill hermano
        para END/ODS/CMV)."""
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)
        migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)

        conn = sqlite3.connect(sidoe_config)
        conn.row_factory = sqlite3.Row
        origen = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'PNPSP-001'"
        ).fetchone()
        destino = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = '2.35'"
        ).fetchone()
        assert origen["indicadores_duplicados"] == "2.35"
        assert destino["indicadores_duplicados"] == "PNPSP-001"
        conn.close()

    def test_codigo_sin_referencia_no_se_toca(self, sidoe_config, excel_prueba_pnpsp):
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)
        migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)

        conn = sqlite3.connect(sidoe_config)
        conn.row_factory = sqlite3.Row
        fila = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'PNPSP-002'"
        ).fetchone()
        assert fila["indicadores_duplicados"] is None
        conn.close()

    def test_backfill_es_idempotente(self, sidoe_config, excel_prueba_pnpsp):
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)

        primero = migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)
        segundo = migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)

        assert primero["actualizados"] >= 1
        assert segundo["actualizados"] == 0

    def test_codigo_pnpsp_del_excel_sin_match_en_bd_se_reporta(
        self, sidoe_config, excel_prueba_pnpsp
    ):
        """No debe romper el resto del backfill si un código PNPSP del
        Excel no existe todavía en la BD."""
        conn = sqlite3.connect(sidoe_config)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('2.35', 'Indicador END destino', 'Activo')"
        )
        conn.commit()
        conn.close()

        resumen = migrar_backfill_pnpsp(excel_prueba_pnpsp, db_path=sidoe_config)
        assert "PNPSP-001" in resumen["sin_match"]
        assert "PNPSP-002" in resumen["sin_match"]

    def test_excel_inexistente_no_rompe(self, sidoe_config, tmp_path):
        _sembrar_indicadores_pnpsp_con_bug(sidoe_config)
        resultado = migrar_backfill_pnpsp(str(tmp_path / "no_existe.xlsx"), db_path=sidoe_config)
        assert resultado == {"error": "excel_no_encontrado"}
