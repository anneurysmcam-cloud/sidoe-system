"""
security/autorizacion.py
=========================
Verificación de rol como defensa en profundidad para funciones de escritura
sensibles de ``models/`` (Hallazgo D del Informe de Auditoría Arquitectónica,
agosto 2026).

Deliberadamente SIN dependencia de Streamlit ni de ``st.session_state``: a
diferencia de ``security.auth.require_role`` (decorador pensado para
``views/*.py``, que lee la sesión de Streamlit y corta la ejecución con
``st.stop()``), esta verificación recibe el rol del actor como parámetro
explícito. Esto es intencional por dos motivos:

1. Mantiene ``models/`` desacoplado de Streamlit — una de las fortalezas
   arquitectónicas del sistema (ver informe, §28) — para que siga siendo
   reutilizable fuera de la UI (p. ej. una futura API) sin arrastrar un
   framework de presentación.
2. No reemplaza a ``@require_role``: es un respaldo. La vista sigue siendo
   la primera línea de defensa; esta verificación solo evita que un bug en
   la vista (o una llamada directa a ``models/`` en el futuro, p. ej. desde
   una API) deje pasar una escritura sin el rol correcto.
"""


class RolNoAutorizadoError(PermissionError):
    """Se lanza cuando el rol del actor no está entre los roles permitidos
    para una operación de escritura sensible de ``models/``."""


def verificar_rol(rol_actor: str | None, roles_permitidos: list[str]) -> None:
    """Verifica que ``rol_actor`` esté entre ``roles_permitidos``.

    Args:
        rol_actor: Rol del usuario que ejecuta la operación (tal como lo
            conoce el llamador, típicamente desde ``st.session_state`` en
            la vista). ``None`` siempre es rechazado.
        roles_permitidos: Roles autorizados para la operación.

    Raises:
        RolNoAutorizadoError: Si ``rol_actor`` es ``None`` o no está en
            ``roles_permitidos``.
    """
    if rol_actor is None or rol_actor not in roles_permitidos:
        raise RolNoAutorizadoError(
            f"Rol no autorizado para esta operación. Se requiere uno de: "
            f"{', '.join(roles_permitidos)}."
        )
