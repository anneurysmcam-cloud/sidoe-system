"""views/auxiliares.py — Gestión de catálogos controlados de Auxiliares (rol supervisor)."""

import pandas as pd
import streamlit as st

from models.crud_auxiliares import (
    actualizar_aplica_a_categoria,
    cambiar_estado_valor,
    crear_categoria,
    crear_valor,
    editar_valor,
    eliminar_categoria,
    eliminar_valor,
    listar_categorias,
    listar_categorias_personalizadas,
    obtener_historial,
    obtener_valores,
)
from security.auth import require_role
from utils.ui_mensajes import (
    aplicar_limpieza_pendiente,
    marcar_limpieza,
    marcar_mensaje,
    mostrar_mensaje_pendiente,
)


def _usuario_id() -> int | None:
    """Devuelve el id del usuario autenticado en la sesión actual de Streamlit, o None si no hay sesión."""
    return (st.session_state.get("usuario") or {}).get("id")


@st.dialog("¿Confirmar eliminación de categoría?")
def _confirmar_eliminacion_categoria(categoria_id: int, nombre_visible: str) -> None:
    """Diálogo modal de confirmación antes de borrar una categoría
    personalizada; ejecuta la eliminación solo si el usuario confirma."""
    st.write(f"Está a punto de eliminar permanentemente la categoría: **{nombre_visible}**")
    st.warning(
        "⚠️ Esta acción no se puede deshacer. Solo es posible si ninguno de "
        "sus valores está en uso."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            ok, msg = eliminar_categoria(categoria_id, _usuario_id())
            marcar_mensaje("success" if ok else "error", msg, seccion="nueva_categoria")
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


@st.dialog("¿Confirmar eliminación de valor?")
def _confirmar_eliminacion_valor(valor_id: int, nombre_visible: str) -> None:
    """Diálogo modal de confirmación antes de borrar permanentemente un
    valor de una categoría de Auxiliares; ejecuta la eliminación solo si
    el usuario confirma. Mismo patrón que _confirmar_eliminacion_categoria
    y _confirmar_eliminacion_usuario (admin_usuarios.py)."""
    st.write(f"Está a punto de eliminar permanentemente el valor: **{nombre_visible}**")
    st.warning(
        "⚠️ Esta acción no se puede deshacer. Solo es posible si el valor "
        "no está en uso por ningún registro."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            ok, msg = eliminar_valor(valor_id, _usuario_id())
            marcar_mensaje("success" if ok else "error", msg, seccion="valores_catalogo")
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


@require_role(["supervisor"])
def mostrar_auxiliares() -> None:
    """Vista de administración de catálogos controlados (Auxiliares):
    crear, editar, activar/desactivar y eliminar categorías y valores.
    Accesible únicamente para el rol supervisor (reestructuración de
    roles, ver imagen de la jefa: "Crea, y elimina valores y categorías
    en Auxiliares")."""
    st.header("🧩 Auxiliares — Catálogos Controlados")
    aplicar_limpieza_pendiente()
    st.caption(
        "Aquí se administran las listas de valores normalizados que usan los formularios "
        "(Generador de Demanda, fuentes, periodicidades, etc.). Solo el rol supervisor "
        "puede modificar estos catálogos."
    )

    categorias = listar_categorias(solo_activas=True)

    tab_valores, tab_categoria, tab_historial = st.tabs(
        ["📋 Valores del catálogo", "➕ Nueva categoría", "🕓 Historial de cambios"]
    )

    # ── Tab 1: gestionar valores de una categoría existente ──────────────────
    with tab_valores:
        mostrar_mensaje_pendiente(seccion="valores_catalogo")
        if not categorias:
            st.info("Aún no hay categorías. Cree una en la pestaña 'Nueva categoría'.")
        else:
            opciones_cat = {c["nombre_visible"]: c["clave"] for c in categorias}
            nombre_sel = st.selectbox("Categoría", list(opciones_cat.keys()))
            clave_sel = opciones_cat[nombre_sel]

            incluir_inactivos = st.checkbox("Mostrar valores desactivados", value=False)
            valores = obtener_valores(clave_sel, solo_activos=not incluir_inactivos)

            if valores:
                df = pd.DataFrame(valores)
                df["activo"] = df["activo"].map({1: "✅ Activo", 0: "🚫 Desactivado"})
                st.dataframe(
                    df.rename(columns={"id": "ID", "valor": "Valor", "activo": "Estado"}),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("Esta categoría todavía no tiene valores.")

            st.markdown("---")
            col_add, col_edit = st.columns(2)

            with col_add:
                st.subheader("➕ Agregar valor")
                with st.form(f"form_add_{clave_sel}"):
                    nuevo_valor = st.text_input("Nuevo valor", key=f"av_nuevo_valor_{clave_sel}")
                    if st.form_submit_button("Agregar"):
                        if not nuevo_valor.strip():
                            st.error("El valor no puede estar vacío.")
                        else:
                            ok, msg, _ = crear_valor(clave_sel, nuevo_valor, _usuario_id())
                            if ok:
                                marcar_limpieza({f"av_nuevo_valor_{clave_sel}": ""})
                                marcar_mensaje("success", msg, seccion="valores_catalogo")
                                st.rerun()
                            else:
                                st.error(msg)

            with col_edit:
                st.subheader("✏️ Editar / Desactivar / Eliminar")
                if valores:
                    opciones_val = {f"{v['id']} — {v['valor']}": v for v in valores}
                    sel_label = st.selectbox("Valor a modificar", list(opciones_val.keys()))
                    v_sel = opciones_val[sel_label]

                    nuevo_nombre = st.text_input(
                        "Renombrar a", value=v_sel["valor"], key="rename_input"
                    )
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if st.button("💾 Renombrar"):
                            ok, msg = editar_valor(v_sel["id"], nuevo_nombre, _usuario_id())
                            if ok:
                                marcar_mensaje("success", msg, seccion="valores_catalogo")
                                st.rerun()
                            else:
                                st.error(msg)
                    with c2:
                        if v_sel["activo"] == 1:
                            if st.button("🚫 Desactivar"):
                                ok, msg = cambiar_estado_valor(v_sel["id"], False, _usuario_id())
                                if ok:
                                    marcar_mensaje("warning", msg, seccion="valores_catalogo")
                                    st.rerun()
                                else:
                                    st.error(msg)
                        else:
                            if st.button("✅ Reactivar"):
                                ok, msg = cambiar_estado_valor(v_sel["id"], True, _usuario_id())
                                if ok:
                                    marcar_mensaje("success", msg, seccion="valores_catalogo")
                                    st.rerun()
                                else:
                                    st.error(msg)
                    with c3:
                        if st.button("🗑️ Eliminar"):
                            _confirmar_eliminacion_valor(v_sel["id"], v_sel["valor"])
                else:
                    st.caption("No hay valores para editar todavía.")

    # ── Tab 2: crear una nueva categoría ────────────────────────────────────
    with tab_categoria:
        mostrar_mensaje_pendiente(seccion="nueva_categoria")
        st.caption(
            "Use esto solo si necesita un catálogo nuevo (ej. 'Territorios'). "
            "Para agregar valores a uno existente, use la pestaña anterior."
        )
        with st.form("form_nueva_categoria"):
            clave = st.text_input(
                "Clave interna (sin espacios, ej. 'territorios')", key="nc_clave"
            )
            nombre_visible = st.text_input(
                "Nombre visible (ej. 'Territorios')", key="nc_nombre_visible"
            )
            descripcion = st.text_area("Descripción (opcional)", key="nc_descripcion")
            componente_nuevo = st.selectbox(
                "¿Dónde debe aparecer este campo?",
                ["Componente de Indicador", "Componente de Fuente"],
                help="Determina en qué formulario se mostrará como campo nuevo.",
                key="nc_componente",
            )
            aplica_a_nuevo = (
                "indicador" if componente_nuevo == "Componente de Indicador" else "fuente"
            )
            if st.form_submit_button("Crear categoría"):
                ok, msg = crear_categoria(
                    clave, nombre_visible, descripcion, _usuario_id(), aplica_a=aplica_a_nuevo
                )
                if ok:
                    marcar_limpieza({
                        "nc_clave": "",
                        "nc_nombre_visible": "",
                        "nc_descripcion": "",
                        "nc_componente": "Componente de Indicador",
                    })
                    marcar_mensaje("success", msg, seccion="nueva_categoria")
                    st.rerun()
                else:
                    st.error(msg)

        st.markdown("---")
        st.subheader("🛠️ Categorías personalizadas existentes")
        st.caption(
            "Categorías creadas manualmente (no son los 27 campos oficiales de la Matriz). "
            "Aquí puede asignar/cambiar dónde se ven o eliminarlas."
        )
        categorias_custom = listar_categorias_personalizadas(solo_activas=False)
        if not categorias_custom:
            st.info("Todavía no hay categorías personalizadas.")
        else:
            opciones_custom = {
                f"{c['nombre_visible']} ({c['aplica_a'] or 'sin asignar'})": c
                for c in categorias_custom
            }
            sel_custom_label = st.selectbox(
                "Categoría personalizada", list(opciones_custom.keys())
            )
            c_sel = opciones_custom[sel_custom_label]

            col_comp, col_del = st.columns(2)
            with col_comp:
                st.markdown("**📍 Dónde se muestra**")
                opciones_comp = ["Componente de Indicador", "Componente de Fuente"]
                idx_actual = 0 if c_sel["aplica_a"] != "fuente" else 1
                comp_sel = st.selectbox(
                    "Componente", opciones_comp, index=idx_actual,
                    key=f"comp_sel_{c_sel['id']}",
                )
                if st.button("💾 Guardar componente"):
                    nuevo_aplica_a = (
                        "indicador" if comp_sel == "Componente de Indicador" else "fuente"
                    )
                    ok, msg = actualizar_aplica_a_categoria(
                        c_sel["id"], nuevo_aplica_a, _usuario_id()
                    )
                    if ok:
                        marcar_mensaje("success", msg, seccion="nueva_categoria")
                        st.rerun()
                    else:
                        st.error(msg)

            with col_del:
                st.markdown("**🗑️ Eliminar categoría**")
                st.caption("Solo se puede eliminar si ninguno de sus valores está en uso.")
                if st.button(
                    "🗑️ Eliminar esta categoría", type="primary",
                    key=f"del_cat_{c_sel['id']}",
                ):
                    _confirmar_eliminacion_categoria(c_sel["id"], c_sel["nombre_visible"])

    # ── Tab 3: historial de cambios en Auxiliares ────────────────────────────
    with tab_historial:
        opciones_cat_hist = {"Todas las categorías": None}
        opciones_cat_hist.update({c["nombre_visible"]: c["clave"] for c in categorias})
        filtro_nombre = st.selectbox(
            "Filtrar por categoría", list(opciones_cat_hist.keys()), key="hist_filtro"
        )
        historial = obtener_historial(categoria_clave=opciones_cat_hist[filtro_nombre])

        if historial:
            df_h = pd.DataFrame(historial)
            cols = [
                c for c in
                ["timestamp", "categoria", "accion", "valor_anterior", "valor_nuevo", "usuario"]
                if c in df_h.columns
            ]
            st.dataframe(
                df_h[cols].rename(columns={
                    "timestamp": "Fecha", "categoria": "Categoría", "accion": "Acción",
                    "valor_anterior": "Valor anterior", "valor_nuevo": "Valor nuevo",
                    "usuario": "Usuario",
                }),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("Todavía no hay cambios registrados.")
