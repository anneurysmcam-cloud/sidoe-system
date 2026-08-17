"""
tests/test_27_ver_auditoria_paginacion.py
==========================================
Test de regresión — views/ver_auditoria.py dejó de cargar la tabla completa
en memoria y pasó a paginar/filtrar a nivel SQL (LIMIT/OFFSET + WHERE
parametrizado), ya que `auditoria` solo crece con el tiempo.

Cubre:
  - _construir_filtros genera la cláusula WHERE y los parámetros correctos
    (incluyendo escape de comodines LIKE % y _ en el username).
  - Consultar con LIMIT/OFFSET contra una BD real (fixture db_conn) trae
    exactamente el conteo esperado y respeta el orden DESC por timestamp.
"""

import pytest

from views.ver_auditoria import _ACCIONES_INDICADOR, _ACCIONES_SEGURIDAD, _ACCIONES_USUARIO, _construir_filtros


def test_sin_filtros_no_genera_clausula_where():
    clausula, params = _construir_filtros("", "")
    assert clausula == ""
    assert params == []


def test_filtro_usuario_genera_like_parametrizado():
    clausula, params = _construir_filtros("randy", "")
    assert "u.username LIKE ?" in clausula
    assert params == ["%randy%"]


def test_filtro_accion_genera_igualdad_parametrizada():
    clausula, params = _construir_filtros("", "CREAR")
    assert "a.accion = ?" in clausula
    assert params == ["CREAR"]


def test_filtro_combinado_usa_and():
    clausula, params = _construir_filtros("randy", "CREAR")
    assert " AND " in clausula
    assert params == ["%randy%", "CREAR"]


def test_escape_de_comodines_like_en_username():
    # Un username con % o _ no debe comportarse como comodín SQL.
    clausula, params = _construir_filtros("50%_off", "")
    assert params == ["%50\\%\\_off%"]
    assert "ESCAPE '\\'" in clausula


def test_paginacion_limit_offset_respeta_conteo_y_orden(db_conn):
    # Sembrar registros de auditoría con timestamps distintos.
    for i in range(7):
        db_conn.execute(
            "INSERT INTO auditoria (usuario_id, accion, detalle, timestamp) "
            "VALUES (1, 'CREAR', ?, datetime('now', ? || ' seconds'))",
            (f"detalle-{i}", i),
        )
    db_conn.commit()

    clausula, params = _construir_filtros("", "")
    fila = db_conn.execute(
        f"SELECT COUNT(*) FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id {clausula}",
        params,
    ).fetchone()
    total = fila[0]
    assert total >= 7

    pagina = db_conn.execute(
        f"""
        SELECT a.detalle FROM auditoria a
        LEFT JOIN usuarios u ON u.id = a.usuario_id
        {clausula}
        ORDER BY a.timestamp DESC
        LIMIT ? OFFSET ?
        """,
        [*params, 3, 0],
    ).fetchall()
    assert len(pagina) == 3
    # El más reciente (i=6) debe ir primero.
    assert pagina[0][0] == "detalle-6"


# ---------------------------------------------------------------------------
# Bug reportado por Randy: el filtro "Filtrar por acción" no incluía todas
# las acciones que realmente se registran en `auditoria` (el flujo de
# aprobación, la sincronización de referenciados, y las acciones de
# seguridad/2FA) — se veían en el listado general pero no se podían
# filtrar por ellas.
# ---------------------------------------------------------------------------

def test_acciones_de_indicador_incluye_flujo_de_aprobacion():
    for accion in ("APROBAR_PUBLICACION", "SINCRONIZAR_REFERENCIA", "AUTO_DESACTIVAR"):
        assert accion in _ACCIONES_INDICADOR, (
            f"'{accion}' se registra en auditoría (ver models/crud_indicadores.py) "
            f"pero no está en _ACCIONES_INDICADOR, así que no se puede filtrar por ella."
        )


def test_acciones_de_usuario_incluye_password():
    for accion in ("CAMBIAR_PASSWORD", "RESET_PASSWORD_ADMIN"):
        assert accion in _ACCIONES_USUARIO


def test_acciones_de_seguridad_incluye_2fa_y_sistema():
    for accion in (
        "ACTIVAR_2FA", "DESACTIVAR_2FA", "DESACTIVAR_2FA_ADMIN",
        "ACTIVAR_2FA_OBLIGATORIO", "EXIGIR_2FA", "QUITAR_EXIGENCIA_2FA",
        "TOTP_CODIGOS_REGENERADOS", "TOTP_CODIGO_RESPALDO_USADO", "BACKUP_DB",
    ):
        assert accion in _ACCIONES_SEGURIDAD


# ---------------------------------------------------------------------------
# Bug reportado por Randy: al activar/desactivar un usuario, el detalle de
# auditoría solo guardaba el ID del usuario afectado ("id=5"), no su
# nombre — a diferencia de otras acciones (p. ej. ELIMINAR_USUARIO) que sí
# lo incluían.
# ---------------------------------------------------------------------------

def test_activar_usuario_registra_username_en_el_detalle(sidoe_config, db_conn):
    pytest.importorskip("streamlit.testing.v1")
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from security.auth import registrar_usuario

    registrar_usuario("editor_p27_activar", "ClaveSegura123!", rol="editor")
    uid = db_conn.execute(
        "SELECT id FROM usuarios WHERE username = ?", ("editor_p27_activar",)
    ).fetchone()[0]
    db_conn.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (uid,))
    db_conn.commit()

    app_path = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app_path, default_timeout=30)
    at.session_state["usuario"] = {
        "id": 1, "username": "p27_admin_apptest", "rol": "administrador",
    }
    at.run()
    at.sidebar.radio[0].set_value("Administrar Usuarios").run()
    assert not at.exception

    sel = next(sb for sb in at.selectbox if sb.label == "Selecciona usuario por ID")
    sel.set_value(uid).run()
    assert not at.exception

    boton = next(b for b in at.button if "Activar" in b.label)
    boton.click().run()
    assert not at.exception

    detalle = db_conn.execute(
        "SELECT detalle FROM auditoria WHERE accion = 'ACTIVAR_USUARIO' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert "editor_p27_activar" in detalle, (
        f"El detalle de ACTIVAR_USUARIO no incluye el nombre del usuario "
        f"afectado, solo: {detalle!r}"
    )
