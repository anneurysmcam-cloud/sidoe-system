"""
tests/test_24_migracion_normalizacion_auxiliares_texto_libre.py
=================================================================
Cubre las dos migraciones que resuelven variantes de acentuación en
institucion_productora / nombre_fuente (punto 4), detectadas por
data/migraciones_historicas/diagnostico_normalizacion_p4.py el 2026-07-25:

- migrar_normalizar_nombre_fuente_conocidos(): normaliza el texto legado
  ANTES del backfill híbrido, para que LOWER(TRIM()) no genere dos
  entradas de catálogo para el mismo valor institucional.
- migrar_fusionar_duplicados_auxiliares_texto_libre(): repara casos donde
  el backfill YA corrió sin la normalización previa y dejó duplicados
  reales en auxiliares_valores (como ocurrió en producción con
  'Estadisticas'/'Estadísticas de Educación Superior').
"""

import pytest

import data.database as db_mod


@pytest.fixture
def indicador_id(sidoe_config):
    """Crea un indicador mínimo válido para satisfacer la FK
    fuentes_indicador.indicador_id -> indicadores(id)."""
    with db_mod.conexion_transaccional() as (conn, cursor):
        cursor.execute(
            "INSERT INTO indicadores (codigo, indicador, estado_indicador) "
            "VALUES ('P4-TEST', 'Indicador de prueba p4', 'Activo')"
        )
        return cursor.lastrowid


class TestMigrarNormalizarNombreFuenteConocidos:

    def test_normaliza_variante_sigef_sin_tildes(self, sidoe_config, indicador_id):
        with db_mod.conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "INSERT INTO fuentes_indicador (indicador_id, nombre_fuente) "
                "VALUES (?, ?)",
                (indicador_id, "Sistema de informacion de gestion financiera (SIGEF)"),
            )
        db_mod.migrar_normalizar_nombre_fuente_conocidos()

        conn = db_mod.obtener_conexion()
        valor = conn.execute(
            "SELECT nombre_fuente FROM fuentes_indicador WHERE indicador_id = ?",
            (indicador_id,),
        ).fetchone()[0]
        conn.close()
        assert valor == "Sistema de Información de Gestión Financiera (SIGEF)"

    def test_normaliza_variante_estadisticas_educacion_superior(self, sidoe_config, indicador_id):
        with db_mod.conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "INSERT INTO fuentes_indicador (indicador_id, nombre_fuente) VALUES "
                "(?, 'Informe General sobre Estadisticas de Educación Superior'), "
                "(?, 'Informe general sobre estadísticas de educación superior')",
                (indicador_id, indicador_id),
            )
        db_mod.migrar_normalizar_nombre_fuente_conocidos()

        conn = db_mod.obtener_conexion()
        valores = {
            r[0] for r in conn.execute(
                "SELECT nombre_fuente FROM fuentes_indicador WHERE indicador_id = ?",
                (indicador_id,),
            ).fetchall()
        }
        conn.close()
        assert valores == {"Informe General sobre Estadísticas de Educación Superior"}

    def test_no_toca_valores_ya_canonicos(self, sidoe_config, indicador_id):
        """Idempotencia: si el texto ya es canónico, no debe alterarse ni fallar."""
        with db_mod.conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "INSERT INTO fuentes_indicador (indicador_id, nombre_fuente) "
                "VALUES (?, ?)",
                (indicador_id, "Sistema de Información de Gestión Financiera (SIGEF)"),
            )
        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_normalizar_nombre_fuente_conocidos()

        conn = db_mod.obtener_conexion()
        valor = conn.execute(
            "SELECT nombre_fuente FROM fuentes_indicador WHERE indicador_id = ?",
            (indicador_id,),
        ).fetchone()[0]
        conn.close()
        assert valor == "Sistema de Información de Gestión Financiera (SIGEF)"


class TestMigrarFusionarDuplicadosAuxiliaresTextoLibre:

    def _crear_duplicado(self, cursor, indicador_id, clave_categoria, valor_a, valor_b, n_a, n_b):
        """Simula el escenario de producción: dos entradas de auxiliares_valores
        para lo que es el mismo valor (variante de acento), con filas de
        fuentes_indicador repartidas entre ambas."""
        categoria_id = cursor.execute(
            "SELECT id FROM auxiliares_categorias WHERE clave = ?", (clave_categoria,)
        ).fetchone()[0]

        cursor.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
            (categoria_id, valor_a),
        )
        id_a = cursor.lastrowid
        cursor.execute(
            "INSERT INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
            (categoria_id, valor_b),
        )
        id_b = cursor.lastrowid

        columna_id = f"{clave_categoria}_id"
        for _ in range(n_a):
            cursor.execute(
                f"INSERT INTO fuentes_indicador (indicador_id, {clave_categoria}, {columna_id}) "
                f"VALUES (?, ?, ?)",
                (indicador_id, valor_a, id_a),
            )
        for _ in range(n_b):
            cursor.execute(
                f"INSERT INTO fuentes_indicador (indicador_id, {clave_categoria}, {columna_id}) "
                f"VALUES (?, ?, ?)",
                (indicador_id, valor_b, id_b),
            )
        return id_a, id_b

    def test_fusiona_duplicado_y_conserva_el_de_mas_uso(self, sidoe_config, indicador_id):
        with db_mod.conexion_transaccional() as (conn, cursor):
            id_mayoritario, id_minoritario = self._crear_duplicado(
                cursor,
                indicador_id,
                clave_categoria="nombre_fuente",
                valor_a="Informe General sobre Estadísticas de Educación Superior",
                valor_b="Informe general sobre estadísticas de educación superior",
                n_a=3,
                n_b=1,
            )

        db_mod.migrar_fusionar_duplicados_auxiliares_texto_libre()

        conn = db_mod.obtener_conexion()
        restantes = conn.execute(
            "SELECT id, valor FROM auxiliares_valores WHERE id IN (?, ?)",
            (id_mayoritario, id_minoritario),
        ).fetchall()
        assert restantes == [(id_mayoritario, "Informe General sobre Estadísticas de Educación Superior")]

        conteo = conn.execute(
            "SELECT COUNT(*) FROM fuentes_indicador WHERE nombre_fuente_id = ?",
            (id_mayoritario,),
        ).fetchone()[0]
        conn.close()
        assert conteo == 4

    def test_texto_canonico_no_se_pierde_por_comparacion_de_longitud(self, sidoe_config, indicador_id):
        """Regresión: la tilde no cambia el largo del string, así que el
        criterio de canonicalización NO debe basarse en len(valor).

        migrar_fusionar_duplicados_auxiliares_texto_libre() toma el texto
        canónico de la tabla origen tal cual esté en ese momento — no
        corrige ortografía por sí sola. Por eso, igual que en el bootstrap
        real (database.py), primero se corre la normalización de texto
        legado y luego la fusión de duplicados del catálogo.
        """
        with db_mod.conexion_transaccional() as (conn, cursor):
            id_sin_tilde, id_con_tilde = self._crear_duplicado(
                cursor,
                indicador_id,
                clave_categoria="nombre_fuente",
                valor_a="Informe General sobre Estadisticas de Educación Superior",
                valor_b="Informe general sobre estadísticas de educación superior",
                n_a=3,
                n_b=1,
            )

        db_mod.migrar_normalizar_nombre_fuente_conocidos()
        db_mod.migrar_fusionar_duplicados_auxiliares_texto_libre()

        conn = db_mod.obtener_conexion()
        valor_final = conn.execute(
            "SELECT valor FROM auxiliares_valores WHERE id = ?", (id_sin_tilde,)
        ).fetchone()[0]
        conn.close()
        assert valor_final == "Informe General sobre Estadísticas de Educación Superior"

    def test_no_hace_nada_si_no_hay_duplicados(self, sidoe_config):
        conn = db_mod.obtener_conexion()
        antes = conn.execute("SELECT COUNT(*) FROM auxiliares_valores").fetchone()[0]
        conn.close()

        db_mod.migrar_fusionar_duplicados_auxiliares_texto_libre()

        conn = db_mod.obtener_conexion()
        despues = conn.execute("SELECT COUNT(*) FROM auxiliares_valores").fetchone()[0]
        conn.close()
        assert antes == despues

    def test_idempotente_segunda_corrida_no_cambia_nada(self, sidoe_config, indicador_id):
        with db_mod.conexion_transaccional() as (conn, cursor):
            self._crear_duplicado(
                cursor,
                indicador_id,
                clave_categoria="institucion_productora",
                valor_a="Dirección General de Aduanas (DGA)",
                valor_b="Direccion General de Aduanas (DGA)",
                n_a=7,
                n_b=1,
            )

        db_mod.migrar_fusionar_duplicados_auxiliares_texto_libre()

        conn = db_mod.obtener_conexion()
        estado_1 = conn.execute(
            "SELECT id, valor FROM auxiliares_valores WHERE categoria_id = "
            "(SELECT id FROM auxiliares_categorias WHERE clave='institucion_productora')"
        ).fetchall()
        conn.close()

        db_mod.migrar_fusionar_duplicados_auxiliares_texto_libre()

        conn = db_mod.obtener_conexion()
        estado_2 = conn.execute(
            "SELECT id, valor FROM auxiliares_valores WHERE categoria_id = "
            "(SELECT id FROM auxiliares_categorias WHERE clave='institucion_productora')"
        ).fetchall()
        conn.close()
        assert estado_1 == estado_2
