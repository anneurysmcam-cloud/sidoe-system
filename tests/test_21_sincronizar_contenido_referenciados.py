"""
tests/test_21_sincronizar_contenido_referenciados.py
=======================================================
Cubre models.crud_indicadores.sincronizar_contenido_referenciados() y su
integración automática en guardar_indicador()/modificar_indicador():

- Al guardar un indicador con referencia (manual o auto-detectada), sus
  fuentes y criterios de factibilidad se propagan al indicador referenciado.
- La factibilidad SIEMPRE se recalcula con el Engine en destino (nunca se
  copia el score directamente).
- Los campos personalizados de las fuentes reemplazadas se copian con ellas.
- Indicadores sin referencia no se tocan.
- La propagación es bidireccional y "vive": editar cualquiera de los dos
  lados vuelve a propagar hacia el otro.
"""

import sqlite3

from models.crud_indicadores import (
    guardar_indicador,
    modificar_indicador,
    sincronizar_contenido_referenciados,
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

FACT_MAX = {
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


def _indicador(codigo: str, nombre: str, generador_demanda_id: int = 1, **extra) -> dict:
    return {
        "codigo": codigo,
        "indicador": nombre,
        "estado_indicador": "Activo",
        "generador_demanda_id": generador_demanda_id,
        **extra,
    }


def _id_por_codigo(conn, codigo: str) -> int:
    return conn.execute(
        "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
    ).fetchone()[0]


def _fuentes_de(conn, codigo: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM fuentes_indicador WHERE indicador_id = "
        "(SELECT id FROM indicadores WHERE codigo = ?) ORDER BY id",
        (codigo,),
    ).fetchall()


def _factibilidad_de(conn, codigo: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM calculo_factibilidad WHERE indicador_id = "
        "(SELECT id FROM indicadores WHERE codigo = ?)",
        (codigo,),
    ).fetchone()


class TestPropagacionAlCrear:

    def test_referencia_manual_propaga_fuente_y_factibilidad_al_destino(self, sidoe_config):
        import data.database as db_mod

        # B existe primero, con su propia fuente/factibilidad "pobre".
        guardar_indicador(
            _indicador("B-001", "Indicador destino sin relación de título"),
            [{"nombre_fuente": "Fuente vieja de B", "institucion_productora": "Vieja"}],
            FACT_MIN,
        )

        # A se crea referenciando manualmente a B, con fuente/factibilidad "buena".
        ok, msg = guardar_indicador(
            _indicador(
                "A-001", "Indicador origen",
                _referencias_manuales=["B-001"],
            ),
            [{"nombre_fuente": "Fuente de A", "institucion_productora": "ONE"}],
            FACT_MAX,
        )
        assert ok is True, msg

        conn = db_mod.obtener_conexion()

        fuentes_b = _fuentes_de(conn, "B-001")
        assert len(fuentes_b) == 1
        assert fuentes_b[0]["nombre_fuente"] == "Fuente de A"
        assert fuentes_b[0]["institucion_productora"] == "ONE"

        fact_b = _factibilidad_de(conn, "B-001")
        fact_a = _factibilidad_de(conn, "A-001")
        assert fact_b["c1_metodologia"] == fact_a["c1_metodologia"] == FACT_MAX["c1_metodologia"]
        assert fact_b["score_factibilidad_final"] == fact_a["score_factibilidad_final"]
        assert fact_b["categoria_factibilidad"] == fact_a["categoria_factibilidad"]

        conn.close()

    def test_nombre_no_se_propaga_pero_otros_campos_de_descripcion_si(self, sidoe_config):
        """Confirmado con la jefa de Randy en ONE (2026-07-27): dos
        indicadores referenciados pueden llevar el mismo tratamiento sin
        llamarse igual (nombres parecidos, no idénticos), así que el
        nombre (columna 'indicador') queda excluido de la sincronización
        de descripción, a diferencia de los demás campos descriptivos que
        sí se propagan (p. ej. metodo_calculo)."""
        import data.database as db_mod

        guardar_indicador(
            _indicador(
                "DESC-B-001", "Nombre original de B, parecido pero no igual",
                metodo_calculo="No identificado",
            ),
            [{"nombre_fuente": "Fuente vieja de B", "institucion_productora": "Vieja"}],
            FACT_MIN,
        )

        ok, msg = guardar_indicador(
            _indicador(
                "DESC-A-001", "Nombre de A, con tratamiento compartido",
                metodo_calculo="Definido",
                _referencias_manuales=["DESC-B-001"],
            ),
            [{"nombre_fuente": "Fuente de A", "institucion_productora": "ONE"}],
            FACT_MAX,
        )
        assert ok is True, msg

        conn = db_mod.obtener_conexion()
        conn.row_factory = sqlite3.Row
        b = conn.execute(
            "SELECT indicador, metodo_calculo FROM indicadores WHERE codigo = ?",
            ("DESC-B-001",),
        ).fetchone()

        # El nombre de B se mantiene intacto: NO se sobrescribe con el de A.
        assert b["indicador"] == "Nombre original de B, parecido pero no igual"
        # El resto de la descripción sí se propaga normalmente.
        assert b["metodo_calculo"] == "Definido"

        conn.close()

    def test_indicador_sin_referencia_no_se_toca(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(
            _indicador("SOLO-001", "Indicador sin ninguna relación"),
            [{"nombre_fuente": "Fuente original", "institucion_productora": "ONE"}],
            FACT_MIN,
        )
        guardar_indicador(
            _indicador("OTRO-001", "Otro indicador cualquiera sin relación"),
            [{"nombre_fuente": "Fuente de otro", "institucion_productora": "ONE"}],
            FACT_MAX,
        )

        conn = db_mod.obtener_conexion()
        fuentes = _fuentes_de(conn, "SOLO-001")
        assert len(fuentes) == 1
        assert fuentes[0]["nombre_fuente"] == "Fuente original"
        conn.close()


class TestPropagacionEsBidireccionalYViva:

    def test_editar_el_lado_referenciado_propaga_de_vuelta(self, sidoe_config):
        """Responde directamente a la pregunta de Randy: si cambio algo en
        uno de los dos, ¿se refleja en el otro? Sí, en ambos sentidos."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("B-002", "Indicador destino"),
            [{"nombre_fuente": "Fuente vieja de B", "institucion_productora": "Vieja"}],
            FACT_MIN,
        )
        guardar_indicador(
            _indicador("A-002", "Indicador origen", _referencias_manuales=["B-002"]),
            [{"nombre_fuente": "Fuente de A", "institucion_productora": "ONE"}],
            FACT_MAX,
        )

        conn = db_mod.obtener_conexion()
        id_b = _id_por_codigo(conn, "B-002")
        id_fuente_b = conn.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id = ?", (id_b,)
        ).fetchone()[0]
        conn.close()

        # Ahora se edita B (el lado que fue "receptor" en el paso anterior)
        # con una fuente/factibilidad distinta -- debe propagar hacia A.
        # Se pasa fuente_id para ACTUALIZAR la fuente existente (ya
        # propagada desde A), no agregar una segunda.
        ok, msg = modificar_indicador(
            id_b,
            {
                "codigo": "B-002", "indicador": "Indicador destino",
                "generador_demanda_id": 1,
                "_referencias_manuales": ["A-002"],
            },
            FACT_MIN,
            datos_fuente={
                "nombre_fuente": "Fuente actualizada desde B",
                "institucion_productora": "ONE-B",
            },
            fuente_id=id_fuente_b,
        )
        assert ok is True, msg

        conn = db_mod.obtener_conexion()
        fuentes_a = _fuentes_de(conn, "A-002")
        assert len(fuentes_a) == 1
        assert fuentes_a[0]["nombre_fuente"] == "Fuente actualizada desde B"

        fact_a = _factibilidad_de(conn, "A-002")
        assert fact_a["c1_metodologia"] == FACT_MIN["c1_metodologia"]
        conn.close()


class TestSincronizarContenidoReferenciadosDirecto:
    """Pruebas unitarias directas de la función, incluyendo el detalle de
    campos personalizados de fuentes, sin pasar por todo guardar_indicador."""

    def test_copia_campos_personalizados_de_la_fuente(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(
            _indicador("B-003", "Indicador destino personalizado"),
            [{"nombre_fuente": "Fuente vieja", "institucion_productora": "Vieja"}],
            FACT_MIN,
        )
        guardar_indicador(
            _indicador("A-003", "Indicador origen personalizado", _referencias_manuales=["B-003"]),
            [{"nombre_fuente": "Fuente con campo custom", "institucion_productora": "ONE"}],
            FACT_MIN,
        )

        conn = db_mod.obtener_conexion()
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()

        # Se crea una categoría personalizada de fuente y se le asigna un
        # valor a la fuente de A (el origen) DESPUÉS de la sincronización
        # inicial, para luego forzar una segunda propagación y verificar
        # que el campo personalizado viaja con la fuente.
        cursor.execute(
            "INSERT INTO auxiliares_categorias (clave, nombre_visible, aplica_a) "
            "VALUES ('obs_calidad', 'Observación de calidad', 'fuente')"
        )
        categoria_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor) VALUES (?, 'Revisado')",
            (categoria_id,),
        )
        valor_id = cursor.lastrowid

        id_fuente_a = cursor.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id = "
            "(SELECT id FROM indicadores WHERE codigo = 'A-003')"
        ).fetchone()[0]
        cursor.execute(
            "INSERT INTO fuente_campos_personalizados (fuente_id, categoria_id, valor_id) "
            "VALUES (?, ?, ?)",
            (id_fuente_a, categoria_id, valor_id),
        )
        conn.commit()

        # Forzar de nuevo la propagación (simula guardar A otra vez).
        sincronizar_contenido_referenciados(cursor, _id_por_codigo(conn, "A-003"))
        conn.commit()

        id_fuente_b_nueva = cursor.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id = "
            "(SELECT id FROM indicadores WHERE codigo = 'B-003')"
        ).fetchone()[0]
        personalizado_b = cursor.execute(
            "SELECT valor_id FROM fuente_campos_personalizados WHERE fuente_id = ?",
            (id_fuente_b_nueva,),
        ).fetchone()

        assert personalizado_b is not None
        assert personalizado_b[0] == valor_id
        conn.close()

    def test_copia_campos_personalizados_del_indicador_y_reemplaza_los_del_destino(
        self, sidoe_config
    ):
        """Reforzado 2026-08-01: los campos personalizados de Auxiliares a
        nivel indicador (tabla indicador_campos_personalizados) también
        deben propagarse, con el mismo criterio de reemplazo completo (no
        mezcla) que ya aplicaba a fuentes y factibilidad.
        """
        import data.database as db_mod

        guardar_indicador(
            _indicador("B-004", "Indicador destino personalizado indicador"),
            [{"nombre_fuente": "Fuente B", "institucion_productora": "Vieja"}],
            FACT_MIN,
        )
        guardar_indicador(
            _indicador(
                "A-004", "Indicador origen personalizado indicador",
                _referencias_manuales=["B-004"],
            ),
            [{"nombre_fuente": "Fuente A", "institucion_productora": "ONE"}],
            FACT_MIN,
        )

        conn = db_mod.obtener_conexion()
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO auxiliares_categorias (clave, nombre_visible, aplica_a) "
            "VALUES ('obs_indicador', 'Observación del indicador', 'indicador')"
        )
        categoria_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor) VALUES (?, 'Prioritario')",
            (categoria_id,),
        )
        valor_id_origen = cursor.lastrowid

        # El destino (B) ya tenía un valor propio en esa misma categoría
        # ANTES de la propagación: debe quedar reemplazado, no mezclado.
        cursor.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor) VALUES (?, 'Sin revisar')",
            (categoria_id,),
        )
        valor_id_viejo_destino = cursor.lastrowid
        id_b = _id_por_codigo(conn, "B-004")
        cursor.execute(
            "INSERT INTO indicador_campos_personalizados "
            "(indicador_id, categoria_id, valor_id) VALUES (?, ?, ?)",
            (id_b, categoria_id, valor_id_viejo_destino),
        )

        id_a = _id_por_codigo(conn, "A-004")
        cursor.execute(
            "INSERT INTO indicador_campos_personalizados "
            "(indicador_id, categoria_id, valor_id) VALUES (?, ?, ?)",
            (id_a, categoria_id, valor_id_origen),
        )
        conn.commit()

        sincronizar_contenido_referenciados(cursor, id_a)
        conn.commit()

        personalizado_b = cursor.execute(
            "SELECT valor_id FROM indicador_campos_personalizados "
            "WHERE indicador_id = ? AND categoria_id = ?",
            (id_b, categoria_id),
        ).fetchall()

        assert len(personalizado_b) == 1
        assert personalizado_b[0][0] == valor_id_origen
        conn.close()

    def test_reemplazo_de_fuentes_elimina_las_viejas_del_destino(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(
            _indicador("B-004", "Indicador con dos fuentes viejas"),
            [
                {"nombre_fuente": "Vieja 1", "institucion_productora": "X"},
                {"nombre_fuente": "Vieja 2", "institucion_productora": "Y"},
            ],
            FACT_MIN,
        )
        guardar_indicador(
            _indicador("A-004", "Indicador con una sola fuente", _referencias_manuales=["B-004"]),
            [{"nombre_fuente": "Única fuente de A", "institucion_productora": "ONE"}],
            FACT_MIN,
        )

        conn = db_mod.obtener_conexion()
        fuentes_b = _fuentes_de(conn, "B-004")
        assert len(fuentes_b) == 1
        assert fuentes_b[0]["nombre_fuente"] == "Única fuente de A"
        conn.close()

    def test_sin_indicadores_duplicados_no_hace_nada(self, sidoe_config):
        import data.database as db_mod

        guardar_indicador(
            _indicador("ND-001", "Indicador sin duplicados"),
            [{"nombre_fuente": "Fuente propia", "institucion_productora": "ONE"}],
            FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        resultado = sincronizar_contenido_referenciados(cursor, _id_por_codigo(conn, "ND-001"))
        conn.commit()
        conn.close()

        assert resultado == []

    def test_codigo_destino_inexistente_no_falla(self, sidoe_config):
        """Un código en indicadores_duplicados que ya no existe en la BD
        (borrado, o typo residual) no debe romper la propagación del resto."""
        import data.database as db_mod

        guardar_indicador(
            _indicador("A-005", "Indicador con referencia rota"),
            [{"nombre_fuente": "Fuente de A", "institucion_productora": "ONE"}],
            FACT_MIN,
        )
        conn = db_mod.obtener_conexion()
        conn.execute(
            "UPDATE indicadores SET indicadores_duplicados = 'NO-EXISTE-999' "
            "WHERE codigo = 'A-005'"
        )
        conn.commit()
        cursor = conn.cursor()
        resultado = sincronizar_contenido_referenciados(cursor, _id_por_codigo(conn, "A-005"))
        conn.commit()
        conn.close()

        assert resultado == []
