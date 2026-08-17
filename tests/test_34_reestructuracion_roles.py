"""
tests/test_34_reestructuracion_roles.py
========================================
Tests de regresión — reestructuración de roles (agosto-2026): rol
'supervisor' nuevo, migración del CHECK de usuarios.rol, y el flujo de
aprobación de publicación (aprobar_publicacion_indicador).

No repite la cobertura genérica de require_role() (ver
tests/test_13_auth_flujos_completos.py::TestRequireRole) — se enfoca en lo
específico de este cambio.
"""

import pytest

from models.crud_indicadores import aprobar_publicacion_indicador, guardar_indicador
from security.auth import registrar_usuario


# ---------------------------------------------------------------------------
# registrar_usuario con el nuevo rol 'supervisor'
# ---------------------------------------------------------------------------

class TestRolSupervisor:

    def test_registrar_usuario_con_rol_supervisor(self, sidoe_config):
        registrar_usuario("supervisor_test", "Contrasena!Fuerte99", "supervisor")

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT rol, activo FROM usuarios WHERE username = ?",
            ("supervisor_test",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "supervisor"
        assert row[1] == 1

    def test_check_constraint_de_bd_acepta_supervisor(self, sidoe_config, db_conn):
        """Confirma que la migración migrar_rol_supervisor() reconstruyó el
        CHECK de usuarios.rol incluyendo 'supervisor' (inserción directa por
        SQL, sin pasar por registrar_usuario)."""
        db_conn.execute(
            "INSERT INTO usuarios (username, password_hash, rol, activo) "
            "VALUES ('sup_directo', 'hash-de-prueba', 'supervisor', 1)"
        )
        db_conn.commit()
        row = db_conn.execute(
            "SELECT rol FROM usuarios WHERE username = 'sup_directo'"
        ).fetchone()
        assert row["rol"] == "supervisor"

    def test_check_constraint_sigue_rechazando_rol_invalido(self, sidoe_config, db_conn):
        with pytest.raises(Exception):
            db_conn.execute(
                "INSERT INTO usuarios (username, password_hash, rol, activo) "
                "VALUES ('rol_invalido', 'hash', 'superadmin', 1)"
            )
            db_conn.commit()


# ---------------------------------------------------------------------------
# aprobar_publicacion_indicador — flujo de aprobación borrador -> publicado
# ---------------------------------------------------------------------------

def _crear_indicador_borrador(codigo: str) -> int:
    """Crea un indicador mínimo en estado 'borrador' y devuelve su id."""
    datos_indicador = {
        "codigo": codigo,
        "indicador": f"Indicador de prueba {codigo}",
        "estado_publicacion": "borrador",
    }
    datos_fuentes = [{}]
    datos_factibilidad = {}
    exito, msg = guardar_indicador(datos_indicador, datos_fuentes, datos_factibilidad)
    assert exito, msg

    import data.database as db_mod
    conn = db_mod.obtener_conexion()
    row = conn.execute(
        "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
    ).fetchone()
    conn.close()
    return row[0]


class TestAprobarPublicacionIndicador:

    def test_aprueba_indicador_en_borrador(self, sidoe_config):
        id_ind = _crear_indicador_borrador("APR-001")

        exito, msg = aprobar_publicacion_indicador(id_ind, usuario_id=1)
        assert exito is True
        assert "aprobado" in msg.lower() or "publicado" in msg.lower()

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        estado = conn.execute(
            "SELECT estado_publicacion FROM indicadores WHERE id = ?", (id_ind,)
        ).fetchone()[0]
        conn.close()
        assert estado == "publicado"

    def test_rechaza_indicador_ya_publicado(self, sidoe_config):
        id_ind = _crear_indicador_borrador("APR-002")
        exito1, _ = aprobar_publicacion_indicador(id_ind, usuario_id=1)
        assert exito1 is True

        exito2, msg2 = aprobar_publicacion_indicador(id_ind, usuario_id=1)
        assert exito2 is False
        assert "ya está publicado" in msg2

    def test_indicador_inexistente_devuelve_error(self, sidoe_config):
        exito, msg = aprobar_publicacion_indicador(999999, usuario_id=1)
        assert exito is False
        assert "no encontrado" in msg.lower()

    def test_no_modifica_otros_campos_del_indicador(self, sidoe_config):
        """La aprobación solo debe tocar estado_publicacion; el nombre y el
        código deben permanecer intactos."""
        id_ind = _crear_indicador_borrador("APR-003")
        aprobar_publicacion_indicador(id_ind, usuario_id=1)

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        row = conn.execute(
            "SELECT codigo, indicador, estado_indicador FROM indicadores WHERE id = ?",
            (id_ind,),
        ).fetchone()
        conn.close()
        assert row[0] == "APR-003"
        assert row[1] == "Indicador de prueba APR-003"
        assert row[2] == "Activo"

    def test_queda_registrado_en_auditoria(self, sidoe_config):
        id_ind = _crear_indicador_borrador("APR-004")
        aprobar_publicacion_indicador(id_ind, usuario_id=1)

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        fila = conn.execute(
            "SELECT accion, detalle FROM auditoria WHERE accion = 'APROBAR_PUBLICACION' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert fila is not None
        assert "APR-004" in fila[1]
