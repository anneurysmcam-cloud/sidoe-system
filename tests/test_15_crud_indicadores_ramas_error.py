"""
tests/test_15_crud_indicadores_ramas_error.py
===============================================
Cobertura de ramas que test_04_crud_indicadores_integracion.py no
ejercitaba en models/crud_indicadores.py:

1. Funciones de lectura: obtener_indicadores_para_referencia,
   obtener_ejes_politicas_extra
2. Sincronización bidireccional de indicadores duplicados
   (sincronizar_indicadores_referenciados)
3. Ramas "no encontrado" en agregar_fuente / actualizar_fuente /
   eliminar_fuente
4. Ramas de excepción genérica (no IntegrityError) en guardar_indicador,
   modificar_indicador, borrar_indicador, agregar_fuente,
   actualizar_fuente, eliminar_fuente — forzadas monkeypateando
   registrar_log para simular un fallo de auditoría a mitad de
   transacción (debe hacer rollback completo, no dejar cambios a medias).
5. Validación de nombre vacío en guardar_indicador.
"""

from models.crud_indicadores import (
    actualizar_fuente,
    agregar_fuente,
    borrar_indicador,
    eliminar_fuente,
    guardar_indicador,
    modificar_indicador,
    obtener_ejes_politicas_extra,
    obtener_indicadores_para_referencia,
    sincronizar_indicadores_referenciados,
)

FACT_MIN = {
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


def _indicador(codigo: str, nombre: str, generador_demanda_id: int = 1) -> dict:
    return {
        "codigo": codigo,
        "indicador": nombre,
        "estado_indicador": "Activo",
        "generador_demanda_id": generador_demanda_id,
    }


def _fuente() -> dict:
    return {"nombre_fuente": "Fuente de prueba", "institucion_productora": "ONE Test"}


# ---------------------------------------------------------------------------
# Funciones de lectura
# ---------------------------------------------------------------------------

class TestObtenerIndicadoresParaReferencia:

    def test_lista_todos_sin_excluir(self, sidoe_config):
        guardar_indicador(_indicador("REF-001", "Indicador A"), [_fuente()], FACT_MIN)
        resultado = obtener_indicadores_para_referencia()
        codigos = [r["codigo"] for r in resultado]
        assert "REF-001" in codigos

    def test_excluye_el_id_indicado(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(_indicador("REF-002", "Indicador B"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'REF-002'"
        ).fetchone()[0]
        conn.close()

        resultado = obtener_indicadores_para_referencia(excluir_id=id_creado)
        codigos = [r["codigo"] for r in resultado]
        assert "REF-002" not in codigos


class TestObtenerEjesPoliticasExtra:

    def test_sin_pares_extra_devuelve_lista_vacia(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(_indicador("EJE-001", "Indicador sin ejes extra"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'EJE-001'"
        ).fetchone()[0]
        conn.close()

        assert obtener_ejes_politicas_extra(id_creado) == []

    def test_indicador_inexistente_no_falla(self, sidoe_config):
        assert obtener_ejes_politicas_extra(999999) == []


# ---------------------------------------------------------------------------
# Sincronización bidireccional de indicadores duplicados
# ---------------------------------------------------------------------------

class TestSincronizarIndicadoresReferenciados:

    def test_detecta_y_vincula_indicador_con_mismo_titulo_otro_generador(self, sidoe_config):
        """Dos indicadores con el mismo título (normalizado) pero distinto
        Generador de demanda deben quedar vinculados en ambas direcciones
        en indicadores_duplicados."""
        import data.database as db_mod

        titulo = "Tasa de Alfabetización Nacional"
        guardar_indicador(_indicador("DUP-001", titulo, generador_demanda_id=1), [_fuente()], FACT_MIN)
        guardar_indicador(_indicador("DUP-002", titulo.upper() + "   ", generador_demanda_id=2), [_fuente()], FACT_MIN)

        conn = db_mod.obtener_conexion()
        dup_001 = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'DUP-001'"
        ).fetchone()[0]
        dup_002 = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'DUP-002'"
        ).fetchone()[0]
        conn.close()

        assert "DUP-002" in dup_001
        assert "DUP-001" in dup_002

    def test_vinculacion_manual_a_codigo_inexistente_no_falla(self, sidoe_config):
        """Un typo o código borrado en el selector manual no debe romper
        el guardado — simplemente no hay nada del otro lado que actualizar."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("ODS-3.2", "Otro indicador cualquiera", generador_demanda_id=1),
            [_fuente()], FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute("SELECT id FROM indicadores WHERE codigo = 'ODS-3.2'").fetchone()[0]
        conn.close()

        ok, msg = modificar_indicador(
            id_creado,
            {
                "codigo": "ODS-3.2", "indicador": "Otro indicador cualquiera",
                "generador_demanda_id": 1, "_referencias_manuales": ["CODIGO-QUE-NO-EXISTE"],
            },
            FACT_MIN,
        )
        assert ok is True

    def test_auto_referencia_manual_no_se_actualiza_a_si_mismo(self, sidoe_config):
        """Si por error el usuario se selecciona a sí mismo como referencia
        (mismo código), no debe intentar re-actualizar su propia fila
        dentro del bucle bidireccional."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("ODS-3.3", "Indicador que se autorreferencia", generador_demanda_id=1),
            [_fuente()], FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute("SELECT id FROM indicadores WHERE codigo = 'ODS-3.3'").fetchone()[0]
        conn.close()

        ok, msg = modificar_indicador(
            id_creado,
            {
                "codigo": "ODS-3.3", "indicador": "Indicador que se autorreferencia",
                "generador_demanda_id": 1, "_referencias_manuales": ["ODS-3.3"],
            },
            FACT_MIN,
        )
        assert ok is True


        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        texto = sincronizar_indicadores_referenciados(
            cursor, indicador_id=1, codigo="X", nombre="   ",
            generador_demanda_id=1, codigos_manuales=[],
        )
        conn.close()
        assert texto == ""

    def test_vinculacion_manual_con_titulos_distintos_se_sincroniza_bidireccional(self, sidoe_config):
        """Caso de uso real del selector manual: dos indicadores con
        títulos DISTINTOS (por eso no los detecta _sugerir_referencias_
        automaticas) que el usuario vincula a mano desde uno de los dos
        lados. El otro lado debe quedar actualizado igual, no solo el que
        se editó explícitamente.

        Regresión de un bug real detectado en producción: el bucle de
        sincronización bidireccional solo recorría los candidatos
        AUTOMÁTICOS (mismo título), nunca los manuales — que es
        precisamente para lo que existe el selector manual (títulos que
        NO coinciden automáticamente)."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("ODS-3.1", "Tasa de alfabetización de 15 años y más", generador_demanda_id=1),
            [_fuente()], FACT_MIN,
        )
        guardar_indicador(
            _indicador("PNPSP-102", "Tasa de alfabetización nacional", generador_demanda_id=2),
            [_fuente()], FACT_MIN,
        )

        conn = db_mod.obtener_conexion()
        id_ods = conn.execute("SELECT id FROM indicadores WHERE codigo = 'ODS-3.1'").fetchone()[0]
        conn.close()

        # El usuario vincula manualmente PNPSP-102 desde la ficha de ODS-3.1
        # (títulos distintos -> _sugerir_referencias_automaticas() NO los
        # habría detectado por sí sola).
        modificar_indicador(
            id_ods,
            {
                "codigo": "ODS-3.1", "indicador": "Tasa de alfabetización de 15 años y más",
                "generador_demanda_id": 1, "_referencias_manuales": ["PNPSP-102"],
            },
            FACT_MIN,
        )

        conn = db_mod.obtener_conexion()
        dup_ods = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'ODS-3.1'"
        ).fetchone()[0]
        dup_pnpsp = conn.execute(
            "SELECT indicadores_duplicados FROM indicadores WHERE codigo = 'PNPSP-102'"
        ).fetchone()[0]
        conn.close()

        assert "PNPSP-102" in dup_ods
        assert "ODS-3.1" in dup_pnpsp, (
            "El vínculo manual debe sincronizarse también hacia el otro "
            "lado, no solo quedar guardado en el indicador que se editó."
        )


# ---------------------------------------------------------------------------
# titulo_normalizado — columna de apoyo para _sugerir_referencias_automaticas
# (Hallazgo 2 del informe de rendimiento agosto 2026: reemplaza el escaneo
# completo de la tabla en Python por un WHERE indexado)
# ---------------------------------------------------------------------------

class TestTituloNormalizado:

    def test_se_calcula_al_crear(self, sidoe_config):
        """guardar_indicador debe poblar titulo_normalizado (minúsculas,
        espacios colapsados) en el mismo INSERT que escribe `indicador`."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("TNORM-001", "  Tasa   de   Empleo   Formal  "),
            [_fuente()], FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        valor = conn.execute(
            "SELECT titulo_normalizado FROM indicadores WHERE codigo = 'TNORM-001'"
        ).fetchone()[0]
        conn.close()

        assert valor == "tasa de empleo formal"

    def test_se_actualiza_al_modificar_el_nombre(self, sidoe_config):
        """modificar_indicador debe recalcular titulo_normalizado cuando el
        formulario envía un nuevo `indicador`, sin dejarlo desincronizado."""
        import data.database as db_mod

        ok, _ = guardar_indicador(
            _indicador("TNORM-002", "Nombre Original"), [_fuente()], FACT_MIN
        )
        assert ok
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'TNORM-002'"
        ).fetchone()[0]
        conn.close()

        ok, _ = modificar_indicador(
            id_creado, {"indicador": "Nombre   Corregido"}, FACT_MIN
        )
        assert ok

        conn = db_mod.obtener_conexion()
        valor = conn.execute(
            "SELECT titulo_normalizado FROM indicadores WHERE id = ?", (id_creado,)
        ).fetchone()[0]
        conn.close()

        assert valor == "nombre corregido"

    def test_no_aparece_en_el_resumen_de_cambios_para_el_supervisor(self, sidoe_config):
        """titulo_normalizado es un campo interno derivado, no editable por
        el usuario: no debe figurar como un 'cambio' más en el resumen que
        ve el supervisor en Aprobar Indicadores (ver
        models/revision_pendiente.py::calcular_diferencias)."""
        import data.database as db_mod

        ok, _ = guardar_indicador(
            _indicador("TNORM-003", "Titulo Inicial"), [_fuente()], FACT_MIN
        )
        assert ok
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'TNORM-003'"
        ).fetchone()[0]
        conn.close()

        ok, _ = modificar_indicador(
            id_creado,
            {"indicador": "Titulo Modificado", "estado_publicacion": "borrador"},
            FACT_MIN,
        )
        assert ok

        conn = db_mod.obtener_conexion()
        detalle = conn.execute(
            "SELECT revision_detalle FROM indicadores WHERE id = ?", (id_creado,)
        ).fetchone()[0]
        conn.close()

        assert "titulo_normalizado" not in detalle.lower()
        assert "nombre del indicador" in detalle.lower()

    def test_sugerir_referencias_usa_titulo_normalizado_no_full_scan(self, sidoe_config):
        """La detección automática (vía titulo_normalizado + índice) debe
        seguir encontrando coincidencias case/espacio-insensibles, igual que
        antes del refactor a la consulta indexada (ver Hallazgo 2)."""
        from models.crud_indicadores import _sugerir_referencias_automaticas

        import data.database as db_mod

        guardar_indicador(
            _indicador("TNORM-004", "Cobertura de Vacunación", generador_demanda_id=1),
            [_fuente()], FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        candidatos = _sugerir_referencias_automaticas(
            cursor, "  cobertura DE vacunación  ", generador_demanda_id=2
        )
        conn.close()

        assert any(codigo == "TNORM-004" for _id, codigo in candidatos)


# ---------------------------------------------------------------------------
# Ramas "no encontrado" en CRUD de fuentes
# ---------------------------------------------------------------------------

class TestFuenteNoEncontrada:

    def test_agregar_fuente_a_indicador_inexistente(self, sidoe_config):
        ok, msg = agregar_fuente(999999, _fuente())
        assert ok is False
        assert "no se encontró" in msg.lower()

    def test_actualizar_fuente_inexistente(self, sidoe_config):
        ok, msg = actualizar_fuente(999999, {"nombre_fuente": "Nueva"})
        assert ok is False
        assert "no se encontró" in msg.lower()

    def test_eliminar_fuente_inexistente(self, sidoe_config):
        ok, msg = eliminar_fuente(999999)
        assert ok is False
        assert "no se encontró" in msg.lower()


# ---------------------------------------------------------------------------
# Validación de campos obligatorios
# ---------------------------------------------------------------------------

class TestValidacionCamposObligatorios:

    def test_guardar_indicador_sin_nombre_falla(self, sidoe_config):
        datos = _indicador("VAL-001", "")
        ok, msg = guardar_indicador(datos, [_fuente()], FACT_MIN)
        assert ok is False
        assert "nombre" in msg.lower()


# ---------------------------------------------------------------------------
# Ramas de excepción genérica — fallo de auditoría a mitad de transacción
# ---------------------------------------------------------------------------
# Se monkeypatea registrar_log (llamado tras las operaciones de escritura
# principales en cada función) para forzar la rama `except Exception` y
# confirmar que TODO el cambio se revierte (rollback real), no solo se
# reporta el error.

class TestRollbackAnteFalloDeAuditoria:

    def test_guardar_indicador_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado de auditoría")),
        )
        ok, msg = guardar_indicador(_indicador("ROLLBACK-001", "Indicador rollback"), [_fuente()], FACT_MIN)
        assert ok is False
        assert "error" in msg.lower()

        conn = db_mod.obtener_conexion()
        existe = conn.execute(
            "SELECT 1 FROM indicadores WHERE codigo = 'ROLLBACK-001'"
        ).fetchone()
        conn.close()
        assert existe is None, "El INSERT debió revertirse por completo (rollback)."

    def test_modificar_indicador_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        guardar_indicador(_indicador("ROLLBACK-002", "Original"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'ROLLBACK-002'"
        ).fetchone()[0]
        conn.close()

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado")),
        )
        ok, msg = modificar_indicador(
            id_creado, {"indicador": "Nombre Modificado"}, FACT_MIN
        )
        assert ok is False

        conn = db_mod.obtener_conexion()
        nombre_actual = conn.execute(
            "SELECT indicador FROM indicadores WHERE id = ?", (id_creado,)
        ).fetchone()[0]
        conn.close()
        assert nombre_actual == "Original", "El UPDATE debió revertirse (rollback)."

    def test_borrar_indicador_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        guardar_indicador(_indicador("ROLLBACK-003", "A eliminar"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'ROLLBACK-003'"
        ).fetchone()[0]
        conn.close()

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado")),
        )
        ok, msg = borrar_indicador(id_creado)
        assert ok is False

        conn = db_mod.obtener_conexion()
        existe = conn.execute(
            "SELECT 1 FROM indicadores WHERE id = ?", (id_creado,)
        ).fetchone()
        conn.close()
        assert existe is not None, "El DELETE debió revertirse (rollback)."

    def test_agregar_fuente_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        guardar_indicador(_indicador("ROLLBACK-004", "Con fuente extra"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_creado = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'ROLLBACK-004'"
        ).fetchone()[0]
        conn.close()

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado")),
        )
        ok, msg = agregar_fuente(id_creado, {"nombre_fuente": "Fuente que no debe persistir"})
        assert ok is False

        conn = db_mod.obtener_conexion()
        existe = conn.execute(
            "SELECT 1 FROM fuentes_indicador WHERE nombre_fuente = 'Fuente que no debe persistir'"
        ).fetchone()
        conn.close()
        assert existe is None, "El INSERT de la fuente debió revertirse (rollback)."

    def test_actualizar_fuente_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        guardar_indicador(_indicador("ROLLBACK-005", "Con fuente"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        fuente_id = conn.execute(
            "SELECT fi.id FROM fuentes_indicador fi "
            "JOIN indicadores i ON i.id = fi.indicador_id "
            "WHERE i.codigo = 'ROLLBACK-005'"
        ).fetchone()[0]
        conn.close()

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado")),
        )
        ok, msg = actualizar_fuente(fuente_id, {"nombre_fuente": "Nombre que no debe persistir"})
        assert ok is False

        conn = db_mod.obtener_conexion()
        nombre_actual = conn.execute(
            "SELECT nombre_fuente FROM fuentes_indicador WHERE id = ?", (fuente_id,)
        ).fetchone()[0]
        conn.close()
        assert nombre_actual != "Nombre que no debe persistir"

    def test_eliminar_fuente_hace_rollback_completo(self, sidoe_config, monkeypatch):
        import models.crud_indicadores as crud_mod
        import data.database as db_mod

        guardar_indicador(_indicador("ROLLBACK-006", "Con fuente a no eliminar"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        fuente_id = conn.execute(
            "SELECT fi.id FROM fuentes_indicador fi "
            "JOIN indicadores i ON i.id = fi.indicador_id "
            "WHERE i.codigo = 'ROLLBACK-006'"
        ).fetchone()[0]
        conn.close()

        monkeypatch.setattr(
            crud_mod, "registrar_log",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallo simulado")),
        )
        ok, msg = eliminar_fuente(fuente_id)
        assert ok is False

        conn = db_mod.obtener_conexion()
        existe = conn.execute(
            "SELECT 1 FROM fuentes_indicador WHERE id = ?", (fuente_id,)
        ).fetchone()
        conn.close()
        assert existe is not None, "El DELETE de la fuente debió revertirse (rollback)."


# ---------------------------------------------------------------------------
# modificar_indicador — actualizar fuente existente vs. insertar nueva
# ---------------------------------------------------------------------------

class TestModificarIndicadorFuente:

    def test_con_fuente_id_actualiza_fuente_existente(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(_indicador("MODF-001", "Con fuente a modificar"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_indicador = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'MODF-001'"
        ).fetchone()[0]
        fuente_id = conn.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id = ?", (id_indicador,)
        ).fetchone()[0]
        conn.close()

        ok, msg = modificar_indicador(
            id_indicador, {}, FACT_MIN,
            datos_fuente={"nombre_fuente": "Fuente Actualizada"},
            fuente_id=fuente_id,
        )
        assert ok is True

        conn = db_mod.obtener_conexion()
        total_fuentes = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id = ?", (id_indicador,)
        ).fetchone()[0]
        nombre = conn.execute(
            "SELECT nombre_fuente FROM fuentes_indicador WHERE id = ?", (fuente_id,)
        ).fetchone()[0]
        conn.close()
        assert total_fuentes == 1, "No debe insertar una fuente nueva; debe actualizar la existente."
        assert nombre == "Fuente Actualizada"

    def test_sin_fuente_id_inserta_fuente_nueva(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(_indicador("MODF-002", "Con una fuente inicial"), [_fuente()], FACT_MIN)
        conn = db_mod.obtener_conexion()
        id_indicador = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'MODF-002'"
        ).fetchone()[0]
        conn.close()

        ok, msg = modificar_indicador(
            id_indicador, {}, FACT_MIN,
            datos_fuente={"nombre_fuente": "Segunda Fuente"},
            fuente_id=None,
        )
        assert ok is True

        conn = db_mod.obtener_conexion()
        total_fuentes = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id = ?", (id_indicador,)
        ).fetchone()[0]
        conn.close()
        assert total_fuentes == 2, "Sin fuente_id debe insertar una fuente adicional."
