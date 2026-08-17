"""utils/ui_mensajes.py — Mensajes y limpieza de formularios que sobreviven a un st.rerun().

Dos problemas de Streamlit que este módulo resuelve:

1. Mensaje silencioso: llamar a st.success()/st.warning() y luego a
   st.rerun() en el mismo ciclo de ejecución descarta el mensaje antes de
   que llegue a pintarse — el rerun interrumpe el render. Solución:
   guardar el mensaje en session_state ANTES del rerun y mostrarlo (y
   limpiarlo) recién en el siguiente ciclo, ya con la página recargada.
   Además, cada mensaje puede llevar una "sección" para mostrarse junto
   al bloque de la pantalla que lo originó, en vez de siempre arriba.

2. "cannot be modified after the widget... is instantiated": Streamlit
   prohíbe asignar st.session_state[key] en el MISMO ciclo en que el
   widget con esa key ya fue instanciado (por ejemplo, para "limpiar" un
   campo justo después de leer su valor). Solución: registrar qué claves
   limpiar y aplicarlas recién al inicio del siguiente ciclo, ANTES de
   que se vuelvan a crear los widgets.
"""

import streamlit as st

_CLAVE_FLASH = "_flash_mensaje"
_CLAVE_LIMPIAR = "_limpieza_pendiente"


def marcar_mensaje(tipo: str, texto: str, seccion: str | None = None) -> None:
    """Guarda un mensaje para mostrarlo después del próximo st.rerun().

    tipo: 'success' | 'warning' | 'error' | 'info' (nombre de un método de st).
    seccion: identificador opcional del bloque de la vista donde debe
    aparecer (ver mostrar_mensaje_pendiente). Si se omite, el mensaje se
    considera "global" y lo mostrará la primera llamada a
    mostrar_mensaje_pendiente() sin importar la sección.
    Debe llamarse justo antes de invocar st.rerun().
    """
    st.session_state[_CLAVE_FLASH] = (tipo, texto, seccion)


def mostrar_mensaje_pendiente(seccion: str | None = None) -> None:
    """Muestra (y limpia) el mensaje guardado por marcar_mensaje, si aplica.

    Un mensaje guardado sin sección (global) se muestra y consume en la
    PRIMERA llamada a esta función, sin importar qué `seccion` reciba esa
    llamada. Un mensaje guardado CON sección solo se muestra y consume
    cuando `seccion` coincide exactamente; en caso contrario se deja
    intacto para que la sección correcta lo consuma más adelante en el
    mismo render — en particular, una llamada genérica sin `seccion` (p.
    ej. al tope de la vista) NO debe "robarse" un mensaje marcado para
    otra sección. Colocar esta llamada justo debajo del encabezado de cada
    bloque de la vista para que el mensaje aparezca ahí, no arriba de toda
    la página.
    """
    flash = st.session_state.get(_CLAVE_FLASH)
    if not flash:
        return
    tipo, texto, flash_seccion = flash
    if flash_seccion is not None and flash_seccion != seccion:
        return
    st.session_state.pop(_CLAVE_FLASH, None)
    getattr(st, tipo)(texto)


def marcar_limpieza(valores: dict) -> None:
    """Registra valores a asignar a session_state ANTES de que se vuelvan a
    instanciar los widgets correspondientes, en el próximo rerun.

    Debe llamarse en la rama de éxito, justo antes de st.rerun() — nunca
    intentar asignar st.session_state[key] directamente ahí mismo, porque
    el widget con esa key ya fue instanciado en este ciclo y Streamlit lo
    rechaza con StreamlitAPIException.
    """
    pendientes = st.session_state.get(_CLAVE_LIMPIAR, {})
    pendientes.update(valores)
    st.session_state[_CLAVE_LIMPIAR] = pendientes


def aplicar_limpieza_pendiente() -> None:
    """Aplica los valores registrados por marcar_limpieza(). Debe llamarse
    al INICIO de la función de vista, antes de instanciar cualquier
    widget (el orden importa: si se llama después de crear un widget con
    una de esas keys, Streamlit lanzará la misma excepción que se busca
    evitar)."""
    pendientes = st.session_state.pop(_CLAVE_LIMPIAR, None)
    if pendientes:
        for clave, valor in pendientes.items():
            st.session_state[clave] = valor
