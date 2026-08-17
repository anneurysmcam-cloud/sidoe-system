"""
tests/test_43_crud_usuarios.py
================================
Cobertura de models/crud_usuarios.py, extraído de views/admin_usuarios.py
(Hallazgo A del Informe de Auditoría Arquitectónica, agosto 2026).

Cubre también la defensa en profundidad de rol (Hallazgo D del mismo
informe): cada función de escritura debe rechazar un ``rol_actor`` que no
sea 'administrador', sin depender de Streamlit.
"""

import pytest

from models.crud_usuarios import (
    activar_usuario,
    cambiar_rol_usuario,
    desactivar_usuario,
    eliminar_usuario,
    exigir_2fa,
    listar_usuarios,
    obtener_totp_habilitado,
    quitar_exigencia_2fa,
)
from security.auth import registrar_usuario
from security.autorizacion import RolNoAutorizadoError


@pytest.fixture
def usuario_prueba(sidoe_config):
    """Crea un usuario 'editor' de prueba y devuelve su id."""
    registrar_usuario("p43_editor_prueba", "Clave123!", rol="editor")
    filas = listar_usuarios()
    return next(u["id"] for u in filas if u["username"] == "p43_editor_prueba")


# ---------------------------------------------------------------------------
# Lecturas
# ---------------------------------------------------------------------------

def test_listar_usuarios_incluye_columnas_esperadas(sidoe_config, usuario_prueba):
    filas = listar_usuarios()
    assert any(u["id"] == usuario_prueba for u in filas)
    fila = next(u for u in filas if u["id"] == usuario_prueba)
    assert set(fila.keys()) == {
        "id", "username", "rol", "activo", "fecha_creacion", "requiere_2fa",
        "totp_habilitado", "eliminaciones_recientes",
    }
    assert fila["rol"] == "editor"


def test_obtener_totp_habilitado_por_defecto_es_falso(sidoe_config, usuario_prueba):
    assert obtener_totp_habilitado(usuario_prueba) is False


def test_obtener_totp_habilitado_usuario_inexistente_es_falso(sidoe_config):
    assert obtener_totp_habilitado(999999) is False


# ---------------------------------------------------------------------------
# Escrituras — comportamiento correcto con rol_actor='administrador'
# ---------------------------------------------------------------------------

def test_cambiar_rol_usuario_ok(sidoe_config, usuario_prueba):
    ok, mensaje = cambiar_rol_usuario(usuario_prueba, "supervisor", "administrador")
    assert ok is True
    fila = next(u for u in listar_usuarios() if u["id"] == usuario_prueba)
    assert fila["rol"] == "supervisor"


def test_desactivar_y_activar_usuario_ok(sidoe_config, usuario_prueba):
    ok, _ = desactivar_usuario(usuario_prueba, "administrador")
    assert ok is True
    assert next(u for u in listar_usuarios() if u["id"] == usuario_prueba)["activo"] == 0

    ok, _ = activar_usuario(usuario_prueba, "administrador")
    assert ok is True
    assert next(u for u in listar_usuarios() if u["id"] == usuario_prueba)["activo"] == 1


def test_exigir_y_quitar_2fa_ok(sidoe_config, usuario_prueba):
    ok, _ = exigir_2fa(usuario_prueba, "administrador")
    assert ok is True
    assert next(u for u in listar_usuarios() if u["id"] == usuario_prueba)["requiere_2fa"] == 1

    ok, _ = quitar_exigencia_2fa(usuario_prueba, "administrador")
    assert ok is True
    assert next(u for u in listar_usuarios() if u["id"] == usuario_prueba)["requiere_2fa"] == 0


def test_eliminar_usuario_ok(sidoe_config, usuario_prueba):
    ok, _ = eliminar_usuario(usuario_prueba, "administrador")
    assert ok is True
    assert not any(u["id"] == usuario_prueba for u in listar_usuarios())


# ---------------------------------------------------------------------------
# Escrituras — defensa en profundidad de rol (Hallazgo D)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None, ""])
def test_cambiar_rol_usuario_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        cambiar_rol_usuario(usuario_prueba, "supervisor", rol_actor)
    # No debe haber mutado nada.
    fila = next(u for u in listar_usuarios() if u["id"] == usuario_prueba)
    assert fila["rol"] == "editor"


@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None])
def test_eliminar_usuario_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        eliminar_usuario(usuario_prueba, rol_actor)
    assert any(u["id"] == usuario_prueba for u in listar_usuarios())


@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None])
def test_desactivar_usuario_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        desactivar_usuario(usuario_prueba, rol_actor)
    assert next(u for u in listar_usuarios() if u["id"] == usuario_prueba)["activo"] == 1


@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None])
def test_activar_usuario_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        activar_usuario(usuario_prueba, rol_actor)


@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None])
def test_exigir_2fa_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        exigir_2fa(usuario_prueba, rol_actor)


@pytest.mark.parametrize("rol_actor", ["editor", "supervisor", None])
def test_quitar_exigencia_2fa_rechaza_rol_no_autorizado(sidoe_config, usuario_prueba, rol_actor):
    with pytest.raises(RolNoAutorizadoError):
        quitar_exigencia_2fa(usuario_prueba, rol_actor)
