"""views/actualizar_indicador.py — Actualización de indicadores y gestión de fuentes (editor/administrador)."""

import pandas as pd
import streamlit as st

from config import (
    ESTADO_PUBLICACION_BORRADOR,
    ESTADOS_INDICADOR,
    OPCIONES_ARTICULACION_FUENTES,
    OPCIONES_C1_METODOLOGIA,
    OPCIONES_C21_EXISTENCIA_FUENTE,
    OPCIONES_C31_DESAGREGACION,
    OPCIONES_ESTRUCTURA_DATOS,
    OPCIONES_SI_NO,
    OPCIONES_VARIABLES_CALCULO,
)
from data import database as db_mod
from models.crud_indicadores import (
    actualizar_fuente,
    agregar_fuente,
    eliminar_fuente,
    modificar_indicador,
    obtener_ejes_politicas_extra,
    obtener_indicador_por_id,
    obtener_indicadores_para_referencia,
)
from security.auth import require_role
from utils.ui_mensajes import marcar_mensaje, mostrar_mensaje_pendiente
from views._form_indicador_shared import (
    campos_personalizados,
    construir_datos_factibilidad,
    construir_datos_fuente,
    construir_datos_indicador,
    indice_seguro,
    selectbox_auxiliar,
    usuario_id,
)
from views._validaciones_consistencia import (
    errores_desagregacion,
    errores_ficha_tecnica_metodologia,
    errores_fuente_sin_fuente,
    errores_ioe_ra_cuestionario_global,
    errores_metodo_calculo,
    errores_requerimiento_clasificacion,
)


# ---------------------------------------------------------------------------
# Diálogo de confirmación de eliminación de fuente
# ---------------------------------------------------------------------------

@st.dialog("¿Confirmar eliminación de fuente?")
def _confirmar_eliminar_fuente(fuente_id: int, etiqueta: str, total_fuentes: int) -> None:
    """Diálogo modal de confirmación antes de eliminar una fuente de un
    indicador; advierte si es la única fuente registrada."""
    st.write(f"Está a punto de eliminar la fuente: **{etiqueta}**")
    if total_fuentes <= 1:
        st.warning(
            "⚠️ Esta es la única fuente del indicador. Si la elimina, el indicador "
            "quedará sin ninguna fuente registrada."
        )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            ok, msg = eliminar_fuente(fuente_id, usuario_id())
            marcar_mensaje("success" if ok else "error", msg, seccion="fuentes")
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


# ---------------------------------------------------------------------------
# Diálogo de confirmación de vinculación de indicador referenciado
# ---------------------------------------------------------------------------

@st.dialog("¿Confirmar vinculación de indicador referenciado?")
def _confirmar_guardado_referenciado() -> None:
    """Diálogo modal de confirmación antes de guardar un indicador que
    referencia a otro(s); advierte que se sobrescribirán fuente,
    factibilidad y descripción en los indicadores vinculados. Se dispara
    solo cuando el formulario se envía con al menos un 'Indicador
    Referenciado' seleccionado — el resto de los cambios del formulario
    queda pendiente de este mismo Confirmar/Cancelar (no se guarda nada
    hasta que el usuario decida aquí)."""
    datos = st.session_state["_pendiente_actualizar_referenciado"]
    st.warning(
        "⚠️ Recuerda que cuando un indicador es referenciado, se sincronizan "
        "automáticamente su fuente, su factibilidad y su descripción hacia "
        "el/los indicador(es) seleccionados (sobrescribiendo lo que "
        "tuvieran). ¿Estás seguro de continuar?"
    )
    st.caption("Indicador(es) referenciado(s): " + ", ".join(datos["etiquetas_ref"]))
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Sí, guardar y sincronizar", type="primary", width="stretch"):
            exito, msg = modificar_indicador(
                datos["id_mod"], datos["datos_indicador"], datos["datos_factibilidad"],
                usuario_id=usuario_id(),
                campos_personalizados_indicador=datos["campos_custom_indicador"],
            )
            del st.session_state["_pendiente_actualizar_referenciado"]
            marcar_mensaje("success" if exito else "error", msg)
            st.rerun()
    with col2:
        if st.button("Cancelar", width="stretch"):
            del st.session_state["_pendiente_actualizar_referenciado"]
            st.rerun()


# ---------------------------------------------------------------------------
# Vista principal
# ---------------------------------------------------------------------------

@require_role(["editor", "supervisor"])
def mostrar_actualizar_indicador() -> None:
    """Vista principal de edición: permite seleccionar un indicador existente,
    modificar sus datos y los de sus fuentes asociadas, y agregar o eliminar
    fuentes. Accesible para roles editor y supervisor."""
    st.header("🔄 Actualización de Indicadores")
    mostrar_mensaje_pendiente()
    st.info(
        "📝 Al guardar, este indicador vuelve a estado **Borrador** — no será "
        "visible en la vista pública sin sesión hasta que un supervisor apruebe "
        "los cambios desde 'Aprobar Indicadores'."
    )
    rol_actual = (st.session_state.get("usuario") or {}).get("rol")
    es_supervisor = rol_actual == "supervisor"

    conn = db_mod.obtener_conexion()
    df = pd.read_sql_query(
        "SELECT id, codigo, indicador FROM indicadores ORDER BY codigo", conn
    )
    conn.close()

    if df.empty:
        st.info("No hay indicadores registrados.")
        return

    opciones = {
        f"{codigo} — {indicador}": id_
        for id_, codigo, indicador in zip(
            df["id"], df["codigo"], df["indicador"], strict=True
        )
    }
    etiquetas = list(opciones.keys())

    # Si se llegó aquí desde 'Aprobar Indicadores' → "Editar antes de
    # aprobar", preselecciona ese indicador en vez del primero de la lista
    # (ver views/aprobar_indicadores.py::_ir_a_actualizar_indicador). Se
    # consume una sola vez: no vuelve a forzar la selección si el
    # supervisor navega manualmente después.
    id_precargado = st.session_state.pop("_indicador_a_editar_id", None)
    indice_inicial = 0
    if id_precargado is not None:
        for i, etiqueta in enumerate(etiquetas):
            if opciones[etiqueta] == id_precargado:
                indice_inicial = i
                break

    sel = st.selectbox(
        "Seleccione el indicador a modificar:", etiquetas, index=indice_inicial
    )
    id_mod = opciones[sel]

    datos = obtener_indicador_por_id(id_mod)
    ind = datos["indicador"]
    calc = datos["factibilidad"]
    pares_extra_actuales = obtener_ejes_politicas_extra(id_mod)

    st.write(f"**ID:** {id_mod} | **Código:** {ind.get('codigo')}")
    if ind.get("estado_indicador") == "Desactivado":
        st.warning(
            "⚠️ Este indicador está **Desactivado**: no aparece en el dashboard, "
            "consultas, fichas ni exportaciones. Cambie 'Estado Indicador' a 'Activo' para restaurarlo."
        )
    st.caption("Los campos marcados con 🧩 provienen del catálogo administrado en 'Auxiliares'.")

    # ── Formulario principal (indicador + factibilidad) ──────────────────────
    with st.form("form_actualizar"):
        tab_demanda, tab_factibilidad = st.tabs([
            "📋 Componente de Indicador",
            "📐 Cálculo de Factibilidad",
        ])

        with tab_demanda:
            col1, col2 = st.columns(2)
            with col1:
                u_codigo = st.text_input("Código", value=ind.get("codigo", ""))
                u_estado = st.selectbox(
                    "Estado Indicador", ESTADOS_INDICADOR,
                    index=indice_seguro(ESTADOS_INDICADOR, ind.get("estado_indicador")),
                    help="'Desactivado' oculta el indicador sin borrarlo. Solo el rol "
                         "supervisor puede desactivar/reactivar indicadores.",
                    disabled=not es_supervisor,
                )
                # Punto 2 (reestructuración de roles): ya no es un selectbox
                # editable — toda edición vuelve el indicador a 'borrador' y
                # solo un supervisor puede volver a publicarlo, desde
                # 'Aprobar Indicadores' (ver views/aprobar_indicadores.py).
                u_estado_publicacion = ESTADO_PUBLICACION_BORRADOR
                opciones_ref = obtener_indicadores_para_referencia(excluir_id=id_mod)
                mapa_ref = {
                    f"{o['codigo']} — {o['indicador']} ({o['generador_demanda'] or 's/g'})": o["codigo"]
                    for o in opciones_ref
                }
                codigos_actuales = {
                    c.strip()
                    for c in (ind.get("indicadores_duplicados") or "").split(",")
                    if c.strip()
                }
                default_ref = [label for label, cod in mapa_ref.items() if cod in codigos_actuales]
                seleccion_ref = st.multiselect(
                    "🔗 Indicador Referenciado", list(mapa_ref.keys()), default=default_ref,
                    help="⚠️ Al vincular un indicador aquí, al guardar se copian "
                         "automáticamente su fuente, su factibilidad y su descripción "
                         "hacia el/los indicador(es) seleccionados (sobrescribiendo lo "
                         "que tuvieran). Verifique la selección antes de guardar: si se "
                         "vincula por error, la sincronización no se revierte sola — "
                         "debe corregir manualmente los datos sobrescritos en el "
                         "indicador afectado.",
                )
                codigos_ref_manual = [mapa_ref[s] for s in seleccion_ref]
                u_gen_txt, u_gen_id = selectbox_auxiliar("generador_demanda", "🧩 Generador", id_actual=ind.get("generador_demanda_id"))
                u_eje_txt, u_eje_id = selectbox_auxiliar("eje", "🧩 Eje", id_actual=ind.get("eje_id"))
                u_sector_txt, u_sector_id = selectbox_auxiliar("sector_ioe", "🧩 Sector IOE", id_actual=ind.get("sector_ioe_id"))
                u_area_txt, u_area_id = selectbox_auxiliar("area_misional_one", "🧩 Área Misional ONE", id_actual=ind.get("area_misional_one_id"))
            with col2:
                u_pol_txt, u_pol_id = selectbox_auxiliar("politica_gobierno", "🧩 Política de gobierno", id_actual=ind.get("politica_gobierno_id"))
                u_nombre = st.text_input("Nombre del Indicador", value=ind.get("indicador", "") or "")
                u_dom_txt, u_dom_id = selectbox_auxiliar("dominio_actividad_estadistica", "🧩 Dominio", id_actual=ind.get("dominio_actividad_estadistica_id"))
                u_subdom_txt, u_subdom_id = selectbox_auxiliar("subdominio_actividad_estadistica", "🧩 Sub-Dominio", id_actual=ind.get("subdominio_actividad_estadistica_id"))
                u_per_txt, u_per_id = selectbox_auxiliar("periodicidad_indicador", "🧩 Periodicidad indicador", id_actual=ind.get("periodicidad_indicador_id"))

            st.markdown("**Ejes/Políticas de gobierno adicionales** 🧩 *(opcional)*")
            st.caption("Los pares existentes vienen precargados; solo edite o agregue pares EXTRA.")
            pares_extra_raw = []
            for _i in range(1, 4):
                eje_actual = pares_extra_actuales[_i - 1][0] if _i - 1 < len(pares_extra_actuales) else None
                pol_actual = pares_extra_actuales[_i - 1][1] if _i - 1 < len(pares_extra_actuales) else None
                cxe1, cxe2 = st.columns(2)
                with cxe1:
                    # La key incluye id_mod: sin eso, Streamlit conserva el
                    # valor de session_state de esta key entre reruns e
                    # IGNORA el id_actual recién calculado — si en la misma
                    # sesión se edita otro indicador (o se vuelve a este vía
                    # "✏️ Editar antes de aprobar" después de haber visto
                    # otro), el campo mostraba el valor del indicador
                    # anterior en vez del real, dando la impresión de que el
                    # eje/política adicional recién guardado "se borró" (bug
                    # reportado por Randy). Con la key scoped por indicador,
                    # cada indicador tiene su propio session_state y el
                    # id_actual real siempre se respeta al cambiar de uno a
                    # otro.
                    _, eje_extra_id = selectbox_auxiliar(
                        "eje", f"Eje adicional {_i}", id_actual=eje_actual,
                        opcional=True, key=f"ueje_extra_{id_mod}_{_i}",
                    )
                with cxe2:
                    _, pol_extra_id = selectbox_auxiliar(
                        "politica_gobierno", f"Política adicional {_i}", id_actual=pol_actual,
                        opcional=True, key=f"upol_extra_{id_mod}_{_i}",
                    )
                pares_extra_raw.append((eje_extra_id, pol_extra_id))
            # Antes se exigía `e and p` (ambos lados presentes) para
            # conservar un par extra — pero la tabla indicador_ejes_politicas
            # permite eje_id/politica_gobierno_id nulos individualmente (ver
            # data/database.py) y sincronizar_ejes_politicas() ya soporta un
            # solo lado presente. Con `and`, si el usuario llenaba SOLO el
            # eje o SOLO la política de un par extra, se descartaba en
            # silencio antes de guardarse: no aparecía ni en la BD ni como
            # cambio pendiente de aprobar (bug reportado por Randy: "si solo
            # agrego uno, no se ve el cambio"). Con `or` se conserva el par
            # parcial y sí se detecta/guarda.
            ejes_politicas_extra = [(e, p) for e, p in pares_extra_raw if e or p]

            col3, col4 = st.columns(2)
            with col3:
                u_met_txt, u_met_id = selectbox_auxiliar("metodo_calculo", "🧩 Método de cálculo", id_actual=ind.get("metodo_calculo_id"))
                u_fic_txt, u_fic_id = selectbox_auxiliar("ficha_tecnica", "🧩 Ficha técnica", id_actual=ind.get("ficha_tecnica_id"))
                u_alc_txt, u_alc_id = selectbox_auxiliar("alcance_metodologico", "🧩 Alcance metodológico", id_actual=ind.get("alcance_metodologico_id"))
                u_ente = st.text_input("Ente responsable", value=ind.get("ente_responsable_metodologia", "") or "")
            with col4:
                u_num = st.text_input("Numerador", value=ind.get("numerador", "") or "")
                u_den = st.text_input("Denominador", value=ind.get("denominador", "") or "")
                u_uni = st.text_input("Unidad de medida", value=ind.get("unidad_medida", "") or "")
                u_req_txt, u_req_id = selectbox_auxiliar("requerimiento_clasificacion", "🧩 Requerimiento de clasificación", id_actual=ind.get("requerimiento_clasificacion_id"))
                u_esp = st.text_input("Especificar clasificación", value=ind.get("especificar_clasificacion", "") or "")

            st.markdown("**Desagregaciones del indicador** 🧩")
            c1, c2, c3, c4, c5 = st.columns(5)
            # Mismo bug de fondo que los ejes/políticas adicionales: estas
            # keys eran estáticas (sin id_mod), así que al cambiar de
            # indicador dentro de la misma sesión sin recargar la app
            # (p. ej. probar varios indicadores seguidos, o volver aquí vía
            # "✏️ Editar antes de aprobar" después de haber visto otro),
            # Streamlit conservaba el valor de session_state del indicador
            # ANTERIOR e ignoraba el id_actual del que se acaba de
            # seleccionar. Se agrega id_mod a cada key para que cada
            # indicador tenga su propio estado.
            with c1: _, u_sexo_id = selectbox_auxiliar("sexo_indicador", "Sexo", id_actual=ind.get("sexo_id"), key=f"ui_sexo_{id_mod}")
            with c2: _, u_edad_id = selectbox_auxiliar("edad_indicador", "Edad", id_actual=ind.get("edad_id"), key=f"ui_edad_{id_mod}")
            with c3: _, u_terr_id = selectbox_auxiliar("territorio_indicador", "Territorio", id_actual=ind.get("territorio_id"), key=f"ui_terr_{id_mod}")
            with c4: _, u_disc_id = selectbox_auxiliar("discapacidad_indicador", "Discapacidad", id_actual=ind.get("discapacidad_id"), key=f"ui_disc_{id_mod}")
            with c5: _, u_ning_id = selectbox_auxiliar("nivel_ingreso_indicador", "Nivel ingreso", id_actual=ind.get("nivel_ingreso_id"), key=f"ui_ning_{id_mod}")

            st.markdown("---")
            # Mismo bug que las desagregaciones/ejes adicionales de arriba:
            # el prefijo de key debe incluir id_mod para que cada indicador
            # tenga su propio estado de session_state.
            campos_custom_indicador, _ = campos_personalizados("indicador", f"ui_custom_{id_mod}", entidad_id=id_mod, opcional=True)

        with tab_factibilidad:
            opts_c1 = OPCIONES_C1_METODOLOGIA
            # Compatibilidad con texto legado de c1_metodologia (previo julio-2026)
            _c1_legado = {
                "Indicador sin metodología definida, pero el método se establece por criterio experto":
                    "Indicador sin metodología definida, pero el método de cálculo se puede establecer mediante criterio experto.",
            }
            c1_actual = _c1_legado.get(calc.get("c1_metodologia"), calc.get("c1_metodologia"))

            col1, col2 = st.columns(2)
            with col1:
                u_c1 = st.selectbox("C1. Metodología", opts_c1, index=indice_seguro(opts_c1, c1_actual))
                u_c21 = st.selectbox("C2.1 Existencia fuente", OPCIONES_C21_EXISTENCIA_FUENTE, index=indice_seguro(OPCIONES_C21_EXISTENCIA_FUENTE, calc.get("c21_existencia_fuente")))
                u_c22 = st.selectbox("C2.2 Disponibilidad/accesibilidad", OPCIONES_SI_NO, index=indice_seguro(OPCIONES_SI_NO, calc.get("c22_disponibilidad")))
                u_c23 = st.selectbox("C2.3 Periodicidad", OPCIONES_SI_NO, index=indice_seguro(OPCIONES_SI_NO, calc.get("c23_periodicidad_establecida")))
                u_c31 = st.selectbox("C3.1 Desagregación", OPCIONES_C31_DESAGREGACION, index=indice_seguro(OPCIONES_C31_DESAGREGACION, calc.get("c31_posee_desagregacion")))
                u_req = st.number_input("Desagregaciones requeridas", min_value=0, value=int(calc.get("num_desagregaciones_requeridas") or 0))
                u_disp = st.number_input("Desagregaciones disponibles", min_value=0, value=int(calc.get("num_desagregaciones_disponibles") or 0))
            with col2:
                u_art = st.selectbox("Articulación", OPCIONES_ARTICULACION_FUENTES, index=indice_seguro(OPCIONES_ARTICULACION_FUENTES, calc.get("articulacion_fuentes")))
                u_arm = st.selectbox("Armonización", OPCIONES_SI_NO, index=indice_seguro(OPCIONES_SI_NO, calc.get("armonizacion_conceptual")))
                u_sub = st.selectbox("Subregistro", OPCIONES_SI_NO, index=indice_seguro(OPCIONES_SI_NO, calc.get("subregistro_cobertura")))
                u_cob = st.selectbox("Cobertura Territorial", OPCIONES_SI_NO, index=indice_seguro(OPCIONES_SI_NO, calc.get("cobertura_territorial")))
                opts_est = OPCIONES_ESTRUCTURA_DATOS
                u_est = st.selectbox("Estructura de datos", opts_est, index=indice_seguro(opts_est, calc.get("estructura_datos")))
                opts_uso = OPCIONES_VARIABLES_CALCULO
                u_var = st.selectbox("Uso de Clasificaciones", opts_uso, index=indice_seguro(opts_uso, calc.get("variables_calculo")))

            if calc.get("score_factibilidad_final") is not None:
                st.info(
                    f"**Score actual:** {calc['score_factibilidad_final']} pts — "
                    f"**{calc['categoria_factibilidad']}** (se recalculará al guardar)"
                )

        if st.form_submit_button("⚡ Aplicar Cambios y Recalcular Factibilidad"):
            codigo_limpio = u_codigo.strip()

            # Validaciones de consistencia (formulario de consistencia,
            # agosto-2026). No incluye las reglas ligadas a "Existencia de
            # fuente" (IOE/RA, campos "No aplica"): en esta vista las
            # fuentes se editan en formularios separados (ver más abajo,
            # "Gestión de fuentes"), fuera de este st.form principal — se
            # validan allí, donde "Existencia de fuente" sí convive con
            # IOE/RA/etc. en el mismo formulario.
            errores_consistencia: list[str] = []
            errores_consistencia += errores_requerimiento_clasificacion(u_req_txt, u_esp, u_var)
            errores_consistencia += errores_metodo_calculo(u_met_txt, u_num, u_den)
            errores_consistencia += errores_ficha_tecnica_metodologia(u_fic_txt, u_c1)
            errores_consistencia += errores_desagregacion(u_c31, u_req, u_disp)

            if not codigo_limpio:
                st.error("El código del indicador es obligatorio.")
            elif errores_consistencia:
                st.error(
                    "⚠️ Se encontraron inconsistencias entre campos relacionados:\n\n"
                    + "\n".join(f"- {e}" for e in errores_consistencia)
                )
            else:
                datos_indicador = construir_datos_indicador(
                    codigo=codigo_limpio, estado_indicador=u_estado,
                    estado_publicacion=u_estado_publicacion,
                    referencias_manuales=codigos_ref_manual,
                    ejes_politicas_extra=ejes_politicas_extra,
                    eje_id=u_eje_id, politica_gobierno_id=u_pol_id,
                    generador_demanda_id=u_gen_id, indicador=u_nombre,
                    dominio_actividad_estadistica_id=u_dom_id,
                    subdominio_actividad_estadistica_id=u_subdom_id,
                    area_misional_one_id=u_area_id, sector_ioe_id=u_sector_id,
                    metodo_calculo_id=u_met_id, ficha_tecnica_id=u_fic_id,
                    numerador=u_num, denominador=u_den, unidad_medida=u_uni,
                    requerimiento_clasificacion_id=u_req_id,
                    especificar_clasificacion=u_esp,
                    sexo_id=u_sexo_id, edad_id=u_edad_id,
                    territorio_id=u_terr_id, discapacidad_id=u_disc_id,
                    nivel_ingreso_id=u_ning_id,
                    periodicidad_indicador_id=u_per_id,
                    ente_responsable_metodologia=u_ente,
                    alcance_metodologico_id=u_alc_id,
                )
                datos_factibilidad = construir_datos_factibilidad(
                    c1_metodologia=u_c1, c21_existencia_fuente=u_c21,
                    c22_disponibilidad=u_c22, c23_periodicidad_establecida=u_c23,
                    c31_posee_desagregacion=u_c31,
                    num_desagregaciones_requeridas=u_req,
                    num_desagregaciones_disponibles=u_disp,
                    articulacion_fuentes=u_art, armonizacion_conceptual=u_arm,
                    subregistro_cobertura=u_sub, cobertura_territorial=u_cob,
                    estructura_datos=u_est, variables_calculo=u_var,
                )
                if codigos_ref_manual:
                    # Hay indicadores referenciados seleccionados: no se guarda
                    # todavía, se difiere al diálogo de confirmación (ver
                    # _confirmar_guardado_referenciado más abajo, fuera del
                    # form — los diálogos de este código no se disparan de
                    # forma confiable desde dentro de un st.form).
                    st.session_state["_pendiente_actualizar_referenciado"] = {
                        "id_mod": id_mod,
                        "datos_indicador": datos_indicador,
                        "datos_factibilidad": datos_factibilidad,
                        "campos_custom_indicador": campos_custom_indicador,
                        "etiquetas_ref": seleccion_ref,
                    }
                    st.rerun()
                else:
                    exito, msg = modificar_indicador(
                        id_mod, datos_indicador, datos_factibilidad,
                        usuario_id=usuario_id(),
                        campos_personalizados_indicador=campos_custom_indicador,
                    )
                    st.success(msg) if exito else st.error(msg)

    if st.session_state.get("_pendiente_actualizar_referenciado"):
        _confirmar_guardado_referenciado()

    # ── Gestión de fuentes (fuera del form) ──────────────────────────────────
    st.markdown("---")
    st.subheader("📡 Fuentes de información")
    st.caption(
        "Un indicador puede tener más de una fuente. Agregar, editar o eliminar "
        "una fuente aquí se guarda de inmediato — no requiere 'Aplicar Cambios' arriba."
    )
    mostrar_mensaje_pendiente(seccion="fuentes")

    fuentes_actuales = obtener_indicador_por_id(id_mod)["fuentes"]

    if fuentes_actuales:
        df_fuentes = pd.DataFrame([{
            "ID": f["id"],
            "Nombre": f.get("nombre_fuente") or "(sin nombre)",
            "Tipo": f.get("tipo_fuente") or "",
            "Institución": f.get("institucion_productora") or "",
            "Periodicidad": f.get("periodicidad") or "",
            "Existencia": f.get("existencia_fuente") or "",
        } for f in fuentes_actuales])
        st.dataframe(df_fuentes, width="stretch", hide_index=True)
    else:
        st.warning("⚠️ Este indicador todavía no tiene ninguna fuente registrada.")

    col_edit_f, col_add_f = st.columns(2)

    # ── Editar / Eliminar fuente existente ───────────────────────────────────
    with col_edit_f:
        st.markdown("**✏️ Editar / Eliminar una fuente existente**")
        if fuentes_actuales:
            opciones_f = {f"{f['id']} — {f.get('nombre_fuente') or 's/n'}": f for f in fuentes_actuales}
            sel_f_label = st.selectbox("Seleccione la fuente", list(opciones_f.keys()), key="sel_fuente_editar")
            f_sel = opciones_f[sel_f_label]

            with st.form(f"form_editar_fuente_{f_sel['id']}"):
                fcol1, fcol2 = st.columns(2)
                with fcol1:
                    ef_txt, ef_id = selectbox_auxiliar("existencia_fuente", "🧩 Existencia de fuente", id_actual=f_sel.get("existencia_fuente_id"), key=f"ef_edit_{f_sel['id']}")
                    nf_txt, nf_id = selectbox_auxiliar("nombre_fuente", "🧩 Nombre de la fuente", id_actual=f_sel.get("nombre_fuente_id"), key=f"nf_edit_{f_sel['id']}")
                    tf_txt, tf_id = selectbox_auxiliar("tipo_fuente", "🧩 Tipo de fuente", id_actual=f_sel.get("tipo_fuente_id"), key=f"tf_edit_{f_sel['id']}")
                    inst_txt, inst_id = selectbox_auxiliar("institucion_productora", "🧩 Institución productora", id_actual=f_sel.get("institucion_productora_id"), key=f"inst_edit_{f_sel['id']}")
                    pf_txt, pf_id = selectbox_auxiliar("periodicidad_fuente", "🧩 Periodicidad de la fuente", id_actual=f_sel.get("periodicidad_id"), key=f"pf_edit_{f_sel['id']}")
                with fcol2:
                    ioe_txt, ioe_id = selectbox_auxiliar("ioe_fuente", "🧩 IOE", id_actual=f_sel.get("ioe_id"), key=f"ioe_edit_{f_sel['id']}")
                    ra_txt, ra_id = selectbox_auxiliar("ra_fuente", "🧩 RA", id_actual=f_sel.get("ra_id"), key=f"ra_edit_{f_sel['id']}")
                    cal_txt, cal_id = selectbox_auxiliar("calculado_datos_agregados", "🧩 Calculado/Dato agregado", id_actual=f_sel.get("calculado_datos_agregados_id"), key=f"cal_edit_{f_sel['id']}")
                    hiper = st.text_input("Hipervínculo", value=f_sel.get("hipervinculo_ultimo_calculo", "") or "", key=f"hiper_edit_{f_sel['id']}")
                    anio = st.text_input("Año último dato", value=f_sel.get("anio_ultimo_dato_disponible", "") or "", key=f"anio_edit_{f_sel['id']}")

                st.markdown("Desagregaciones de la fuente 🧩")
                d1, d2, d3, d4, d5 = st.columns(5)
                with d1: sx_txt, sx_id = selectbox_auxiliar("sexo_fuente", "Sexo", id_actual=f_sel.get("sexo_id"), key=f"sx_edit_{f_sel['id']}")
                with d2: ed_txt, ed_id = selectbox_auxiliar("edad_fuente", "Edad", id_actual=f_sel.get("edad_id"), key=f"ed_edit_{f_sel['id']}")
                with d3: tr_txt, tr_id = selectbox_auxiliar("territorio_fuente", "Territorio", id_actual=f_sel.get("territorio_id"), key=f"tr_edit_{f_sel['id']}")
                with d4: dc_txt, dc_id = selectbox_auxiliar("discapacidad_fuente", "Discapacidad", id_actual=f_sel.get("discapacidad_id"), key=f"dc_edit_{f_sel['id']}")
                with d5: ni_txt, ni_id = selectbox_auxiliar("nivel_ingreso_fuente", "Nivel ingreso", id_actual=f_sel.get("nivel_ingreso_socioeconomico_id"), key=f"ni_edit_{f_sel['id']}")
                com = st.text_area("Comentarios", value=f_sel.get("comentarios", "") or "", key=f"com_edit_{f_sel['id']}")

                st.markdown("---")
                campos_custom_f_edit, _ = campos_personalizados("fuente", f"fe_custom_{f_sel['id']}", entidad_id=f_sel["id"], opcional=True)

                errores_consistencia_f = []
                errores_consistencia_f += errores_fuente_sin_fuente(
                    ef_txt,
                    nombre_f_txt=nf_txt, tipo_txt=tf_txt, inst_f_txt=inst_txt,
                    per_f_txt=pf_txt, ioe_txt=ioe_txt, ra_txt=ra_txt, cal_txt=cal_txt,
                    hiper_f=hiper, anio_f=anio,
                    sexo_f_txt=sx_txt, edad_f_txt=ed_txt, terr_f_txt=tr_txt,
                    disc_f_txt=dc_txt, ning_f_txt=ni_txt,
                )
                errores_consistencia_f += errores_ioe_ra_cuestionario_global(ef_txt, tf_txt, ioe_txt, ra_txt)

                if st.form_submit_button("💾 Guardar cambios de esta fuente"):
                    if errores_consistencia_f:
                        st.error(
                            "⚠️ Se encontraron inconsistencias entre campos relacionados:\n\n"
                            + "\n".join(f"- {e}" for e in errores_consistencia_f)
                        )
                        st.stop()
                    datos_f = construir_datos_fuente(
                        existencia_fuente_id=ef_id, nombre_fuente_id=nf_id,
                        tipo_fuente_id=tf_id, institucion_productora_id=inst_id,
                        periodicidad_id=pf_id,
                        sexo_id=sx_id, edad_id=ed_id, territorio_id=tr_id,
                        discapacidad_id=dc_id, nivel_ingreso_socioeconomico_id=ni_id,
                        ioe_id=ioe_id, ra_id=ra_id, calculado_datos_agregados_id=cal_id,
                        hipervinculo_ultimo_calculo=hiper,
                        anio_ultimo_dato_disponible=anio,
                        comentarios=com,
                    )
                    ok, msg = actualizar_fuente(f_sel["id"], datos_f, usuario_id(), campos_personalizados=campos_custom_f_edit)
                    if ok:
                        marcar_mensaje("success", msg, seccion="fuentes")
                        st.rerun()
                    else:
                        st.error(msg)

            if st.button("🗑️ Eliminar esta fuente", key=f"btn_del_fuente_{f_sel['id']}"):
                _confirmar_eliminar_fuente(f_sel["id"], sel_f_label, len(fuentes_actuales))
        else:
            st.caption("No hay fuentes para editar todavía. Agregue la primera a la derecha →")

    # ── Agregar nueva fuente ─────────────────────────────────────────────────
    with col_add_f:
        st.markdown("**➕ Agregar una nueva fuente**")
        with st.form("form_agregar_fuente", clear_on_submit=True):
            acol1, acol2 = st.columns(2)
            with acol1:
                n_ef_txt, n_ef_id = selectbox_auxiliar("existencia_fuente", "🧩 Existencia de fuente", id_actual=None, opcional=True, key="n_ef")
                n_nf_txt, n_nf_id = selectbox_auxiliar("nombre_fuente", "🧩 Nombre de la fuente", id_actual=None, key="n_nf")
                n_tf_txt, n_tf_id = selectbox_auxiliar("tipo_fuente", "🧩 Tipo de fuente", id_actual=None, opcional=True, key="n_tf")
                n_inst_txt, n_inst_id = selectbox_auxiliar("institucion_productora", "🧩 Institución productora", id_actual=None, opcional=True, key="n_inst")
                n_pf_txt, n_pf_id = selectbox_auxiliar("periodicidad_fuente", "🧩 Periodicidad de la fuente", id_actual=None, opcional=True, key="n_pf")
            with acol2:
                n_ioe_txt, n_ioe_id = selectbox_auxiliar("ioe_fuente", "🧩 IOE", id_actual=None, opcional=True, key="n_ioe")
                n_ra_txt, n_ra_id = selectbox_auxiliar("ra_fuente", "🧩 RA", id_actual=None, opcional=True, key="n_ra")
                n_cal_txt, n_cal_id = selectbox_auxiliar("calculado_datos_agregados", "🧩 Calculado/Dato agregado", id_actual=None, opcional=True, key="n_cal")
                n_hiper = st.text_input("Hipervínculo", key="n_hiper")
                n_anio = st.text_input("Año último dato", key="n_anio")

            st.markdown("Desagregaciones de la fuente 🧩")
            n1, n2, n3, n4, n5 = st.columns(5)
            with n1: n_sx_txt, n_sx_id = selectbox_auxiliar("sexo_fuente", "Sexo", id_actual=None, opcional=True, key="n_sx")
            with n2: n_ed_txt, n_ed_id = selectbox_auxiliar("edad_fuente", "Edad", id_actual=None, opcional=True, key="n_ed")
            with n3: n_tr_txt, n_tr_id = selectbox_auxiliar("territorio_fuente", "Territorio", id_actual=None, opcional=True, key="n_tr")
            with n4: n_dc_txt, n_dc_id = selectbox_auxiliar("discapacidad_fuente", "Discapacidad", id_actual=None, opcional=True, key="n_dc")
            with n5: n_ni_txt, n_ni_id = selectbox_auxiliar("nivel_ingreso_fuente", "Nivel ingreso", id_actual=None, opcional=True, key="n_ni")
            n_com = st.text_area("Comentarios (opcional)", key="n_com")

            st.markdown("---")
            campos_custom_f_nueva, _ = campos_personalizados("fuente", "fn_custom", entidad_id=None, opcional=True)

            errores_consistencia_nf = []
            errores_consistencia_nf += errores_fuente_sin_fuente(
                n_ef_txt,
                nombre_f_txt=n_nf_txt, tipo_txt=n_tf_txt, inst_f_txt=n_inst_txt,
                per_f_txt=n_pf_txt, ioe_txt=n_ioe_txt, ra_txt=n_ra_txt, cal_txt=n_cal_txt,
                hiper_f=n_hiper, anio_f=n_anio,
                sexo_f_txt=n_sx_txt, edad_f_txt=n_ed_txt, terr_f_txt=n_tr_txt,
                disc_f_txt=n_dc_txt, ning_f_txt=n_ni_txt,
            )
            errores_consistencia_nf += errores_ioe_ra_cuestionario_global(n_ef_txt, n_tf_txt, n_ioe_txt, n_ra_txt)

            if st.form_submit_button("➕ Agregar fuente"):
                if not n_nf_id:
                    st.error("El nombre de la fuente es obligatorio.")
                elif errores_consistencia_nf:
                    st.error(
                        "⚠️ Se encontraron inconsistencias entre campos relacionados:\n\n"
                        + "\n".join(f"- {e}" for e in errores_consistencia_nf)
                    )
                else:
                    datos_f_nueva = construir_datos_fuente(
                        existencia_fuente_id=n_ef_id, nombre_fuente_id=n_nf_id,
                        tipo_fuente_id=n_tf_id, institucion_productora_id=n_inst_id,
                        periodicidad_id=n_pf_id,
                        sexo_id=n_sx_id, edad_id=n_ed_id, territorio_id=n_tr_id,
                        discapacidad_id=n_dc_id, nivel_ingreso_socioeconomico_id=n_ni_id,
                        ioe_id=n_ioe_id, ra_id=n_ra_id, calculado_datos_agregados_id=n_cal_id,
                        hipervinculo_ultimo_calculo=n_hiper,
                        anio_ultimo_dato_disponible=n_anio,
                        comentarios=n_com,
                    )
                    ok, msg = agregar_fuente(id_mod, datos_f_nueva, usuario_id(), campos_personalizados=campos_custom_f_nueva)
                    if ok:
                        marcar_mensaje("success", msg, seccion="fuentes")
                        st.rerun()
                    else:
                        st.error(msg)
