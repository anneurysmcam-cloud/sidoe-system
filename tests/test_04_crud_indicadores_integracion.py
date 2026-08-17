"""
tests/test_04_crud_indicadores_integracion.py
==============================================
TESTS DE INTEGRACIÓN — CRUD de Indicadores

Validan el flujo completo de operaciones sobre indicadores: crear, leer,
actualizar, eliminar — incluyendo sus efectos cascada sobre fuentes_indicador
y calculo_factibilidad.

Cada test opera sobre la BD temporal (fixture sidoe_config) y verifica
el estado final directamente en SQLite para confirmar persistencia real.
"""

import pytest


# ---------------------------------------------------------------------------
# Datos de prueba
# ---------------------------------------------------------------------------

FACT_MAX = {
    "c1_metodologia": (
        "Indicador con metodología nacional o internacional definida"
    ),
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


def _indicador(codigo: str = "INTG-001", nombre: str = "Indicador de integración") -> dict:
    return {
        "codigo": codigo,
        "indicador": nombre,
        "estado_indicador": "Activo",
        "generador_demanda_id": 1,
    }


def _fuente() -> dict:
    return {
        "nombre_fuente": "Fuente integración test",
        "institucion_productora": "ONE Test",
    }


# ---------------------------------------------------------------------------
# Crear Indicador
# ---------------------------------------------------------------------------

class TestCrearIndicador:

    def test_crear_indicador_minimo_exitoso(self, sidoe_config):
        from models.crud_indicadores import guardar_indicador
        ok, msg = guardar_indicador(
            datos_indicador=_indicador("INT-CREATE-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        assert ok is True, f"Falló crear indicador: {msg}"
        assert "correctamente" in msg.lower() or "guardado" in msg.lower()

    def test_crear_indicador_persiste_en_bd(self, sidoe_config):
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        guardar_indicador(
            datos_indicador=_indicador("INT-PERSIST-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT codigo, indicador FROM indicadores WHERE codigo=?",
            ("INT-PERSIST-01",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "INT-PERSIST-01"

    def test_crear_indicador_genera_factibilidad(self, sidoe_config):
        """Al crear un indicador, debe crearse su fila en calculo_factibilidad."""
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        guardar_indicador(
            datos_indicador=_indicador("INT-FACT-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", ("INT-FACT-01",)
        ).fetchone()[0]
        fact = conn.execute(
            "SELECT score_factibilidad_final, categoria_factibilidad "
            "FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()
        conn.close()
        assert fact is not None
        assert fact[0] == pytest.approx(100.002, rel=1e-3)
        assert fact[1] == "Factibilidad I"

    def test_crear_indicador_con_score_cero(self, sidoe_config):
        """Indicador con todos los criterios mínimos → Factibilidad III."""
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        guardar_indicador(
            datos_indicador=_indicador("INT-CERO-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", ("INT-CERO-01",)
        ).fetchone()[0]
        fact = conn.execute(
            "SELECT score_factibilidad_final, categoria_factibilidad "
            "FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()
        conn.close()
        assert fact[0] == 0.0
        assert fact[1] == "Factibilidad III"

    def test_crear_indicador_registra_auditoria(self, sidoe_config):
        """Crear un indicador debe dejar registro en tabla auditoria."""
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        guardar_indicador(
            datos_indicador=_indicador("INT-AUDIT-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        log = conn.execute(
            "SELECT accion, detalle FROM auditoria "
            "WHERE accion='CREAR' AND detalle LIKE '%INT-AUDIT-01%' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert log is not None, "No se registró la acción de auditoría al crear"
        assert log[0] == "CREAR"

    def test_codigo_duplicado_devuelve_error(self, sidoe_config):
        """Crear dos indicadores con el mismo código debe fallar en el segundo."""
        from models.crud_indicadores import guardar_indicador

        guardar_indicador(
            datos_indicador=_indicador("INT-DUP-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        ok, msg = guardar_indicador(
            datos_indicador=_indicador("INT-DUP-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        assert ok is False
        assert "INT-DUP-01" in msg or "existe" in msg.lower() or "duplicado" in msg.lower()

    def test_crear_sin_codigo_devuelve_error(self, sidoe_config):
        from models.crud_indicadores import guardar_indicador
        datos = _indicador("")
        datos["codigo"] = ""
        ok, msg = guardar_indicador(
            datos_indicador=datos,
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        assert ok is False

    def test_crear_indicador_con_multiples_fuentes(self, sidoe_config):
        """Un indicador puede tener N fuentes — todas deben persistir."""
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        fuentes = [
            {"nombre_fuente": "Fuente A", "institucion_productora": "Inst A"},
            {"nombre_fuente": "Fuente B", "institucion_productora": "Inst B"},
            {"nombre_fuente": "Fuente C", "institucion_productora": "Inst C"},
        ]
        guardar_indicador(
            datos_indicador=_indicador("INT-MULTI-01"),
            datos_fuentes=fuentes,
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", ("INT-MULTI-01",)
        ).fetchone()[0]
        cnt = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert cnt == 3


# ---------------------------------------------------------------------------
# Leer Indicador
# ---------------------------------------------------------------------------

class TestLeerIndicador:

    def test_obtener_indicador_por_id_existente(self, sidoe_config):
        from models.crud_indicadores import guardar_indicador, obtener_indicador_por_id
        guardar_indicador(
            datos_indicador=_indicador("INT-READ-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", ("INT-READ-01",)
        ).fetchone()[0]
        conn.close()

        result = obtener_indicador_por_id(ind_id)
        assert "indicador" in result
        assert "fuentes" in result
        assert "factibilidad" in result
        assert result["indicador"]["codigo"] == "INT-READ-01"

    def test_obtener_indicador_id_inexistente_devuelve_vacios(self, sidoe_config):
        from models.crud_indicadores import obtener_indicador_por_id
        result = obtener_indicador_por_id(999999)
        assert result["indicador"] == {} or result["indicador"] is None or not result["indicador"]

    @pytest.mark.requiere_bd_local
    def test_obtener_indicadores_para_referencia(self, sidoe_config):
        from models.crud_indicadores import obtener_indicadores_para_referencia
        lista = obtener_indicadores_para_referencia()
        assert isinstance(lista, list)
        assert len(lista) > 0
        assert "codigo" in lista[0]
        assert "id" in lista[0]

    def test_vista_resuelto_resuelve_generador_demanda(self, sidoe_config):
        """indicadores_resuelto debe devolver texto, no ID para generador_demanda."""
        from models.crud_indicadores import guardar_indicador, obtener_indicador_por_id
        guardar_indicador(
            datos_indicador=_indicador("INT-VISTA-01"),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", ("INT-VISTA-01",)
        ).fetchone()[0]
        conn.close()

        result = obtener_indicador_por_id(ind_id)
        gd = result["indicador"].get("generador_demanda")
        assert gd is not None
        assert isinstance(gd, str)
        assert not gd.isdigit(), "generador_demanda debe ser texto, no un ID numérico"


# ---------------------------------------------------------------------------
# Modificar Indicador
# ---------------------------------------------------------------------------

class TestModificarIndicador:

    def _crear_y_obtener_id(self, sidoe_config, codigo: str) -> int:
        from models.crud_indicadores import guardar_indicador
        import data.database as db_mod
        guardar_indicador(
            datos_indicador=_indicador(codigo),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", (codigo,)
        ).fetchone()[0]
        conn.close()
        return ind_id

    def test_modificar_nombre_indicador(self, sidoe_config):
        from models.crud_indicadores import modificar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-MOD-01")
        ok, msg = modificar_indicador(
            id_indicador=ind_id,
            datos_indicador={"indicador": "Nombre Modificado por Test"},
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        assert ok is True, f"Error al modificar: {msg}"

        conn = db_mod.obtener_conexion()
        nombre = conn.execute(
            "SELECT indicador FROM indicadores WHERE id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert nombre == "Nombre Modificado por Test"

    def test_modificar_recalcula_factibilidad(self, sidoe_config):
        """Al modificar los datos de factibilidad, el score debe actualizarse."""
        from models.crud_indicadores import modificar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-RECALC-01")

        # Primero el score debe ser 100.002 (máximo)
        conn = db_mod.obtener_conexion()
        score_antes = conn.execute(
            "SELECT score_factibilidad_final FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()[0]
        conn.close()
        assert score_antes == pytest.approx(100.002, rel=1e-3)

        # Ahora modificar con factibilidad mínima → score debe ser 0
        modificar_indicador(
            id_indicador=ind_id,
            datos_indicador={},
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )

        conn = db_mod.obtener_conexion()
        score_despues = conn.execute(
            "SELECT score_factibilidad_final, categoria_factibilidad "
            "FROM calculo_factibilidad WHERE indicador_id=?",
            (ind_id,),
        ).fetchone()
        conn.close()
        assert score_despues[0] == 0.0
        assert score_despues[1] == "Factibilidad III"

    def test_modificar_registra_auditoria(self, sidoe_config):
        from models.crud_indicadores import modificar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-MAUDIT-01")
        modificar_indicador(
            id_indicador=ind_id,
            datos_indicador={"indicador": "Nombre para auditoria test"},
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        log = conn.execute(
            "SELECT accion FROM auditoria WHERE accion='ACTUALIZAR' "
            f"AND detalle LIKE '%{ind_id}%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert log is not None

    def test_agregar_fuente_a_indicador_existente(self, sidoe_config):
        from models.crud_indicadores import agregar_fuente
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-FUENTE-ADD-01")

        ok, msg = agregar_fuente(
            indicador_id=ind_id,
            datos_fuente={"nombre_fuente": "Nueva fuente", "institucion_productora": "Inst Nueva"},
            usuario_id=1,
        )
        assert ok is True, f"Error al agregar fuente: {msg}"

        conn = db_mod.obtener_conexion()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert cnt == 2  # La original más la nueva

    def test_actualizar_fuente_existente(self, sidoe_config):
        from models.crud_indicadores import actualizar_fuente
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-FUENTE-UPD-01")

        conn = db_mod.obtener_conexion()
        fuente_id = conn.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()

        ok, msg = actualizar_fuente(
            fuente_id=fuente_id,
            datos_fuente={"nombre_fuente": "Fuente Actualizada por Test"},
            usuario_id=1,
        )
        assert ok is True

        conn = db_mod.obtener_conexion()
        nombre = conn.execute(
            "SELECT nombre_fuente FROM fuentes_indicador WHERE id=?", (fuente_id,)
        ).fetchone()[0]
        conn.close()
        assert nombre == "Fuente Actualizada por Test"

    def test_eliminar_fuente_individual(self, sidoe_config):
        from models.crud_indicadores import agregar_fuente, eliminar_fuente
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-FUENTE-DEL-01")
        # Agregar segunda fuente
        agregar_fuente(
            indicador_id=ind_id,
            datos_fuente={"nombre_fuente": "Fuente a eliminar"},
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        fuentes = conn.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchall()
        conn.close()
        assert len(fuentes) == 2
        fuente_a_eliminar = fuentes[1][0]

        ok, msg = eliminar_fuente(fuente_id=fuente_a_eliminar, usuario_id=1)
        assert ok is True

        conn = db_mod.obtener_conexion()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert cnt == 1  # Solo queda la original


# ---------------------------------------------------------------------------
# Eliminar Indicador
# ---------------------------------------------------------------------------

class TestEliminarIndicador:

    def _crear_y_obtener_id(self, sidoe_config, codigo: str) -> int:
        from models.crud_indicadores import guardar_indicador
        import data.database as db_mod
        guardar_indicador(
            datos_indicador=_indicador(codigo),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", (codigo,)
        ).fetchone()[0]
        conn.close()
        return ind_id

    def test_eliminar_indicador_exitoso(self, sidoe_config):
        from models.crud_indicadores import borrar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-DEL-01")
        ok, msg = borrar_indicador(ind_id, usuario_id=1)
        assert ok is True

        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT id FROM indicadores WHERE id=?", (ind_id,)
        ).fetchone()
        conn.close()
        assert row is None

    def test_eliminar_en_cascada_fuentes(self, sidoe_config):
        """Eliminar indicador debe eliminar sus fuentes en cascada."""
        from models.crud_indicadores import borrar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-CASCADE-01")
        borrar_indicador(ind_id, usuario_id=1)

        conn = db_mod.obtener_conexion()
        fuentes = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert fuentes == 0

    def test_eliminar_en_cascada_factibilidad(self, sidoe_config):
        """Eliminar indicador debe eliminar su factibilidad en cascada."""
        from models.crud_indicadores import borrar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-CASCADE-FACT-01")
        borrar_indicador(ind_id, usuario_id=1)

        conn = db_mod.obtener_conexion()
        fact = conn.execute(
            "SELECT COUNT(*) FROM calculo_factibilidad WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.close()
        assert fact == 0

    def test_eliminar_registra_auditoria(self, sidoe_config):
        from models.crud_indicadores import borrar_indicador
        import data.database as db_mod

        ind_id = self._crear_y_obtener_id(sidoe_config, "INT-DAUDIT-01")
        borrar_indicador(ind_id, usuario_id=1)

        conn = db_mod.obtener_conexion()
        log = conn.execute(
            "SELECT accion FROM auditoria WHERE accion='ELIMINAR' "
            "AND detalle LIKE '%INT-DAUDIT-01%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert log is not None

    def test_eliminar_indicador_inexistente_devuelve_ok_sin_crash(self, sidoe_config):
        """Intentar eliminar un ID que no existe no debe lanzar excepción fatal."""
        from models.crud_indicadores import borrar_indicador
        # Puede retornar ok=True (no encontró nada que borrar) o un mensaje apropiado
        try:
            ok, msg = borrar_indicador(999999, usuario_id=1)
            # No debe fallar con excepción
        except Exception as e:
            pytest.fail(f"borrar_indicador lanzó excepción inesperada: {e}")
