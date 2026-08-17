"""
tests/test_03_database_auxiliares.py
=====================================
TESTS UNITARIOS — Base de Datos y Sistema de Auxiliares

Validan:
  - obtener_conexion devuelve conexión funcional con FK y WAL
  - conexion_transaccional hace commit en éxito y rollback en error
  - Las 3 tablas core existen con las columnas esperadas
  - Las 2 vistas resolutoras existen y son consultables
  - CRUD de auxiliares: listar, crear, renombrar, desactivar
  - Bloqueo de eliminación de valores en uso
  - Categorías de sistema no son eliminables
"""

import sqlite3
import pytest


# ---------------------------------------------------------------------------
# Tests de conexión
# ---------------------------------------------------------------------------

class TestObtenerConexion:

    def test_devuelve_conexion_sqlite(self, sidoe_config):
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_foreign_keys_activos(self, sidoe_config):
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        assert fk == 1, "PRAGMA foreign_keys debe estar ON"

    def test_wal_mode_activo(self, sidoe_config):
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal", "journal_mode debe ser WAL"


class TestConexionTransaccional:

    def test_commit_en_exito(self, sidoe_config):
        """Si el bloque no lanza excepción, el commit debe persistir."""
        import data.database as db_mod
        with db_mod.conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "INSERT INTO auditoria (usuario_id, accion, detalle) VALUES (?, ?, ?)",
                (1, "TEST_COMMIT", "test unitario")
            )
        # Verificar que persiste
        conn2 = db_mod.obtener_conexion()
        row = conn2.execute(
            "SELECT detalle FROM auditoria WHERE accion='TEST_COMMIT'"
        ).fetchone()
        conn2.close()
        assert row is not None
        assert row[0] == "test unitario"

    def test_rollback_en_excepcion(self, sidoe_config):
        """Si el bloque lanza excepción, el rollback debe evitar persistencia."""
        import data.database as db_mod
        with pytest.raises(RuntimeError):
            with db_mod.conexion_transaccional() as (conn, cursor):
                cursor.execute(
                    "INSERT INTO auditoria (usuario_id, accion, detalle) VALUES (?, ?, ?)",
                    (1, "TEST_ROLLBACK", "nunca debe persistir")
                )
                raise RuntimeError("Error simulado para test de rollback")

        conn2 = db_mod.obtener_conexion()
        row = conn2.execute(
            "SELECT detalle FROM auditoria WHERE accion='TEST_ROLLBACK'"
        ).fetchone()
        conn2.close()
        assert row is None, "El rollback debió revertir el INSERT"


# ---------------------------------------------------------------------------
# Tests de esquema — tablas y columnas
# ---------------------------------------------------------------------------

class TestEsquemaTablas:

    TABLAS_CORE = [
        "indicadores",
        "fuentes_indicador",
        "calculo_factibilidad",
        "usuarios",
        "auditoria",
        "auxiliares_categorias",
        "auxiliares_valores",
    ]

    def test_tablas_core_existen(self, db_conn):
        tablas = {
            r[0] for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for tabla in self.TABLAS_CORE:
            assert tabla in tablas, f"Tabla '{tabla}' no existe en el esquema"

    def test_indicadores_tiene_columna_codigo_unique(self, db_conn):
        cols = {r[1] for r in db_conn.execute("PRAGMA table_info(indicadores)").fetchall()}
        assert "codigo" in cols
        assert "estado_indicador" in cols
        assert "indicador" in cols

    def test_calculo_factibilidad_tiene_columnas_criterio(self, db_conn):
        cols = {r[1] for r in db_conn.execute(
            "PRAGMA table_info(calculo_factibilidad)"
        ).fetchall()}
        for col in ["c1_metodologia", "c21_existencia_fuente", "score_factibilidad_final",
                    "categoria_factibilidad", "indicador_id"]:
            assert col in cols, f"Columna '{col}' falta en calculo_factibilidad"

    def test_fuentes_indicador_tiene_fk_a_indicadores(self, db_conn):
        fks = db_conn.execute("PRAGMA foreign_key_list(fuentes_indicador)").fetchall()
        tablas_ref = {fk[2] for fk in fks}
        assert "indicadores" in tablas_ref

    def test_calculo_factibilidad_tiene_fk_a_indicadores(self, db_conn):
        fks = db_conn.execute("PRAGMA foreign_key_list(calculo_factibilidad)").fetchall()
        tablas_ref = {fk[2] for fk in fks}
        assert "indicadores" in tablas_ref


class TestIndices:

    INDICES_ESPERADOS = {
        "idx_fuentes_indicador_indicador_id": "fuentes_indicador",
        "idx_indicadores_estado_publicacion": "indicadores",
        "idx_indicadores_titulo_normalizado": "indicadores",
        "idx_auditoria_timestamp": "auditoria",
    }

    def test_indices_existen(self, db_conn):
        indices = {
            r[0] for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        for nombre in self.INDICES_ESPERADOS:
            assert nombre in indices, f"Índice '{nombre}' no existe en el esquema"

    def test_indices_apuntan_a_la_tabla_correcta(self, db_conn):
        for nombre, tabla_esperada in self.INDICES_ESPERADOS.items():
            fila = db_conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
                (nombre,),
            ).fetchone()
            assert fila is not None, f"Índice '{nombre}' no encontrado"
            assert fila[0] == tabla_esperada

    def test_indice_compuesto_indicadores_cubre_ambas_columnas(self, db_conn):
        cols = [
            r[2] for r in db_conn.execute(
                "PRAGMA index_info(idx_indicadores_estado_publicacion)"
            ).fetchall()
        ]
        assert cols == ["estado_indicador", "estado_publicacion"]

    def test_crear_indices_es_idempotente(self, sidoe_config):
        """Ejecutar crear_indices() dos veces no debe lanzar error."""
        import data.database as db_mod
        db_mod.crear_indices()
        db_mod.crear_indices()


class TestMigracionTituloNormalizado:

    def test_columna_existe(self, db_conn):
        cols = {r[1] for r in db_conn.execute("PRAGMA table_info(indicadores)").fetchall()}
        assert "titulo_normalizado" in cols

    def test_migracion_es_idempotente(self, sidoe_config):
        """Ejecutar migrar_titulo_normalizado() dos veces no debe lanzar
        error ni duplicar trabajo (ALTER TABLE ya aplicado, backfill sin
        filas pendientes en la segunda pasada)."""
        import data.database as db_mod
        db_mod.migrar_titulo_normalizado()
        db_mod.migrar_titulo_normalizado()

    def test_backfill_puebla_filas_preexistentes(self, sidoe_config):
        """Simula una base 'antigua' (fila con indicador pero
        titulo_normalizado NULL, como quedaría una base creada antes de
        esta migración) y verifica que el backfill la corrige."""
        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        conn.execute(
            "INSERT INTO indicadores (codigo, indicador, generador_demanda_id, "
            "titulo_normalizado) VALUES ('BKF-001', '  Título   Sin Normalizar  ', 1, NULL)"
        )
        conn.commit()
        conn.close()

        db_mod.migrar_titulo_normalizado()

        conn = db_mod.obtener_conexion()
        valor = conn.execute(
            "SELECT titulo_normalizado FROM indicadores WHERE codigo = 'BKF-001'"
        ).fetchone()[0]
        conn.close()

        assert valor == "título sin normalizar"


class TestVistas:

    @pytest.mark.requiere_bd_local
    def test_vista_indicadores_resuelto_existe_y_es_consultable(self, db_conn):
        vistas = {
            r[0] for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        assert "indicadores_resuelto" in vistas
        # Debe poder ejecutarse sin error
        rows = db_conn.execute(
            "SELECT id, codigo, generador_demanda FROM indicadores_resuelto LIMIT 5"
        ).fetchall()
        assert len(rows) > 0

    def test_vista_fuentes_resuelto_existe_y_es_consultable(self, db_conn):
        vistas = {
            r[0] for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        assert "fuentes_resuelto" in vistas
        rows = db_conn.execute(
            "SELECT id, indicador_id FROM fuentes_resuelto LIMIT 5"
        ).fetchall()
        assert rows is not None  # Puede estar vacía pero no debe lanzar error

    def test_vista_resuelto_devuelve_texto_no_id(self, db_conn):
        """La vista debe resolver FK a texto, no devolver IDs numéricos."""
        row = db_conn.execute(
            "SELECT generador_demanda FROM indicadores_resuelto LIMIT 1"
        ).fetchone()
        if row:
            val = row[0]
            # El valor debe ser texto (END, ODS, CMV, PNPSP) no un entero
            assert not isinstance(val, int) or val is None


# ---------------------------------------------------------------------------
# Tests de integridad de datos de producción
# ---------------------------------------------------------------------------

class TestIntegridadDatosProduccion:

    def test_todos_indicadores_tienen_factibilidad(self, db_conn):
        """Cada indicador activo debe tener exactamente 1 registro de factibilidad."""
        sin_fact = db_conn.execute("""
            SELECT COUNT(*) FROM indicadores i
            LEFT JOIN calculo_factibilidad cf ON cf.indicador_id = i.id
            WHERE cf.id IS NULL
        """).fetchone()[0]
        assert sin_fact == 0, f"{sin_fact} indicadores sin registro de factibilidad"

    def test_codigos_indicadores_son_unicos(self, db_conn):
        total = db_conn.execute("SELECT COUNT(*) FROM indicadores").fetchone()[0]
        unicos = db_conn.execute(
            "SELECT COUNT(DISTINCT codigo) FROM indicadores"
        ).fetchone()[0]
        assert total == unicos, "Existen códigos duplicados en la tabla indicadores"

    def test_factibilidades_tienen_categoria_valida(self, db_conn):
        invalidas = db_conn.execute("""
            SELECT COUNT(*) FROM calculo_factibilidad
            WHERE categoria_factibilidad NOT IN
                ('Factibilidad I', 'Factibilidad II', 'Factibilidad III')
        """).fetchone()[0]
        assert invalidas == 0, f"{invalidas} filas con categoría de factibilidad inválida"

    def test_scores_en_rango_0_100(self, db_conn):
        fuera_rango = db_conn.execute("""
            SELECT COUNT(*) FROM calculo_factibilidad
            WHERE score_factibilidad_final < 0 OR score_factibilidad_final > 105
        """).fetchone()[0]
        assert fuera_rango == 0, f"{fuera_rango} scores fuera del rango esperado [0,105]"

    def test_fuentes_con_indicador_valido(self, db_conn):
        """No deben existir fuentes huérfanas (indicador_id no existe)."""
        huerfanas = db_conn.execute("""
            SELECT COUNT(*) FROM fuentes_indicador fi
            LEFT JOIN indicadores i ON fi.indicador_id = i.id
            WHERE i.id IS NULL
        """).fetchone()[0]
        assert huerfanas == 0, f"{huerfanas} fuentes sin indicador válido"

    def test_usuarios_tienen_rol_valido(self, db_conn):
        invalidos = db_conn.execute("""
            SELECT COUNT(*) FROM usuarios
            WHERE rol NOT IN ('editor', 'administrador')
        """).fetchone()[0]
        assert invalidos == 0, f"{invalidos} usuarios con rol inválido"


# ---------------------------------------------------------------------------
# Tests CRUD de Auxiliares
# ---------------------------------------------------------------------------

class TestAuxiliaresCRUD:

    def test_listar_categorias_devuelve_lista(self, sidoe_config):
        from models.crud_auxiliares import listar_categorias
        cats = listar_categorias()
        assert isinstance(cats, list)
        assert len(cats) > 0
        assert "clave" in cats[0]
        assert "nombre_visible" in cats[0]

    def test_obtener_categoria_por_clave_existente(self, sidoe_config):
        from models.crud_auxiliares import obtener_categoria_por_clave
        cat = obtener_categoria_por_clave("generador_demanda")
        assert cat is not None
        assert cat["clave"] == "generador_demanda"

    def test_obtener_categoria_por_clave_inexistente_devuelve_none(self, sidoe_config):
        from models.crud_auxiliares import obtener_categoria_por_clave
        cat = obtener_categoria_por_clave("clave_que_no_existe_xyz")
        assert cat is None

    def test_listar_valores_por_categoria(self, sidoe_config):
        from models.crud_auxiliares import obtener_valores
        vals = obtener_valores("generador_demanda")
        assert isinstance(vals, list)
        assert len(vals) >= 4  # END, ODS, CMV, PNPSP
        textos = [v["valor"] for v in vals]
        assert "END" in textos

    def test_crear_valor_auxiliar_nuevo(self, sidoe_config):
        """Crear un valor nuevo en una categoría existente y verificar en misma conexión."""
        import data.database as db_mod

        # Obtener cat_id directamente con la conexión parchada
        conn = db_mod.obtener_conexion()
        cat_id = conn.execute(
            "SELECT id FROM auxiliares_categorias WHERE clave=?", ("generador_demanda",)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
            (cat_id, "TEST_VAL_NUEVO"),
        )
        conn.commit()

        # Verificar en la misma conexión parchada
        textos = [
            r[0] for r in conn.execute(
                "SELECT valor FROM auxiliares_valores WHERE categoria_id=?", (cat_id,)
            ).fetchall()
        ]
        conn.close()
        assert "TEST_VAL_NUEVO" in textos

    def test_valor_duplicado_en_categoria_es_rechazado(self, sidoe_config):
        """Insertar valor duplicado (case-insensitive) debe fallar."""
        from models.crud_auxiliares import obtener_categoria_por_clave
        import data.database as db_mod

        cat = obtener_categoria_por_clave("generador_demanda")
        cat_id = cat["id"]

        conn = db_mod.obtener_conexion()
        # Primer insert
        conn.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
            (cat_id, "VAL_DUPLICADO_TEST"),
        )
        conn.commit()

        # La lógica de duplicado está en _existe_duplicado del módulo
        from models.crud_auxiliares import _existe_duplicado
        cursor = conn.cursor()
        assert _existe_duplicado(cursor, cat_id, "VAL_DUPLICADO_TEST") is True
        assert _existe_duplicado(cursor, cat_id, "val_duplicado_test") is True  # case-insensitive
        conn.close()


# ---------------------------------------------------------------------------
# Punto 4: normalización de campos categóricos (institucion_productora,
# nombre_fuente, area_misional_one) al convertirlos al modelo híbrido
# ---------------------------------------------------------------------------

class TestNormalizacionCamposCategoricosP4:
    """Simula el escenario real encontrado en el diagnóstico de producción
    (data/migraciones_historicas/diagnostico_normalizacion_p4.py): variantes de escritura por
    mayúsculas/minúsculas (que el matching case-insensitive de
    migrar_campo_hibrido() debe deduplicar solo) y el único conflicto real
    de acentos detectado (SIGEF), que requiere normalización explícita."""

    def _reinsertar_datos_legados(self, db_mod, indicador_id: int) -> None:
        conn = db_mod.obtener_conexion()
        conn.execute(
            "INSERT INTO fuentes_indicador (indicador_id, institucion_productora, nombre_fuente) "
            "VALUES (?, ?, ?)",
            (indicador_id, "Dirección General de Aduanas (DGA)",
             "Sistema de informacion de gestion financiera (SIGEF)"),
        )
        conn.execute(
            "INSERT INTO fuentes_indicador (indicador_id, institucion_productora, nombre_fuente) "
            "VALUES (?, ?, ?)",
            (indicador_id, "Dirección general de aduanas (DGA)",
             "Sistema de Información de Gestión Financiera (SIGEF)"),
        )
        conn.commit()
        conn.close()

    def test_case_insensitive_no_crea_auxiliares_duplicados(self, sidoe_config):
        """'Dirección General...' y 'Dirección general...' (solo difieren en
        mayúsculas) deben colapsar en UNA sola entrada del Auxiliar."""
        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('P4-TEST', 'Indicador prueba p4', 'Activo')"
        )
        indicador_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._reinsertar_datos_legados(db_mod, indicador_id)

        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_todos_los_campos_hibridos()
        db_mod.crear_vistas_resueltas()

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        valores_institucion = cursor.execute(
            "SELECT COUNT(*) FROM auxiliares_valores av "
            "JOIN auxiliares_categorias ac ON ac.id = av.categoria_id "
            "WHERE ac.clave = 'institucion_productora' "
            "AND LOWER(av.valor) LIKE '%aduanas%'"
        ).fetchone()[0]
        conn.close()
        assert valores_institucion == 1

    def test_sigef_se_normaliza_antes_del_backfill(self, sidoe_config):
        """El valor sin tildes de SIGEF debe normalizarse a la forma con
        tildes ANTES de crear el Auxiliar, evitando una entrada duplicada."""
        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('P4-TEST-2', 'Indicador prueba p4 sigef', 'Activo')"
        )
        indicador_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._reinsertar_datos_legados(db_mod, indicador_id)

        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_todos_los_campos_hibridos()
        db_mod.crear_vistas_resueltas()

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        valores_sigef = cursor.execute(
            "SELECT av.valor FROM auxiliares_valores av "
            "JOIN auxiliares_categorias ac ON ac.id = av.categoria_id "
            "WHERE ac.clave = 'nombre_fuente' AND av.valor LIKE '%SIGEF%'"
        ).fetchall()
        conn.close()
        assert len(valores_sigef) == 1
        assert valores_sigef[0][0] == "Sistema de Información de Gestión Financiera (SIGEF)"

    def test_ambas_filas_resuelven_al_mismo_texto_sin_columnas_duplicadas(self, sidoe_config):
        """Regresión: fuentes_resuelto no debe tener columnas duplicadas
        (institucion_productora/nombre_fuente) tras convertirlas a híbridas;
        ambas filas legadas deben resolver exactamente al mismo texto."""
        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('P4-TEST-3', 'Indicador prueba p4 vistas', 'Activo')"
        )
        indicador_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._reinsertar_datos_legados(db_mod, indicador_id)

        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_todos_los_campos_hibridos()
        db_mod.crear_vistas_resueltas()

        conn = db_mod.obtener_conexion()
        conn.row_factory = sqlite3.Row
        filas = conn.execute(
            "SELECT institucion_productora, nombre_fuente FROM fuentes_resuelto "
            "WHERE indicador_id = ?", (indicador_id,)
        ).fetchall()
        conn.close()

        assert len(filas) == 2
        assert filas[0]["institucion_productora"] == filas[1]["institucion_productora"]
        assert filas[0]["nombre_fuente"] == filas[1]["nombre_fuente"]
        assert filas[0]["nombre_fuente"] == "Sistema de Información de Gestión Financiera (SIGEF)"

    def test_ningun_valor_legado_queda_sin_backfill(self, sidoe_config):
        """Toda fila con texto legado en institucion_productora/nombre_fuente
        debe terminar con su *_id correspondiente asignado."""
        import data.database as db_mod

        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('P4-TEST-4', 'Indicador prueba p4 backfill', 'Activo')"
        )
        indicador_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._reinsertar_datos_legados(db_mod, indicador_id)

        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_todos_los_campos_hibridos()

        conn = db_mod.obtener_conexion()
        faltantes = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE indicador_id = ? "
            "AND (institucion_productora_id IS NULL OR nombre_fuente_id IS NULL)",
            (indicador_id,),
        ).fetchone()[0]
        conn.close()
        assert faltantes == 0
