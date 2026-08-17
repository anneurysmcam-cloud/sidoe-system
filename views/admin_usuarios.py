"""views/admin_usuarios.py — Administración de usuarios (solo administradores)."""

import io

import pandas as pd
import streamlit as st

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
from models.logs import registrar_log_standalone
from security.auth import (
    cambiar_password,
    resetear_password_admin,
    verificar_password_propia,
    confirmar_activacion_totp,
    contar_codigos_respaldo_restantes,
    desactivar_totp,
    generar_y_guardar_codigos_respaldo,
    iniciar_enrolamiento_totp,
    registrar_usuario,
    require_role,
)
from security.hardening import sanitizar_username, validar_politica_password
from utils.ui_mensajes import (
    aplicar_limpieza_pendiente,
    marcar_limpieza,
    marcar_mensaje,
    mostrar_mensaje_pendiente,
)


def _usuario_id() -> int | None:
    """Devuelve el id del usuario autenticado en la sesión actual de Streamlit, o None si no hay sesión."""
    return (st.session_state.get("usuario") or {}).get("id")


def _rol_actual() -> str | None:
    """Devuelve el rol del usuario autenticado en la sesión actual de
    Streamlit, o None si no hay sesión. Se pasa explícitamente a las
    funciones de escritura de ``models/crud_usuarios.py`` como defensa en
    profundidad (Hallazgo D del informe de arquitectura, agosto 2026)."""
    return (st.session_state.get("usuario") or {}).get("rol")


def _mostrar_codigos_respaldo_nuevos() -> None:
    """Muestra un lote de códigos de respaldo recién generados, una única
    vez, con opción de descarga. Cualquier lote anterior ya quedó invalidado
    en BD por ``generar_y_guardar_codigos_respaldo`` — estos son los únicos
    válidos a partir de ahora, así que el usuario debe guardarlos antes de
    seguir."""
    codigos = st.session_state["totp_codigos_respaldo_nuevos"]
    st.warning(
        "🔑 **Guarda estos códigos de respaldo ahora.** No se volverán a "
        "mostrar. Cada uno funciona una sola vez como sustituto del código "
        "de tu app autenticadora, si llegas a perder acceso a ella. "
        "Guárdalos en un lugar seguro, separado de tu teléfono (por ejemplo, "
        "impresos o en un gestor de contraseñas)."
    )
    st.code("\n".join(codigos), language=None)
    st.download_button(
        "⬇️ Descargar códigos (.txt)",
        data="\n".join(codigos),
        file_name="sidoe_codigos_respaldo.txt",
        mime="text/plain",
    )
    if st.button("✅ Ya los guardé"):
        del st.session_state["totp_codigos_respaldo_nuevos"]
        st.rerun()


@st.dialog("¿Confirmar eliminación de usuario?")
def _confirmar_eliminacion_usuario(usuario_id: int, username: str) -> None:
    """Diálogo modal de confirmación antes de borrar un usuario
    permanentemente; ejecuta la eliminación solo si el usuario confirma."""
    st.write(f"Está a punto de eliminar permanentemente al usuario: **{username}**")
    st.warning("⚠️ Esta acción no se puede deshacer.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            ok, mensaje = eliminar_usuario(usuario_id, _rol_actual())
            if ok:
                registrar_log_standalone(
                    _usuario_id(), "ELIMINAR_USUARIO",
                    f"Usuario '{username}' (id={usuario_id})",
                )
                marcar_mensaje(
                    "success", f"Usuario '{username}' eliminado permanentemente.",
                    seccion="actualizar_usuario",
                )
            else:
                marcar_mensaje("error", f"Error al eliminar: {mensaje}", seccion="actualizar_usuario")
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


@require_role(["administrador"])
def mostrar_administrar_usuarios() -> None:
    """Vista de gestión de usuarios: crear, cambiar rol, activar/desactivar,
    eliminar y restablecer contraseña. Registra cada acción en auditoría.
    Accesible solo para administradores."""
    st.header("👥 Administración de Usuarios")
    aplicar_limpieza_pendiente()

    df = pd.DataFrame(listar_usuarios())

    st.dataframe(df, width="stretch")

    # ── Crear nuevo usuario ──────────────────────────────────────────────────
    st.subheader("➕ Crear Usuario")
    mostrar_mensaje_pendiente(seccion="crear_usuario")
    with st.expander("📋 Política de contraseñas", expanded=False):
        st.markdown(
            "- Mínimo **8 caracteres**\n"
            "- Al menos **1 letra mayúscula**\n"
            "- Al menos **1 número**\n"
            "- Al menos **1 carácter especial** (!@#$%^&*...)"
        )
    with st.form("crear_usuario"):
        nuevo_user = st.text_input(
            "Nombre de usuario", max_chars=64, key="cu_nuevo_user",
            help="Solo letras, números, puntos, guiones y guiones bajos.",
        )
        nueva_pass = st.text_input(
            "Contraseña", type="password", key="cu_nueva_pass",
            help="Mínimo 8 caracteres, mayúscula, número y carácter especial.",
        )
        confirmar_pass = st.text_input(
            "Confirmar contraseña", type="password", key="cu_confirmar_pass"
        )
        nuevo_rol = st.selectbox(
            "Rol", ["editor", "supervisor", "administrador"], key="cu_nuevo_rol"
        )
        if st.form_submit_button("Crear"):
            nuevo_user_limpio = sanitizar_username(nuevo_user.strip())
            if not nuevo_user_limpio:
                st.error("El nombre de usuario es inválido o está vacío.")
            elif not nueva_pass:
                st.error("La contraseña es obligatoria.")
            elif nueva_pass != confirmar_pass:
                st.error("Las contraseñas no coinciden.")
            else:
                errores_pass = validar_politica_password(nueva_pass)
                if errores_pass:
                    for e in errores_pass:
                        st.error(f"🔑 {e}")
                else:
                    try:
                        registrar_usuario(nuevo_user_limpio, nueva_pass, rol=nuevo_rol)
                        registrar_log_standalone(
                            _usuario_id(), "CREAR_USUARIO",
                            f"Usuario={nuevo_user_limpio}, Rol={nuevo_rol}",
                        )
                        # Limpiar el formulario solo tras un éxito real; si
                        # hubo un error de validación, el usuario conserva lo
                        # que escribió para no tener que retipearlo. No se
                        # puede asignar session_state[key] aquí mismo porque
                        # el widget ya fue instanciado en este ciclo — se
                        # difiere al inicio del próximo rerun.
                        marcar_limpieza({
                            "cu_nuevo_user": "",
                            "cu_nueva_pass": "",
                            "cu_confirmar_pass": "",
                            "cu_nuevo_rol": "editor",
                        })
                        marcar_mensaje(
                            "success",
                            f"✅ Usuario '{nuevo_user_limpio}' creado con rol '{nuevo_rol}'.",
                            seccion="crear_usuario",
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"No se pudo crear el usuario: {exc}")

    # ── Actualizar / Eliminar usuario ────────────────────────────────────────
    st.subheader("🔄 Actualizar / Eliminar Usuario")
    mostrar_mensaje_pendiente(seccion="actualizar_usuario")
    if df.empty:
        st.info("No hay usuarios registrados.")
        return

    usuario_id_sel = st.selectbox(
        "Selecciona usuario por ID",
        df["id"].tolist(),
        format_func=lambda uid: f"{uid} — {df.loc[df['id'] == uid, 'username'].values[0]}",
        key="usuario_sel",
    )
    # Se resuelve una sola vez aquí y se reutiliza en todos los
    # registrar_log_standalone de esta sección: antes el detalle de
    # auditoría solo guardaba "id={usuario_id_sel}" y la vista de Auditoría
    # no muestra el nombre del usuario AFECTADO (solo el de quien ejecuta la
    # acción, vía el join con auditoria.usuario_id) — al activar/desactivar
    # un usuario solo se veía su ID en el detalle, no su nombre (bug
    # reportado por Randy).
    username_sel = df.loc[df["id"] == usuario_id_sel, "username"].values[0]

    col_rol, col_estado, col_2fa, col_elim = st.columns(4)

    with col_rol:
        nuevo_rol_upd = st.selectbox(
            "Nuevo rol", ["editor", "supervisor", "administrador"], key="rol_update"
        )
        if st.button("💾 Cambiar rol"):
            ok, mensaje = cambiar_rol_usuario(usuario_id_sel, nuevo_rol_upd, _rol_actual())
            if ok:
                registrar_log_standalone(
                    _usuario_id(), "CAMBIAR_ROL",
                    f"Usuario '{username_sel}' (id={usuario_id_sel}), nuevo_rol={nuevo_rol_upd}",
                )
                marcar_mensaje("success", "Rol actualizado correctamente.", seccion="actualizar_usuario")
                st.rerun()
            else:
                st.error(f"Error al cambiar rol: {mensaje}")

    with col_estado:
        activo_actual = df.loc[df["id"] == usuario_id_sel, "activo"].values[0]
        if activo_actual:
            if st.button("🚫 Desactivar usuario"):
                ok, mensaje = desactivar_usuario(usuario_id_sel, _rol_actual())
                if ok:
                    registrar_log_standalone(
                        _usuario_id(), "DESACTIVAR_USUARIO",
                        f"Usuario '{username_sel}' (id={usuario_id_sel})",
                    )
                    marcar_mensaje("warning", "Usuario desactivado.", seccion="actualizar_usuario")
                    st.rerun()
                else:
                    st.error(f"Error: {mensaje}")
        else:
            if st.button("✅ Activar usuario"):
                ok, mensaje = activar_usuario(usuario_id_sel, _rol_actual())
                if ok:
                    registrar_log_standalone(
                        _usuario_id(), "ACTIVAR_USUARIO",
                        f"Usuario '{username_sel}' (id={usuario_id_sel})",
                    )
                    marcar_mensaje("success", "Usuario activado.", seccion="actualizar_usuario")
                    st.rerun()
                else:
                    st.error(f"Error: {mensaje}")

    with col_2fa:
        requiere_2fa_actual = bool(df.loc[df["id"] == usuario_id_sel, "requiere_2fa"].values[0])
        totp_habilitado_actual = bool(df.loc[df["id"] == usuario_id_sel, "totp_habilitado"].values[0])
        if requiere_2fa_actual:
            if totp_habilitado_actual:
                st.caption("🔐 2FA exigido — ya configurado.")
            else:
                st.caption("🔐 2FA exigido — pendiente en próximo login.")
            if st.button("🔓 Quitar exigencia de 2FA"):
                ok, mensaje = quitar_exigencia_2fa(usuario_id_sel, _rol_actual())
                if ok:
                    registrar_log_standalone(
                        _usuario_id(), "QUITAR_EXIGENCIA_2FA",
                        f"Usuario '{username_sel}' (id={usuario_id_sel})",
                    )
                    # Si el usuario ya había completado el enrolamiento
                    # forzado (totp_habilitado=1), quitar SOLO requiere_2fa
                    # no le libera nada en la práctica: seguiría pidiéndole
                    # el código en cada login porque su 2FA sigue activo.
                    # "Quitar exigencia" debe deshacer también eso — es lo
                    # único que se le ofreció activar, no una elección propia.
                    if totp_habilitado_actual:
                        desactivar_totp(usuario_id_sel)
                        registrar_log_standalone(
                            _usuario_id(), "DESACTIVAR_2FA_ADMIN",
                            f"Usuario '{username_sel}' (id={usuario_id_sel})",
                        )
                        marcar_mensaje(
                            "success",
                            "Exigencia de 2FA retirada y 2FA desactivado para el usuario "
                            "(sus códigos de respaldo quedaron invalidados).",
                            seccion="actualizar_usuario",
                        )
                    else:
                        marcar_mensaje("success", "Exigencia de 2FA retirada.", seccion="actualizar_usuario")
                    st.rerun()
                else:
                    st.error(f"Error: {mensaje}")
        else:
            st.caption("2FA no exigido.")
            if st.button("🔒 Exigir 2FA"):
                ok, mensaje = exigir_2fa(usuario_id_sel, _rol_actual())
                if ok:
                    registrar_log_standalone(
                        _usuario_id(), "EXIGIR_2FA",
                        f"Usuario '{username_sel}' (id={usuario_id_sel})",
                    )
                    marcar_mensaje(
                        "success",
                        "2FA exigido. El usuario deberá configurarlo en su próximo login.",
                        seccion="actualizar_usuario",
                    )
                    st.rerun()
                else:
                    st.error(f"Error: {mensaje}")

    with col_elim:
        st.markdown("**⚠️ Zona peligrosa**")
        if st.button("🗑️ Eliminar usuario", type="primary"):
            # Impedir auto-eliminación
            if usuario_id_sel == _usuario_id():
                st.error("No puedes eliminar tu propia cuenta.")
            else:
                username_sel = df.loc[df["id"] == usuario_id_sel, "username"].values[0]
                _confirmar_eliminacion_usuario(usuario_id_sel, username_sel)

    # ── Cambio de contraseña (administrador puede cambiar la de cualquier usuario) ──
    st.divider()
    st.subheader("🔑 Cambiar Contraseña de Usuario")
    mostrar_mensaje_pendiente(seccion="cambiar_password")
    with st.expander("📋 Política de contraseñas", expanded=False):
        st.markdown(
            "- Mínimo **8 caracteres**\n"
            "- Al menos **1 letra mayúscula**\n"
            "- Al menos **1 número**\n"
            "- Al menos **1 carácter especial** (!@#$%^&*...)"
        )

    uid_pass = st.selectbox(
        "Usuario al que cambiar contraseña",
        df["id"].tolist(),
        format_func=lambda uid: f"{uid} — {df.loc[df['id'] == uid, 'username'].values[0]}",
        key="uid_pass_admin",
    )
    # Mismo motivo que username_sel más arriba: el detalle de auditoría de
    # cambio/reseteo de contraseña solo guardaba el ID del usuario afectado.
    username_pass = df.loc[df["id"] == uid_pass, "username"].values[0]
    es_autoservicio = uid_pass == _usuario_id()

    if es_autoservicio:
        # Cambiar mi propia contraseña: sigue exigiendo la contraseña actual.
        with st.form("cambiar_password_admin"):
            pass_actual = st.text_input(
                "Tu contraseña actual", type="password", key="cp_pass_actual"
            )
            pass_nuevo = st.text_input(
                "Nueva contraseña", type="password", key="cp_pass_nuevo"
            )
            pass_confirm = st.text_input(
                "Confirmar nueva contraseña", type="password", key="cp_pass_confirm"
            )
            if st.form_submit_button("🔄 Cambiar mi contraseña"):
                if not pass_actual or not pass_nuevo:
                    st.error("Todos los campos son obligatorios.")
                elif pass_nuevo != pass_confirm:
                    st.error("Las contraseñas nuevas no coinciden.")
                else:
                    errores_pass = validar_politica_password(pass_nuevo)
                    if errores_pass:
                        for e in errores_pass:
                            st.error(f"🔑 {e}")
                    else:
                        try:
                            cambiar_password(uid_pass, pass_actual, pass_nuevo)
                            registrar_log_standalone(
                                _usuario_id(), "CAMBIAR_PASSWORD",
                                f"Usuario '{username_pass}' (id={uid_pass})",
                            )
                            marcar_limpieza({
                                "cp_pass_actual": "",
                                "cp_pass_nuevo": "",
                                "cp_pass_confirm": "",
                            })
                            marcar_mensaje(
                                "success", "✅ Contraseña actualizada correctamente.",
                                seccion="cambiar_password",
                            )
                            st.rerun()
                        except (ValueError, LookupError) as exc:
                            st.error(f"❌ {exc}")
                        except Exception as exc:
                            st.error(f"Error inesperado: {exc}")
    else:
        # Reseteo administrativo de la contraseña de otro usuario: no se pide
        # (ni se debe pedir) la contraseña actual del usuario objetivo — el
        # administrador nunca debería conocerla. Como control equivalente, se
        # exige re-autenticación con la propia contraseña del administrador y
        # queda un registro de auditoría explícito (RESET_PASSWORD_ADMIN) con
        # quién ejecutó el reseteo y sobre quién.
        st.caption(
            "🔒 Vas a **resetear** la contraseña de otro usuario. No se requiere "
            "(ni se pedirá) su contraseña anterior. Por seguridad, confirma con "
            "tu propia contraseña de administrador; la acción queda auditada."
        )
        with st.form("resetear_password_admin_otro"):
            pass_admin_confirm = st.text_input(
                "Tu contraseña de administrador (confirmación)", type="password",
                key="rp_pass_admin_confirm",
            )
            pass_nuevo = st.text_input(
                "Nueva contraseña para el usuario", type="password", key="rp_pass_nuevo"
            )
            pass_confirm = st.text_input(
                "Confirmar nueva contraseña", type="password", key="rp_pass_confirm"
            )
            if st.form_submit_button("🔄 Resetear contraseña"):
                if not pass_admin_confirm or not pass_nuevo:
                    st.error("Todos los campos son obligatorios.")
                elif pass_nuevo != pass_confirm:
                    st.error("Las contraseñas nuevas no coinciden.")
                else:
                    errores_pass = validar_politica_password(pass_nuevo)
                    if errores_pass:
                        for e in errores_pass:
                            st.error(f"🔑 {e}")
                    else:
                        if not verificar_password_propia(_usuario_id(), pass_admin_confirm):
                            st.error("❌ Tu contraseña de administrador es incorrecta.")
                        else:
                            try:
                                resetear_password_admin(uid_pass, pass_nuevo)
                                registrar_log_standalone(
                                    _usuario_id(), "RESET_PASSWORD_ADMIN",
                                    f"Usuario '{username_pass}' (id={uid_pass})",
                                )
                                marcar_limpieza({
                                    "rp_pass_admin_confirm": "",
                                    "rp_pass_nuevo": "",
                                    "rp_pass_confirm": "",
                                })
                                marcar_mensaje(
                                    "success", "✅ Contraseña reseteada correctamente.",
                                    seccion="cambiar_password",
                                )
                                st.rerun()
                            except (ValueError, LookupError) as exc:
                                st.error(f"❌ {exc}")
                            except Exception as exc:
                                st.error(f"Error inesperado: {exc}")


    # ── Backup de base de datos ──────────────────────────────────────────────
    st.divider()
    st.subheader("💾 Backup de Base de Datos")
    from utils.backup import crear_backup_rotado, listar_backups

    col_bk1, col_bk2 = st.columns([1, 2])
    with col_bk1:
        if st.button("📦 Crear Backup Ahora"):
            try:
                ruta = crear_backup_rotado()
                nombre = ruta.split("/")[-1]
                registrar_log_standalone(
                    _usuario_id(), "BACKUP_DB", f"archivo={nombre}"
                )
                st.success(f"✅ Backup creado: `{nombre}`")
            except Exception as exc:
                st.error(f"Error al crear backup: {exc}")

    with col_bk2:
        backups = listar_backups()
        if backups:
            df_bk = pd.DataFrame(backups)[["nombre", "tamaño_mb", "fecha"]]
            df_bk.columns = ["Archivo", "Tamaño (MB)", "Fecha"]
            st.dataframe(df_bk, width="stretch", hide_index=True)
        else:
            st.info("No hay backups disponibles.")

    # ── Verificación en dos pasos (2FA) — autoservicio sobre la cuenta propia ──
    # No se ofrece "activar 2FA para otro usuario" desde aquí: el QR solo
    # tiene sentido para quien va a escanearlo con su propio teléfono. Un
    # administrador solo puede activar/desactivar 2FA de SU PROPIA cuenta.
    st.divider()
    st.subheader("🔐 Verificación en dos pasos (2FA) — mi cuenta")
    mostrar_mensaje_pendiente(seccion="totp")

    mi_id = _usuario_id()
    mi_username = (st.session_state.get("usuario") or {}).get("username", "")
    totp_activo = obtener_totp_habilitado(mi_id)

    if totp_activo:
        st.success("✅ La verificación en dos pasos está **activada** para tu cuenta.")

        # ── Códigos de respaldo (recovery codes) ────────────────────────────
        if st.session_state.get("totp_codigos_respaldo_nuevos"):
            _mostrar_codigos_respaldo_nuevos()
        else:
            restantes = contar_codigos_respaldo_restantes(mi_id)
            if restantes == 0:
                st.warning(
                    "⚠️ No tienes códigos de respaldo disponibles. Si pierdes "
                    "acceso a tu app autenticadora, no podrás entrar a tu "
                    "cuenta sin ayuda de otro administrador. Genera un lote."
                )
            else:
                st.caption(f"🔑 Te quedan **{restantes}** código(s) de respaldo sin usar.")
            if st.button("🔄 Generar nuevos códigos de respaldo"):
                codigos = generar_y_guardar_codigos_respaldo(mi_id)
                st.session_state["totp_codigos_respaldo_nuevos"] = codigos
                registrar_log_standalone(mi_id, "TOTP_CODIGOS_REGENERADOS", f"Usuario '{mi_username}' (id={mi_id})")
                st.rerun()

        st.divider()
        if st.button("🚫 Desactivar 2FA"):
            try:
                desactivar_totp(mi_id)
                registrar_log_standalone(mi_id, "DESACTIVAR_2FA", f"Usuario '{mi_username}' (id={mi_id})")
                marcar_mensaje("success", "2FA desactivado.", seccion="totp")
                st.rerun()
            except LookupError as exc:
                st.error(f"❌ {exc}")
    else:
        st.caption(
            "Agrega una capa extra de seguridad a tu cuenta con una app "
            "autenticadora (Google Authenticator, Authy, etc.)."
        )
        if not st.session_state.get("totp_enrolamiento_uri"):
            if st.button("🔐 Activar 2FA"):
                secreto, uri = iniciar_enrolamiento_totp(mi_id)
                st.session_state["totp_enrolamiento_secreto"] = secreto
                st.session_state["totp_enrolamiento_uri"] = uri
                st.rerun()
        else:
            import qrcode

            uri = st.session_state["totp_enrolamiento_uri"]
            secreto = st.session_state["totp_enrolamiento_secreto"]

            img = qrcode.make(uri)
            buf_qr = io.BytesIO()
            img.save(buf_qr, format="PNG")
            st.image(buf_qr.getvalue(), caption="Escanea con tu app autenticadora", width=220)
            with st.expander("¿No puedes escanear el código?"):
                st.code(secreto, language=None)
                st.caption("Ingresa este secreto manualmente en tu app autenticadora.")

            with st.form("confirmar_2fa_form"):
                codigo_confirmacion = st.text_input("Código de 6 dígitos", max_chars=6)
                col_conf, col_canc = st.columns(2)
                with col_conf:
                    confirmar_2fa = st.form_submit_button("✅ Confirmar y activar")
                with col_canc:
                    cancelar_2fa = st.form_submit_button("Cancelar")

            if cancelar_2fa:
                # No desactiva nada en BD (el usuario podría reintentar), solo
                # limpia el estado local del asistente de enrolamiento.
                del st.session_state["totp_enrolamiento_uri"]
                del st.session_state["totp_enrolamiento_secreto"]
                st.rerun()

            if confirmar_2fa:
                try:
                    confirmar_activacion_totp(mi_id, codigo_confirmacion)
                    registrar_log_standalone(mi_id, "ACTIVAR_2FA", f"Usuario '{mi_username}' (id={mi_id})")
                    del st.session_state["totp_enrolamiento_uri"]
                    del st.session_state["totp_enrolamiento_secreto"]

                    # Códigos de respaldo: se generan de una vez al activar,
                    # para que el usuario nunca quede con 2FA activo y cero
                    # códigos de respaldo disponibles.
                    codigos_respaldo = generar_y_guardar_codigos_respaldo(mi_id)
                    st.session_state["totp_codigos_respaldo_nuevos"] = codigos_respaldo
                    registrar_log_standalone(mi_id, "TOTP_CODIGOS_REGENERADOS", f"Usuario '{mi_username}' (id={mi_id})")

                    marcar_mensaje("success", "✅ 2FA activado correctamente.", seccion="totp")
                    st.rerun()
                except (ValueError, LookupError) as exc:
                    st.error(f"❌ {exc}")
