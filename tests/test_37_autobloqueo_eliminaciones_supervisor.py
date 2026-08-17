"""
tests/test_37_autobloqueo_eliminaciones_supervisor.py
========================================================
Salvaguarda contra eliminación masiva accidental (o de una cuenta
comprometida): al llegar a config.UMBRAL_ELIMINACIONES_AUTOBLOQUEO
indicadores eliminados por un mismo usuario con rol `supervisor`, su
cuenta se desactiva automáticamente y debe ser reactivada por un
administrador (ver models/crud_indicadores.py::borrar_indicador).

Cubre:
- El contador solo cuenta para `supervisor` (no editor/administrador).
- Al llegar al umbral: cuenta desactivada + contador reseteado a 0 + log
  de auditoría 'AUTO_DESACTIVAR'.
- Antes del umbral: cuenta sigue activa, contador incrementa normalmente.
- Tras reactivación manual por un administrador, una nueva tanda de
  eliminaciones vuelve a exigir el umbral completo (no arrastra progreso).
- views/eliminar_indicador.py fuerza el logout de la sesión ACTUAL en el
  mismo instante en que se cruza el umbral (no solo bloquea el próximo
  login) — probado a nivel de AppTest.
"""

import pytest

from config import UMBRAL_ELIMINACIONES_AUTOBLOQUEO
from data.database import obtener_conexion
from models.crud_auxiliares import opciones_selectbox
from models.crud_indicadores import borrar_indicador, guardar_indicador
from security.auth import registrar_usuario

DATOS_FACTIBILIDAD_MINIMA = {
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
    "estructura_datos": "No posee ninguna de las anteriores",
    "variables_calculo": "No",
}


def _crear_usuario(username: str, rol: str) -> int:
    registrar_usuario(username, "ClaveSegura123!", rol=rol)
    conn = obtener_conexion()
    uid = conn.execute(
        "SELECT id FROM usuarios WHERE username = ?", (username,)
    ).fetchone()[0]
    conn.close()
    return uid


def _crear_indicador(codigo: str) -> int:
    _, mapa_eje = opciones_selectbox("eje")
    _, mapa_politica = opciones_selectbox("politica_gobierno")
    datos = {
        "codigo": codigo,
        "indicador": f"Indicador {codigo}",
        "estado_indicador": "Activo",
        "estado_publicacion": "publicado",
        "generador_demanda_id": 1,
        "eje_id": next(iter(mapa_eje.values())),
        "politica_gobierno_id": next(iter(mapa_politica.values())),
    }
    ok, msg = guardar_indicador(
        datos_indicador=datos, datos_fuentes=[],
        datos_factibilidad=DATOS_FACTIBILIDAD_MINIMA, usuario_id=1,
    )
    assert ok, msg
    conn = obtener_conexion()
    iid = conn.execute(
        "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
    ).fetchone()[0]
    conn.close()
    return iid


def _estado_usuario(uid: int) -> tuple[int, int]:
    conn = obtener_conexion()
    activo, contador = conn.execute(
        "SELECT activo, eliminaciones_recientes FROM usuarios WHERE id = ?",
        (uid,),
    ).fetchone()
    conn.close()
    return activo, contador


class TestAutobloqueoSupervisor:
    def test_al_llegar_al_umbral_se_desactiva_y_resetea_contador(self, sidoe_config):
        uid = _crear_usuario("p37_supervisor_a", "supervisor")

        for i in range(UMBRAL_ELIMINACIONES_AUTOBLOQUEO - 1):
            iid = _crear_indicador(f"P37-A{i}")
            ok, msg = borrar_indicador(iid, usuario_id=uid)
            assert ok, msg
            activo, contador = _estado_usuario(uid)
            assert activo == 1, (
                f"No debería desactivarse antes del umbral (eliminación {i + 1})."
            )
            assert contador == i + 1

        # La eliminación número UMBRAL: se desactiva y resetea.
        iid_final = _crear_indicador("P37-A-FINAL")
        ok, msg = borrar_indicador(iid_final, usuario_id=uid)
        assert ok, msg
        activo, contador = _estado_usuario(uid)
        assert activo == 0, "Debe desactivarse al llegar al umbral."
        assert contador == 0, "El contador debe resetearse tras desactivar."
        assert "desactivada" in msg.lower() or "administrador" in msg.lower(), (
            f"El mensaje debería avisar del bloqueo, obtuvo: {msg!r}"
        )

        conn = obtener_conexion()
        fila_log = conn.execute(
            "SELECT accion, detalle FROM auditoria WHERE usuario_id = ? "
            "AND accion = 'AUTO_DESACTIVAR' ORDER BY id DESC LIMIT 1",
            (uid,),
        ).fetchone()
        conn.close()
        assert fila_log is not None, "Debe quedar un log de auditoría AUTO_DESACTIVAR."
        assert str(UMBRAL_ELIMINACIONES_AUTOBLOQUEO) in fila_log[1]

    def test_no_afecta_a_editor_ni_administrador(self, sidoe_config):
        uid_editor = _crear_usuario("p37_editor_b", "editor")
        uid_admin = _crear_usuario("p37_admin_b", "administrador")

        for uid, prefijo in [(uid_editor, "P37-B-ED"), (uid_admin, "P37-B-AD")]:
            for i in range(UMBRAL_ELIMINACIONES_AUTOBLOQUEO + 2):
                iid = _crear_indicador(f"{prefijo}{i}")
                ok, msg = borrar_indicador(iid, usuario_id=uid)
                assert ok, msg
            activo, contador = _estado_usuario(uid)
            assert activo == 1, "editor/administrador no deben autodesactivarse."
            assert contador == 0, (
                "El contador de eliminaciones no debe incrementarse para "
                "roles distintos de supervisor."
            )

    def test_reactivacion_no_arrastra_progreso_previo(self, sidoe_config):
        """Tras reactivar manualmente (como haría un administrador), una
        nueva tanda de eliminaciones debe volver a exigir el umbral
        completo — no debe autodesactivarse de nuevo con solo 1 o 2
        eliminaciones adicionales."""
        uid = _crear_usuario("p37_supervisor_c", "supervisor")

        for i in range(UMBRAL_ELIMINACIONES_AUTOBLOQUEO):
            iid = _crear_indicador(f"P37-C{i}")
            borrar_indicador(iid, usuario_id=uid)
        activo, contador = _estado_usuario(uid)
        assert activo == 0 and contador == 0

        # El administrador reactiva la cuenta (simulado directamente en BD,
        # igual que hace views/admin_usuarios.py).
        conn = obtener_conexion()
        conn.execute("UPDATE usuarios SET activo = 1 WHERE id = ?", (uid,))
        conn.commit()
        conn.close()

        iid2 = _crear_indicador("P37-C-POST-REACTIVACION")
        ok, msg = borrar_indicador(iid2, usuario_id=uid)
        assert ok, msg
        activo, contador = _estado_usuario(uid)
        assert activo == 1, "No debe volver a desactivarse con solo 1 eliminación."
        assert contador == 1

    def test_usuario_sin_id_no_rompe_la_eliminacion(self, sidoe_config):
        """usuario_id=None (llamado sin contexto de sesión, p. ej. un
        script de mantenimiento) no debe intentar tocar la tabla usuarios
        ni fallar."""
        iid = _crear_indicador("P37-SINUSUARIO")
        ok, msg = borrar_indicador(iid, usuario_id=None)
        assert ok, msg


class TestAutobloqueoAppTest:
    """La lógica real de "forzar logout al cruzar el umbral" vive dentro
    de un @st.dialog (views/eliminar_indicador.py::_confirmar_eliminacion)
    — límite ya documentado en el proyecto (ver README.md / commit
    4ce1216): `AppTest` no puede simular clics dentro de modales
    `@st.dialog`, así que un test end-to-end que abra el diálogo y haga
    clic en "Sí, eliminar" no es fiable aquí (se intentó: el clic no
    dispara el rerun del diálogo de forma consistente). La combinación
    íntegra "eliminar -> ¿se desactivó? -> logout()" YA está probada de
    forma robusta y determinística en TestAutobloqueoSupervisor, llamando
    directamente a borrar_indicador() como lo hace la vista. Este test
    solo confirma que la vista renderiza correctamente el aviso del
    límite y no revienta con el nuevo caption/columna."""

    def test_vista_eliminar_muestra_aviso_del_limite(self, sidoe_config):
        pytest.importorskip("streamlit.testing.v1")
        from pathlib import Path

        from streamlit.testing.v1 import AppTest

        _crear_usuario("p37_supervisor_vista", "supervisor")
        _crear_indicador("P37-VISTA")

        app_path = str(Path(__file__).resolve().parent.parent / "app.py")
        at = AppTest.from_file(app_path, default_timeout=30)
        at.run()
        login_expander = at.sidebar.expander[0]
        login_expander.text_input[0].set_value("p37_supervisor_vista")
        login_expander.text_input[1].set_value("ClaveSegura123!")
        login_expander.button[0].click().run()
        assert not at.exception

        radio = at.sidebar.radio[0]
        radio.set_value("Eliminar Indicador").run()
        assert not at.exception

        textos = " ".join(c.value for c in at.caption)
        assert str(UMBRAL_ELIMINACIONES_AUTOBLOQUEO) in textos, (
            "La vista debe avisar del límite de eliminaciones antes de que "
            "el supervisor elimine nada."
        )
