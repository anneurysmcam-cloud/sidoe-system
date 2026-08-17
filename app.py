"""
app.py
======
Punto de entrada de la aplicación SIDOE — Oficina Nacional de Estadística (ONE).

Ejecutar con:
    streamlit run app.py
"""

import logging
import time

import streamlit as st

# ── Logging institucional ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Bootstrap de base de datos (migraciones idempotentes) ───────────────────
# Llamada EXPLÍCITA (Hallazgo #4 del informe de revisión de código de agosto
# 2026): antes, `import data.database` disparaba el bootstrap completo como
# efecto secundario del import. Ahora inicializar_base_datos() se invoca acá,
# una sola vez por proceso, antes de cualquier st.* — el punto de entrada
# real de la aplicación.
from data.database import inicializar_base_datos
from config import DB_PATH

inicializar_base_datos()

# ── Seguridad: permisos del archivo de BD al arrancar ───────────────────────
from security.hardening import asegurar_permisos_db
asegurar_permisos_db(DB_PATH)

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="SIDOE - ONE 📊",
    page_icon="📊",
    layout="wide",
)

# ── Estilos institucionales ──────────────────────────────────────────────────
# Los colores usan las variables de tema que Streamlit expone en runtime
# (--text-color, --background-color, --secondary-background-color) en vez de
# hex fijos. Streamlit ya trae un selector de tema claro/oscuro nativo (menú
# ☰ → Settings → Theme); con colores hardcodeados, el título y el footer
# institucionales quedaban casi ilegibles al activar el modo oscuro (texto
# azul oscuro sobre fondo oscuro). El azul institucional (#002F6C) se
# conserva como acento en modo claro vía color-mix, sin perder legibilidad
# en oscuro.
st.markdown("""
    <style>
    .main-title {
        color: var(--text-color);
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: bold;
        text-align: center;
        margin-bottom: 5px;
    }
    .subtitle {
        color: var(--text-color);
        opacity: 0.7;
        text-align: center;
        margin-bottom: 25px;
    }
    .footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: var(--secondary-background-color);
        color: var(--text-color);
        text-align: center;
        padding: 8px;
        font-size: 12px;
        border-top: 1px solid rgba(128, 128, 128, 0.3);
    }
    </style>
""", unsafe_allow_html=True)

# ── Estado de sesión ─────────────────────────────────────────────────────────
# Se inicializa aquí (antes del header) porque _mostrando_landing() ya
# necesita leer landing_dismissed para decidir si el header se muestra.
if "usuario" not in st.session_state:
    st.session_state["usuario"] = None
if "show_help" not in st.session_state:
    st.session_state.show_help = False
if "landing_dismissed" not in st.session_state:
    st.session_state.landing_dismissed = False
if "opcion_publica_preseleccionada" not in st.session_state:
    st.session_state.opcion_publica_preseleccionada = None
if "opcion_autenticada_preseleccionada" not in st.session_state:
    # Permite que una vista (p. ej. Aprobar Indicadores → "Revisar en
    # Actualizar Indicador") navegue programáticamente a otra opción del
    # menú de sesión autenticada, en vez de solo poder decirle al usuario
    # "ve a tal pantalla" — mismo patrón que opcion_publica_preseleccionada.
    st.session_state.opcion_autenticada_preseleccionada = None


def _mostrando_landing() -> bool:
    """True si en este render se va a mostrar la landing institucional
    (views/landing.py). Esa pantalla ya trae su propio título/subtítulo
    centralizados dentro del cuadro azul — el header genérico de aquí se
    omite en ese caso puntual para no duplicarlo, tal como se ve en el
    resto de la app (Consultas, Dashboard, login, etc.)."""
    return (
        st.session_state["usuario"] is None
        and "usuario_pendiente_2fa" not in st.session_state
        and "usuario_pendiente_setup_2fa" not in st.session_state
        and not st.session_state.landing_dismissed
    )


if not _mostrando_landing():
    st.markdown('<h1 class="main-title">Oficina Nacional de Estadística (ONE)</h1>', unsafe_allow_html=True)
    st.markdown('<h3 class="subtitle">Sistema Integrado de Demanda y Oferta Estadística (SIDOE)</h3>', unsafe_allow_html=True)

# ── Seguridad y utilidades de sesión ─────────────────────────────────────────
# NOTA DE RENDIMIENTO: las vistas (views.*) NO se importan aquí arriba a
# propósito. Cada vista arrastra dependencias pesadas (pandas, plotly,
# fpdf2, openpyxl), y como Python solo importa un módulo una vez por
# proceso, hacerlo a nivel de módulo de app.py forzaba ese costo (~1-1.5s)
# ANTES de poder pintar la pantalla de login — de ahí la sensación de
# lentitud/cuelgue al arrancar. En su lugar, cada vista se importa de forma
# perezosa (lazy import) solo cuando el usuario ya autenticado la selecciona
# en el menú, vía _importar_vista() más abajo.
from security.auth import (
    BloqueadoError,
    confirmar_activacion_totp,
    generar_y_guardar_codigos_respaldo,
    iniciar_enrolamiento_totp,
    logout,
    validar_credenciales,
    verificar_codigo_respaldo,
    verificar_segundo_factor,
)
from security.hardening import (
    intentos_restantes,
    registrar_actividad,
    verificar_timeout_sesion,
    minutos_restantes_sesion,
)
from utils.ui_mensajes import marcar_mensaje, mostrar_mensaje_pendiente
import importlib

# ── Roles y opciones de menú ─────────────────────────────────────────────────
# Punto 9: el acceso de solo lectura ya no es un rol de login — es el acceso
# público por defecto SIN sesión (pensado para el enlace público en la
# página de la ONE). Cualquiera que entre sin sesión ve exactamente estas
# opciones automáticamente, sin necesidad de una cuenta.
_OPCIONES_PUBLICAS: list[str] = ["Generar Consulta", "Generar Ficha", "Dashboard"]

_OPCIONES_POR_ROL: dict[str, list[str]] = {
    # Reestructuración de roles (agosto-2026, ver imagen "Creación de Nuevos
    # Roles y Reestructuración de estos" compartida por la jefa). El rol
    # "Usuario" de esa tabla no es un rol de login nuevo: coincide 1:1 con
    # el modo público sin sesión ya existente (_OPCIONES_PUBLICAS arriba).
    "editor": [
        "Generar Consulta", "Crear Nuevo Indicador", "Actualizar Indicador",
        "Generar Ficha", "Dashboard",
    ],
    "supervisor": [
        "Generar Consulta", "Crear Nuevo Indicador", "Actualizar Indicador",
        "Eliminar Indicador", "Aprobar Indicadores", "Generar Ficha", "Dashboard",
        "Indicadores Desactivados", "Auxiliares",
    ],
    "administrador": [
        "Generar Consulta", "Generar Ficha", "Dashboard",
        "Ver Auditoría", "Administrar Usuarios",
    ],
}

# Mapa opción de menú -> (módulo, nombre de función). La importación real
# del módulo se hace en _importar_vista(), solo cuando se necesita.
_ROUTER: dict[str, tuple[str, str]] = {
    "Generar Consulta": ("views.consultas", "mostrar_consultas"),
    "Crear Nuevo Indicador": ("views.crear_indicador", "mostrar_crear_indicador"),
    "Actualizar Indicador": ("views.actualizar_indicador", "mostrar_actualizar_indicador"),
    "Eliminar Indicador": ("views.eliminar_indicador", "mostrar_eliminar_indicador"),
    "Generar Ficha": ("views.generar_ficha", "mostrar_generar_ficha"),
    "Dashboard": ("views.dashboard", "mostrar_dashboard"),
    "Indicadores Desactivados": ("views.ver_indicadores_desactivados", "mostrar_indicadores_desactivados"),
    "Ver Auditoría": ("views.ver_auditoria", "mostrar_ver_auditoria"),
    "Administrar Usuarios": ("views.admin_usuarios", "mostrar_administrar_usuarios"),
    "Auxiliares": ("views.auxiliares", "mostrar_auxiliares"),
    "Aprobar Indicadores": ("views.aprobar_indicadores", "mostrar_aprobar_indicadores"),
}


def _importar_vista(opcion: str):
    """Importa perezosamente el módulo de la vista seleccionada y devuelve
    su función de entrada. Devuelve None si la opción no está registrada."""
    destino = _ROUTER.get(opcion)
    if destino is None:
        return None
    modulo_path, nombre_funcion = destino
    modulo = importlib.import_module(modulo_path)
    return getattr(modulo, nombre_funcion)


def _ejecutar_vista(vista, opcion: str) -> None:
    """Ejecuta la vista seleccionada blindada contra errores no controlados.

    Cualquier excepción que se escape de una vista (bug de código, dato
    inesperado, etc.) se captura acá antes de que Streamlit la muestre en
    pantalla con su traceback técnico — eso expone detalles internos y
    rompe la experiencia de un usuario que no tiene por qué entenderlos.

    En vez de eso: se registra el error completo (traceback incluido) vía
    ``logger.exception`` — queda en los logs del servidor (journalctl del
    servicio systemd en producción, ver DESPLIEGUE_PRODUCCION.md) con un
    identificador de incidente corto, y al usuario se le muestra un mensaje
    genérico con ese mismo identificador para que pueda reportarlo sin
    exponer la causa técnica real.
    """
    try:
        vista()
    except Exception:
        incidente = f"ERR-{int(time.time())}"
        usuario_actual = st.session_state.get("usuario") or {}
        logger.exception(
            "Error no controlado en vista '%s' (incidente %s, usuario '%s').",
            opcion, incidente, usuario_actual.get("username", "público"),
        )
        st.error(
            "⚠️ Ocurrió un error inesperado al procesar esta sección. "
            "El equipo técnico ya tiene el detalle registrado.\n\n"
            f"Si el problema persiste, reporta este código: **{incidente}**"
        )

def _mostrar_boton_ayuda() -> None:
    """Botón + panel de documentación descargable, en la esquina superior
    derecha del área principal — como estuvo en versiones anteriores a
    d33a82b, revertido a pedido explícito de Randy tras ver el resultado
    en el sidebar (no era lo que quería, prefiere la esquina de siempre).

    Nota heredada de cuando se movió al sidebar (commit d33a82b): ahí
    competía por posición con la barra de herramientas nativa de
    Streamlit (☰ / "Deploy", fija en la esquina superior derecha) y podía
    quedar parcialmente tapada por ella. Se deja un `st.write("")` como
    espaciador antes de la fila del botón para separarlo verticalmente de
    esa barra — no hay forma de confirmarlo sin un navegador real en este
    entorno de desarrollo, así que conviene que Randy confirme visualmente
    que no vuelve a taparse tras desplegar.
    """
    st.write("")
    col_h1, col_h2 = st.columns([7, 2])
    with col_h2:
        if st.button("🧭 Ayudas y Guías", width='stretch'):
            st.session_state.show_help = not st.session_state.show_help

    if st.session_state.show_help:
        st.markdown("### 📖 Documentación del Sistema SIDOE")
        for nombre, archivo, label in [
            ("📘 Manual de Uso", "docs/manual_uso.pdf", "manual_uso.pdf"),
            ("📗 Instructivo de Consistencia", "docs/instructivo_consistencia.pdf", "instructivo_consistencia.pdf"),
            ("❓ Preguntas Frecuentes", "docs/faqs.pdf", "faqs.pdf"),
        ]:
            try:
                with open(archivo, "rb") as f:
                    st.download_button(nombre, f, archivo, mime="application/pdf")
            except FileNotFoundError:
                st.caption(f"Archivo no disponible: {label}")


def _mostrar_logo_sidebar() -> None:
    st.sidebar.image("tracking/logo_one.png", width=200)


def _mostrar_footer() -> None:
    st.markdown("""
        <div class="footer">
            Desarrollado por Randy A. Medina — Oficina Nacional de Estadística (ONE) 🇩🇴 —
            Sistema Integrado de Demanda y Oferta Estadística (SIDOE)
        </div>
    """, unsafe_allow_html=True)


def _procesar_intento_login() -> None:
    """Renderiza el formulario de login y procesa el intento si se envía.

    Si el usuario tiene 2FA (TOTP) activado, el login NO se completa aquí:
    se guarda en ``usuario_pendiente_2fa`` (no en ``usuario``, que es lo que
    el resto de la app trata como "sesión iniciada") y se muestra un
    segundo formulario (``_procesar_segundo_factor``) para el código. Solo
    tras un código válido se establece ``usuario`` y arranca la sesión real.
    """
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Ingresar")

    if not submit:
        return

    username_strip = username.strip()
    if not username_strip or not password:
        st.error("Por favor ingrese usuario y contraseña.")
        return

    try:
        usuario_login = validar_credenciales(username_strip, password)
        if usuario_login:
            if usuario_login.get("totp_habilitado"):
                st.session_state["usuario_pendiente_2fa"] = usuario_login
                st.rerun()
            elif usuario_login.get("requiere_2fa"):
                # El administrador exigió 2FA para este usuario (ver
                # views/admin_usuarios.py) pero todavía no lo configuró —
                # se fuerza el enrolamiento antes de establecer la sesión,
                # en vez de dejarlo entrar sin 2FA como el resto de los
                # logins sin totp_habilitado.
                st.session_state["usuario_pendiente_setup_2fa"] = usuario_login
                st.rerun()
            else:
                st.session_state["usuario"] = usuario_login
                registrar_actividad()  # iniciar timer de inactividad
                logger.info("Sesión iniciada para usuario '%s'.", usuario_login["username"])
                st.rerun()
        else:
            restantes = intentos_restantes(username_strip)
            if restantes > 0:
                st.error(
                    f"❌ Credenciales inválidas o usuario inactivo. "
                    f"Intentos restantes: {restantes}."
                )
            else:
                st.error("❌ Credenciales inválidas.")
    except BloqueadoError as exc:
        st.error(f"🔒 {exc}")


def _procesar_segundo_factor() -> None:
    """Segundo paso del login cuando el usuario tiene 2FA activado: pide el
    código de 6 dígitos de la app autenticadora, o alternativamente un
    código de respaldo de un solo uso si el usuario no tiene acceso a su
    dispositivo, antes de establecer la sesión real."""
    usuario_pendiente = st.session_state["usuario_pendiente_2fa"]
    st.info(f"🔐 Hola, {usuario_pendiente['username']}. Ingresa el código de tu app autenticadora.")

    usar_respaldo = st.checkbox(
        "No tengo acceso a mi app autenticadora — usar un código de respaldo"
    )

    with st.form("segundo_factor_form"):
        if usar_respaldo:
            codigo = st.text_input(
                "Código de respaldo", max_chars=9, help="Formato AAAA-AAAA"
            )
        else:
            codigo = st.text_input("Código de 6 dígitos", max_chars=6)
        col_confirmar, col_cancelar = st.columns(2)
        with col_confirmar:
            confirmar = st.form_submit_button("Confirmar")
        with col_cancelar:
            cancelar = st.form_submit_button("Cancelar")

    if cancelar:
        del st.session_state["usuario_pendiente_2fa"]
        st.rerun()

    if not confirmar:
        return

    if usar_respaldo:
        valido = verificar_codigo_respaldo(usuario_pendiente["id"], codigo)
    else:
        valido = verificar_segundo_factor(usuario_pendiente["id"], codigo)

    if valido:
        st.session_state["usuario"] = usuario_pendiente
        del st.session_state["usuario_pendiente_2fa"]
        registrar_actividad()  # iniciar timer de inactividad
        if usar_respaldo:
            from models.logs import registrar_log_standalone
            from security.auth import contar_codigos_respaldo_restantes

            restantes = contar_codigos_respaldo_restantes(usuario_pendiente["id"])
            registrar_log_standalone(
                usuario_pendiente["id"],
                "TOTP_CODIGO_RESPALDO_USADO",
                f"Usuario '{usuario_pendiente['username']}' (id={usuario_pendiente['id']}), "
                f"restantes={restantes}",
            )
            logger.warning(
                "Sesión iniciada con código de respaldo para usuario '%s' (%d restantes).",
                usuario_pendiente["username"], restantes,
            )
            st.session_state["totp_aviso_respaldo_usado"] = restantes
        else:
            logger.info("Sesión iniciada (con 2FA) para usuario '%s'.", usuario_pendiente["username"])
        st.rerun()
    else:
        st.error("❌ Código inválido. Intenta de nuevo.")


def _procesar_configuracion_2fa_obligatoria() -> None:
    """Fuerza el enrolamiento TOTP durante el login cuando el administrador
    marcó ``requiere_2fa`` para este usuario y todavía no lo tiene
    configurado (ver ``views/admin_usuarios.py`` — sección 'Exigir 2FA').

    Mismo flujo de enrolamiento que el autoservicio de 2FA en
    ``views/admin_usuarios.py`` (QR + confirmación + códigos de respaldo
    mostrados una sola vez), pero ejecutado ANTES de establecer la sesión
    real: el usuario no puede acceder a ninguna vista hasta completarlo.
    """
    usuario_pendiente = st.session_state["usuario_pendiente_setup_2fa"]
    st.warning(
        f"🔐 Hola, {usuario_pendiente['username']}. Tu administrador exige "
        "que actives la verificación en dos pasos (2FA) antes de continuar. "
        "No podrás acceder al sistema hasta completar este paso."
    )

    # ── Paso final: mostrar códigos de respaldo una sola vez y terminar login ──
    if st.session_state.get("setup2fa_codigos_respaldo"):
        st.success("✅ 2FA activado correctamente.")
        st.warning(
            "⚠️ Guarda estos códigos de respaldo en un lugar seguro — es la "
            "única vez que se muestran. Te permiten entrar si pierdes acceso "
            "a tu app autenticadora."
        )
        st.code("\n".join(st.session_state["setup2fa_codigos_respaldo"]), language=None)
        if st.button("Continuar al sistema"):
            st.session_state["usuario"] = usuario_pendiente
            del st.session_state["usuario_pendiente_setup_2fa"]
            del st.session_state["setup2fa_codigos_respaldo"]
            registrar_actividad()  # iniciar timer de inactividad
            logger.info(
                "Sesión iniciada tras configurar 2FA obligatorio para usuario '%s'.",
                usuario_pendiente["username"],
            )
            st.rerun()
        return

    # ── Paso 1: iniciar enrolamiento (generar secreto/QR) ────────────────────
    if not st.session_state.get("setup2fa_uri"):
        if st.button("🔐 Comenzar configuración de 2FA"):
            secreto, uri = iniciar_enrolamiento_totp(usuario_pendiente["id"])
            st.session_state["setup2fa_secreto"] = secreto
            st.session_state["setup2fa_uri"] = uri
            st.rerun()
        if st.button("Cancelar e ingresar más tarde"):
            del st.session_state["usuario_pendiente_setup_2fa"]
            st.rerun()
        return

    # ── Paso 2: escanear QR y confirmar el primer código ─────────────────────
    import io

    import qrcode

    uri = st.session_state["setup2fa_uri"]
    secreto = st.session_state["setup2fa_secreto"]
    img = qrcode.make(uri)
    buf_qr = io.BytesIO()
    img.save(buf_qr, format="PNG")
    st.image(buf_qr.getvalue(), caption="Escanea con tu app autenticadora", width=220)
    with st.expander("¿No puedes escanear el código?"):
        st.code(secreto, language=None)
        st.caption("Ingresa este secreto manualmente en tu app autenticadora.")

    with st.form("confirmar_2fa_obligatorio_form"):
        codigo_confirmacion = st.text_input("Código de 6 dígitos", max_chars=6)
        col_conf, col_canc = st.columns(2)
        with col_conf:
            confirmar = st.form_submit_button("✅ Confirmar y activar")
        with col_canc:
            cancelar = st.form_submit_button("Cancelar")

    if cancelar:
        # No desactiva nada en BD (el usuario podrá reintentar al volver a
        # loguearse), solo limpia el estado local del asistente.
        del st.session_state["setup2fa_uri"]
        del st.session_state["setup2fa_secreto"]
        del st.session_state["usuario_pendiente_setup_2fa"]
        st.rerun()

    if confirmar:
        from models.logs import registrar_log_standalone

        try:
            confirmar_activacion_totp(usuario_pendiente["id"], codigo_confirmacion)
            registrar_log_standalone(
                usuario_pendiente["id"], "ACTIVAR_2FA_OBLIGATORIO",
                f"Usuario '{usuario_pendiente['username']}' (id={usuario_pendiente['id']})",
            )
            del st.session_state["setup2fa_uri"]
            del st.session_state["setup2fa_secreto"]

            # Códigos de respaldo: se generan de una vez al activar, igual
            # que en el autoservicio de admin_usuarios.py.
            codigos_respaldo = generar_y_guardar_codigos_respaldo(usuario_pendiente["id"])
            st.session_state["setup2fa_codigos_respaldo"] = codigos_respaldo
            registrar_log_standalone(
                usuario_pendiente["id"], "TOTP_CODIGOS_REGENERADOS",
                f"Usuario '{usuario_pendiente['username']}' (id={usuario_pendiente['id']})",
            )
            st.rerun()
        except (ValueError, LookupError) as exc:
            st.error(f"❌ {exc}")


usuario = st.session_state["usuario"]

# ═══════════════════════════════════════════════════════════════════════════
# 2FA PENDIENTE — el usuario pasó usuario/contraseña pero falta el código TOTP
# ═══════════════════════════════════════════════════════════════════════════
# Se corta el flujo aquí (antes del modo público) para no exponer ninguna
# vista, ni siquiera de solo lectura, mientras el login está a medias.
if usuario is None and "usuario_pendiente_2fa" in st.session_state:
    _mostrar_logo_sidebar()
    st.header("🔐 Verificación en dos pasos")
    _procesar_segundo_factor()
    _mostrar_footer()
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════
# 2FA OBLIGATORIO PENDIENTE DE CONFIGURAR — el admin lo exigió (ver
# views/admin_usuarios.py) y este usuario todavía no lo tiene activado
# ═══════════════════════════════════════════════════════════════════════════
if usuario is None and "usuario_pendiente_setup_2fa" in st.session_state:
    _mostrar_logo_sidebar()
    st.header("🔐 Configuración obligatoria de 2FA")
    _procesar_configuracion_2fa_obligatoria()
    _mostrar_footer()
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════
# MODO PÚBLICO (punto 9) — sin sesión iniciada
# ═══════════════════════════════════════════════════════════════════════════
# El acceso de solo lectura no requiere login: cualquiera que entre al
# sistema sin haber iniciado sesión ve directamente Consulta / Ficha /
# Dashboard de solo lectura — pensado para el enlace público en la página
# de la ONE. Crear,
# editar, eliminar indicadores, Auditoría, Administrar Usuarios y Auxiliares
# siguen exigiendo login como Editor o Administrador.
if usuario is None:
    _mostrar_logo_sidebar()
    mostrar_mensaje_pendiente()
    st.sidebar.caption("👁️ Modo público — sin sesión iniciada.")
    with st.sidebar.expander("🔐 Iniciar sesión"):
        _procesar_intento_login()

    if st.session_state.landing_dismissed:
        if st.sidebar.button("🏠 Inicio", width='stretch'):
            st.session_state.landing_dismissed = False
            st.session_state.opcion_publica_preseleccionada = None
            st.rerun()

    # ── Landing institucional (punto de entrada del modo público) ───────────
    # Se muestra una sola vez por sesión de navegador: al elegir un acceso
    # rápido se marca landing_dismissed y se preselecciona esa opción en el
    # radio de abajo, para no perder el contexto de lo que el visitante
    # quería hacer. Puede volver a verla con "🏠 Inicio" en el sidebar.
    #
    # El botón "Ayudas y Guías" se oculta mientras la landing está visible:
    # en esa pantalla no hay espacio para acomodarlo sin generar ruido
    # visual adicional. En cuanto el visitante elige cualquiera de los
    # accesos rápidos (o ya la había descartado antes, p.ej. tras iniciar
    # sesión y volver), landing_dismissed pasa a True y el botón reaparece
    # en su lugar de siempre en el sidebar.
    if not st.session_state.landing_dismissed:
        from views.landing import mostrar_landing

        opcion_elegida = mostrar_landing()
        if opcion_elegida:
            st.session_state.landing_dismissed = True
            st.session_state.opcion_publica_preseleccionada = opcion_elegida
            st.rerun()
        _mostrar_footer()
        st.stop()

    _mostrar_boton_ayuda()

    indice_preseleccionado = 0
    if st.session_state.opcion_publica_preseleccionada in _OPCIONES_PUBLICAS:
        indice_preseleccionado = _OPCIONES_PUBLICAS.index(
            st.session_state.opcion_publica_preseleccionada
        )

    opcion = st.sidebar.radio(
        "Selecciona una opción:", _OPCIONES_PUBLICAS, index=indice_preseleccionado
    )
    vista = _importar_vista(opcion)
    if vista:
        _ejecutar_vista(vista, opcion)
    else:
        st.error(f"Opción no reconocida: {opcion}")

    _mostrar_footer()
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════
# SESIÓN AUTENTICADA
# ═══════════════════════════════════════════════════════════════════════════

# ── Timeout de sesión por inactividad ────────────────────────────────────────
if verificar_timeout_sesion():
    usuario_expirado = st.session_state.get("usuario", {}).get("username", "desconocido")
    logger.info("Sesión expirada por inactividad para usuario '%s'.", usuario_expirado)
    logout(st.session_state)
    marcar_mensaje(
        "warning",
        "⏱️ Tu sesión ha expirado por inactividad. Por favor vuelve a iniciar sesión.",
    )
    st.rerun()

# ── Registrar actividad en cada renderizado ───────────────────────────────────
registrar_actividad()

_mostrar_boton_ayuda()

# ── Menú lateral ─────────────────────────────────────────────────────────────
_mostrar_logo_sidebar()
st.sidebar.write(f"👤 **{usuario['username']}** ({usuario['rol']})")

# Aviso único (se limpia solo) si esta sesión se inició con un código de
# respaldo: recuerda al usuario cuántos le quedan antes de que lo olvide.
if "totp_aviso_respaldo_usado" in st.session_state:
    restantes = st.session_state.pop("totp_aviso_respaldo_usado")
    if restantes == 0:
        st.sidebar.error(
            "🔑 Usaste tu último código de respaldo. Genera un lote nuevo "
            "desde Administrar Usuarios → 2FA lo antes posible."
        )
    else:
        st.sidebar.warning(
            f"🔑 Entraste con un código de respaldo. Te quedan **{restantes}**. "
            "Considera reactivar tu 2FA con tu dispositivo o generar códigos nuevos."
        )

# Mostrar tiempo restante de sesión en el sidebar
mins = minutos_restantes_sesion()
if mins <= 10:
    st.sidebar.warning(f"⏳ Sesión expira en {mins} min")
else:
    st.sidebar.caption(f"⏳ Sesión activa ({mins} min restantes)")

if st.sidebar.button("Cerrar Sesión"):
    logger.info("Sesión cerrada manualmente por usuario '%s'.", usuario["username"])
    logout(st.session_state)
    st.rerun()

# ── Opciones según rol ────────────────────────────────────────────────────────
opciones = _OPCIONES_POR_ROL.get(usuario.get("rol"))
if opciones is None:
    st.error(
        f"🚫 El rol '{usuario.get('rol')}' no está reconocido por el sistema. "
        "Roles válidos: editor, supervisor, administrador. "
        "Contacte a un administrador."
    )
    st.stop()

# El radio necesita un `key` explícito para poder forzar una navegación
# programática (p. ej. "Aprobar Indicadores" -> "Editar antes de aprobar",
# ver views/aprobar_indicadores.py::_ir_a_actualizar_indicador). Sin `key`,
# Streamlit igual le asigna uno interno automático basado en label+options,
# y como ese radio ya se había renderizado antes en la sesión (el
# supervisor ya estaba parado en "Aprobar Indicadores"), reutiliza el valor
# ya guardado en session_state y el argumento `index=` de abajo se ignora
# por completo — la navegación "funcionaba" en el sentido de que
# opcion_autenticada_preseleccionada quedaba bien calculado, pero el radio
# nunca se movía de la opción en la que ya estaba, así que el supervisor
# se quedaba viendo "Aprobar Indicadores" en vez de caer en "Actualizar
# Indicador". Con un `key` propio, se puede sobrescribir ese valor a mano
# en session_state antes de instanciar el widget para forzar el salto, y
# se consume una sola vez (igual que _indicador_a_editar_id) para no
# pisar una navegación manual posterior del usuario.
_KEY_MENU_AUTENTICADO = "menu_autenticado_radio"
preseleccion = st.session_state.opcion_autenticada_preseleccionada
if preseleccion in opciones:
    st.session_state[_KEY_MENU_AUTENTICADO] = preseleccion
    st.session_state.opcion_autenticada_preseleccionada = None
elif _KEY_MENU_AUTENTICADO not in st.session_state:
    st.session_state[_KEY_MENU_AUTENTICADO] = opciones[0]
elif st.session_state[_KEY_MENU_AUTENTICADO] not in opciones:
    # El rol cambió entre renders (p. ej. otro usuario inició sesión) y la
    # opción memorizada ya no existe en el menú de este rol.
    st.session_state[_KEY_MENU_AUTENTICADO] = opciones[0]
opcion = st.sidebar.radio(
    "Selecciona una opción:", opciones, key=_KEY_MENU_AUTENTICADO
)

# ── Enrutador ─────────────────────────────────────────────────────────────────
vista = _importar_vista(opcion)
if vista:
    _ejecutar_vista(vista, opcion)
else:
    st.error(f"Opción no reconocida: {opcion}")

# ── Footer institucional ──────────────────────────────────────────────────────
_mostrar_footer()
