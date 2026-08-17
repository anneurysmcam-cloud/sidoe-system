"""
views/landing.py
=================
Landing institucional pública de SIDOE — pantalla de bienvenida que se
muestra a quien entra sin sesión iniciada, antes de las vistas públicas de
solo lectura (Consulta / Ficha / Dashboard).

Cómo se activa (ver app.py)
----------------------------
Un flag en ``session_state`` (``landing_dismissed``) controla su visibilidad:
la primera vez que un visitante sin sesión llega a la app, ve esta pantalla
en vez del radio de opciones. Al pulsar cualquiera de los tres accesos
rápidos se marca ``landing_dismissed = True`` y se preselecciona esa opción
en el radio — no se vuelve a mostrar en esa sesión de navegador, salvo que
el visitante pulse "🏠 Inicio" en el sidebar (ver ``_mostrar_boton_inicio``
en app.py), que resetea el flag.

Impacto en rendimiento: prácticamente nulo. No hay consultas a la base de
datos ni imports pesados (pandas/plotly/fpdf2) — solo HTML/CSS estático y
tres ``st.button()``. El logo se lee una única vez por proceso gracias a
``st.cache_data``.
"""

import base64
from pathlib import Path

import streamlit as st

_LOGO_PATH = Path(__file__).resolve().parent.parent / "tracking" / "logo_one.png"

# Los 4 generadores de demanda oficiales del sistema (ver
# CAMPOS_HIBRIDOS_INDICADORES en config.py — mismos 4 valores del Excel
# matriz). El nombre completo es solo descriptivo para esta pantalla; el
# valor que se guarda en BD sigue siendo la sigla exacta ("END", "ODS",
# "CMV", "PNPSP") y nunca debe tocarse desde aquí.
_GENERADORES_DEMANDA = [
    ("END", "Estrategia Nacional de Desarrollo 2030"),
    ("ODS", "Objetivos de Desarrollo Sostenible"),
    ("PNPSP", "Plan Nacional Plurianual del Sector Público"),
    ("CMV", "Consenso de Montevideo sobre Población y Desarrollo"),
]

_OPCION_POR_BOTON = {
    "consultar": "Generar Consulta",
    "dashboard": "Dashboard",
    "ficha": "Generar Ficha",
}


@st.cache_data(show_spinner=False)
def _logo_base64() -> str:
    """Lee y codifica el logo oficial una sola vez por proceso."""
    return base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")


def _inyectar_estilos() -> None:
    st.markdown(
        """
        <style>
        /* Todo el contenido de la landing (hero, tarjetas, stats, botones)
           se centra dentro de un ancho máximo fijo. Con layout="wide" el
           contenedor principal ocupa casi todo el ancho de la ventana; sin
           este límite, el contenido queda pegado a la izquierda en
           monitores anchos en vez de leerse como una landing centrada.
           IMPORTANTE — causa real de que el centrado no se aplicara en
           intentos anteriores: el selector usado antes,
           `div[data-testid="stAppViewContainer"] .block-container`, dejó de
           coincidir con nada. Streamlit 1.60 (versión pineada del proyecto,
           ver requirements.lock) renombró el testid del contenedor
           principal a `stMainBlockContainer` y ya no expone la clase
           `.block-container` en el DOM — se verificó extrayendo los
           bundles JS del paquete instalado (grep sobre static/*.js). La
           regla de max-width/margin:auto nunca fallaba por lógica de CSS;
           fallaba porque el selector no encontraba ningún elemento al que
           aplicarse. Se apunta ahora al testid correcto, y se deja el
           selector viejo como respaldo por si el proyecto se ejecuta algún
           día con una versión de Streamlit anterior donde sí exista. */
        div[data-testid="stMainBlockContainer"],
        div[data-testid="stAppViewContainer"] .block-container {
            padding-top: 1.2rem;
            max-width: 1180px;
            margin-left: auto !important;
            margin-right: auto !important;
        }

        /* Mensaje flash (p. ej. "Tu sesión ha expirado por inactividad")
           pintado por mostrar_mensaje_pendiente() ANTES de la landing:
           queda como primer elemento dentro del block-container, cuyo
           padding-top se redujo arriba a 1.2rem para que el hero no
           quedara con demasiado aire encima. Ese padding reducido no
           alcanza para despejar el header fijo de Streamlit
           (data-testid="stAppHeader"/"stHeader"), así que sin este margen
           extra la alerta queda parcialmente tapada/cortada debajo del
           header — exactamente el bug reportado en las capturas. Se le da
           margen propio solo a la alerta (no se toca el padding general)
           para no volver a alejar el hero de la parte superior cuando no
           hay ningún mensaje pendiente. Se cubren el testid actual
           (stAlertContainer) y el legado (stAlert) por la misma razón de
           compatibilidad entre versiones documentada arriba. */
        div[data-testid="stAlertContainer"],
        div[data-testid="stAlert"] {
            margin-top: 2.4rem;
        }

        .sidoe-hero {
            /* Colores institucionales fijos (no color-mix): color-mix() no
               se renderiza de forma confiable en todos los navegadores /
               vistas embebidas, y cuando falla el navegador simplemente
               ignora la propiedad completa (fondo, borde y sombra
               desaparecen a la vez), dejando la tarjeta "sin cuadro".
               Con valores fijos el recuadro azul institucional siempre se
               ve, en cualquier navegador. */
            background: radial-gradient(circle at 15% 0%, #0f3a7a 0%, #08214e 45%, #04102b 100%);
            border-radius: 20px;
            padding: 2.9rem 2rem 2.5rem 2rem;
            text-align: center !important;
            margin-bottom: 1.6rem;
            border: 1px solid rgba(127, 178, 255, 0.35);
            box-shadow:
                0 12px 30px rgba(2, 10, 30, 0.45),
                0 0 0 1px rgba(0, 47, 108, 0.25);
        }
        .sidoe-hero-badge {
            width: 84px;
            height: 84px;
            border-radius: 18px;
            background: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 1.1rem auto;
            padding: 10px;
            border: 1px solid rgba(0, 47, 108, 0.15);
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.25);
        }
        .sidoe-hero-badge img { width: 100%; height: 100%; object-fit: contain; }
        /* Los 4 textos del hero se fuerzan con !important en color Y en
           text-align. Streamlit reaplica estilos de tema propios sobre los
           encabezados/párrafos que genera vía st.markdown (con una
           especificidad que gana sobre una clase simple) cada vez que el
           visitante cambia de modo claro/oscuro desde su selector nativo
           (☰ → Settings → Theme) — por eso el título "SIDOE" volvía a
           tomar el color de texto del tema en vez de quedarse blanco, y por
           lo que el text-align: center heredado del contenedor padre no
           siempre ganaba. Con !important aquí, ninguno de los dos vuelve a
           cambiar sin importar el tema activo. */
        .sidoe-hero-eyebrow {
            color: #7fb2ff !important;
            font-weight: 700;
            font-size: 0.78rem;
            letter-spacing: 2.2px;
            text-transform: uppercase;
            text-align: center !important;
            margin: 0 0 0.6rem 0;
        }
        .sidoe-hero-title {
            color: #ffffff !important;
            font-family: 'Helvetica Neue', sans-serif;
            font-weight: 800;
            font-size: 3.6rem;
            letter-spacing: 1.5px;
            line-height: 1.05;
            text-align: center !important;
            margin: 0 0 0.5rem 0;
        }
        .sidoe-hero-subtitle {
            color: rgba(255,255,255,0.92) !important;
            font-weight: 600;
            font-size: 1.15rem;
            letter-spacing: 0.2px;
            text-align: center !important;
            margin: 0 0 1.1rem 0;
        }
        .sidoe-hero-tagline {
            color: rgba(255,255,255,0.85) !important;
            font-size: 0.98rem;
            line-height: 1.55;
            text-align: center !important;
            max-width: 640px;
            /* !important es imprescindible aquí, no solo cosmético como en
               los demás textos del hero: a diferencia del badge/eyebrow/
               título/subtítulo (que ocupan el ancho completo de la tarjeta
               y no necesitan más que text-align para verse centrados), este
               párrafo tiene max-width y por lo tanto DEPENDE de que
               margin:auto se aplique para no quedar pegado al borde
               izquierdo. Streamlit reaplica su propio margin por defecto
               sobre los <p> generados vía st.markdown con más especificidad
               que esta clase, así que sin !important el auto se pierde
               silenciosamente — el texto se ve "centrado" en su propia
               línea (por el text-align) pero el bloque entero queda
               desplazado ~100px a la izquierda del centro real de la
               tarjeta, exactamente el bug reportado. */
            margin: 0 auto !important;
        }

        .sidoe-card {
            background: var(--secondary-background-color);
            border: 1px solid rgba(127, 178, 255, 0.15);
            border-radius: 16px;
            padding: 1.4rem 1.3rem;
            height: 100%;
            min-height: 230px;
            transition: border-color 0.15s ease, transform 0.15s ease;
        }
        .sidoe-card:hover {
            border-color: rgba(127, 178, 255, 0.45);
            transform: translateY(-2px);
        }
        .sidoe-card-icon { font-size: 1.5rem; margin-bottom: 0.4rem; }
        .sidoe-card-title {
            color: var(--text-color);
            font-weight: 700;
            font-size: 1.05rem;
            margin-bottom: 0.6rem;
        }
        .sidoe-card-text {
            color: var(--text-color);
            opacity: 0.85;
            font-size: 0.92rem;
            line-height: 1.5;
            margin: 0;
        }
        .sidoe-card-list {
            margin: 0;
            padding-left: 1.1rem;
            color: var(--text-color);
            opacity: 0.85;
            font-size: 0.9rem;
            line-height: 1.65;
        }

        .sidoe-chip-list { display: flex; flex-direction: column; gap: 0.5rem; }
        .sidoe-chip {
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
            background: rgba(127, 178, 255, 0.08);
            border-radius: 8px;
            padding: 0.4rem 0.6rem;
        }
        .sidoe-chip-code {
            font-weight: 700;
            font-size: 0.82rem;
            color: #4a90e2;
            min-width: 52px;
        }
        .sidoe-chip-name {
            color: var(--text-color);
            opacity: 0.8;
            font-size: 0.8rem;
            line-height: 1.3;
        }

        .sidoe-stats {
            display: flex;
            justify-content: center;
            gap: 3.2rem;
            margin: 1.8rem 0 0.6rem 0;
            flex-wrap: wrap;
        }
        .sidoe-stat { text-align: center; }
        .sidoe-stat-num {
            color: var(--text-color);
            font-weight: 800;
            font-size: 2.1rem;
            line-height: 1;
        }
        .sidoe-stat-label {
            color: var(--text-color);
            opacity: 0.65;
            font-size: 0.82rem;
            margin-top: 0.3rem;
        }

        .sidoe-cta-spacer { height: 0.6rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def mostrar_landing() -> str | None:
    """Renderiza la landing institucional.

    Devuelve la opción de menú a preseleccionar ("Generar Consulta",
    "Dashboard" o "Generar Ficha") si el visitante pulsó uno de los tres
    accesos rápidos en este render; ``None`` si todavía no ha elegido nada
    (la landing debe seguir mostrándose).
    """
    _inyectar_estilos()
    logo_b64 = _logo_base64()

    st.markdown(
        f"""
        <div class="sidoe-hero">
          <div class="sidoe-hero-badge"><img src="data:image/png;base64,{logo_b64}" /></div>
          <p class="sidoe-hero-eyebrow">Oficina Nacional de Estadística</p>
          <h1 class="sidoe-hero-title">SIDOE</h1>
          <p class="sidoe-hero-subtitle">Sistema Integrado de Demanda y Oferta Estadística</p>
          <p class="sidoe-hero-tagline">
            La plataforma que centraliza y da seguimiento a los indicadores
            estadísticos que cruzan la demanda de políticas públicas con la oferta
            real de información del país.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3, gap="medium")
    with col1:
        st.markdown(
            """
            <div class="sidoe-card">
              <div class="sidoe-card-icon">🧭</div>
              <div class="sidoe-card-title">¿Qué puedes hacer aquí?</div>
              <ul class="sidoe-card-list">
                <li>Consulta del estado de indicadores oficiales</li>
                <li>Visualización de fichas técnicas en PDF</li>
                <li>Exploración del dashboard interactivo</li>
                <li>Evaluación del nivel de factibilidad</li>
              </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            """
            <div class="sidoe-card">
              <div class="sidoe-card-icon">🔗</div>
              <div class="sidoe-card-title">El sistema</div>
              <p class="sidoe-card-text">
                SIDOE es la plataforma de la Oficina Nacional de Estadística (ONE)
                para consultar, en un solo lugar, los indicadores estadísticos
                oficiales del país: qué política pública los origina, de dónde
                salen sus datos y qué tan factible es producirlos — siempre
                actualizado y validado.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col3:
        chips = "".join(
            f'<div class="sidoe-chip">'
            f'<span class="sidoe-chip-code">{sigla}</span>'
            f'<span class="sidoe-chip-name">{nombre}</span>'
            f"</div>"
            for sigla, nombre in _GENERADORES_DEMANDA
        )
        st.markdown(
            f"""
            <div class="sidoe-card">
              <div class="sidoe-card-icon">🧩</div>
              <div class="sidoe-card-title">Generadores de demanda</div>
              <div class="sidoe-chip-list">{chips}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="sidoe-stats">
          <div class="sidoe-stat">
            <div class="sidoe-stat-num">+800</div>
            <div class="sidoe-stat-label">indicadores activos</div>
          </div>
          <div class="sidoe-stat">
            <div class="sidoe-stat-num">+1000</div>
            <div class="sidoe-stat-label">fuentes oficiales empleadas</div>
          </div>
          <div class="sidoe-stat">
            <div class="sidoe-stat-num">17</div>
            <div class="sidoe-stat-label">sectores cubiertos</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sidoe-cta-spacer"></div>', unsafe_allow_html=True)

    b1, b2, b3 = st.columns(3, gap="medium")
    opcion_elegida = None
    with b1:
        if st.button(
            "🔍 Consultar indicadores", width='stretch', type="primary"
        ):
            opcion_elegida = _OPCION_POR_BOTON["consultar"]
    with b2:
        if st.button("📊 Ver dashboard", width='stretch'):
            opcion_elegida = _OPCION_POR_BOTON["dashboard"]
    with b3:
        if st.button("📄 Generar ficha", width='stretch'):
            opcion_elegida = _OPCION_POR_BOTON["ficha"]

    return opcion_elegida
