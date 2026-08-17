"""views/crear_indicador.py — Registro de nuevos indicadores (editor/administrador)."""

import streamlit as st

from config import (
    ESTADO_ACTIVO,
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
from models.crud_indicadores import guardar_indicador, obtener_indicadores_para_referencia
from security.auth import require_role
from views._form_indicador_shared import (
    campos_personalizados,
    construir_datos_factibilidad,
    construir_datos_fuente,
    construir_datos_indicador,
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
# Validación: consistencia cuando no hay fuente
# ---------------------------------------------------------------------------
# Cuando "Existencia de fuente" (componente de Fuente) se marca como
# "No hay fuente", los criterios de factibilidad que dependen de una fuente
# no pueden tener una respuesta afirmativa. Estos son los criterios C1-C3 del
# Engine: usan el vocabulario EXACTO del Excel oficial (ver config.py), por
# lo que el valor "negativo" de cada uno debe coincidir con esas cadenas.
VALOR_SIN_FUENTE = "No hay fuente"

CRITERIOS_NEGATIVOS_SIN_FUENTE: dict[str, tuple[str, str]] = {
    # clave_interna: (etiqueta visible, valor esperado cuando no hay fuente)
    "c21": ("C2.1 Existencia fuente", VALOR_SIN_FUENTE),
    "c22": ("C2.2 Disponibilidad/accesibilidad", "No"),
    "c23": ("C2.3 Periodicidad establecida", "No"),
    "c31": ("C3.1 Posee desagregación requerida", "No"),
    "art": ("Articulación de fuentes", "No se articula"),
    "arm": ("Armonización conceptual", "No"),
    "sub": ("Subregistro/Subcobertura", "No"),
    "cob": ("Cobertura Territorial", "No"),
    "est": ("Estructura de datos", "No posee ninguna de las anteriores"),
    "var": ("Uso de Clasificaciones", "No"),
}


def _errores_consistencia_sin_fuente(exist_txt: str | None, valores_criterios: dict[str, str]) -> list[str]:
    """Si 'Existencia de fuente' es 'No hay fuente', valida que los criterios
    de factibilidad dependientes de la fuente estén todos en su valor negativo.

    valores_criterios: {clave_interna: valor_seleccionado_actual}
    Devuelve una lista de mensajes de error (vacía si todo está en orden o si
    sí hay fuente).
    """
    if exist_txt != VALOR_SIN_FUENTE:
        return []
    errores = []
    for clave, (etiqueta, esperado) in CRITERIOS_NEGATIVOS_SIN_FUENTE.items():
        actual = valores_criterios.get(clave)
        if actual != esperado:
            errores.append(f"«{etiqueta}» debe ser «{esperado}» (actualmente: «{actual}»)")
    return errores


def _campos_vacios(campos: dict[str, object]) -> list[str]:
    """Devuelve las etiquetas de los campos cuyo valor está vacío (None o
    string en blanco). `campos` es un mapa etiqueta_visible -> valor."""
    faltantes = []
    for etiqueta, valor in campos.items():
        if valor is None:
            faltantes.append(etiqueta)
        elif isinstance(valor, str) and not valor.strip():
            faltantes.append(etiqueta)
    return faltantes


# ---------------------------------------------------------------------------
# Vista principal
# ---------------------------------------------------------------------------

@require_role(["editor", "supervisor"])
def mostrar_crear_indicador() -> None:
    """Vista de registro: formulario para dar de alta un nuevo indicador con
    su(s) fuente(s) y cálculo de factibilidad inicial. Accesible para
    editores y supervisores."""
    st.header("📝 Registro de Nuevo Indicador")
    st.caption(
        "Los campos marcados con 🧩 provienen del catálogo administrado en 'Auxiliares'. "
        "Si falta un valor, un supervisor puede agregarlo ahí."
    )
    st.info(
        "📝 Este indicador quedará en estado **Borrador** al guardarlo — no será "
        "visible en la vista pública sin sesión hasta que un supervisor lo revise "
        "y apruebe desde 'Aprobar Indicadores'."
    )

    with st.form("form_crear"):
        tab_demanda, tab_oferta, tab_factibilidad = st.tabs([
            "📋 Componente de Indicador",
            "📡 Componente de Fuente",
            "📐 Cálculo de Factibilidad",
        ])

        # ── Tab: Componente de Indicador ─────────────────────────────────────
        with tab_demanda:
            col1, col2 = st.columns(2)
            with col1:
                codigo = st.text_input("Código Único del Indicador")
                estado_indicador = st.selectbox(
                    "Estado Indicador", ESTADOS_INDICADOR,
                    index=ESTADOS_INDICADOR.index(ESTADO_ACTIVO),
                    help="Uso interno: 'Desactivado' oculta el indicador del dashboard, "
                         "consultas, fichas y exportaciones.",
                )
                # Punto 2 (reestructuración de roles): ya no es un selectbox
                # editable — todo indicador nuevo entra en 'borrador' y solo
                # un supervisor puede publicarlo, desde 'Aprobar Indicadores'
                # (ver views/aprobar_indicadores.py).
                estado_publicacion = ESTADO_PUBLICACION_BORRADOR
                opciones_ref = obtener_indicadores_para_referencia()
                mapa_ref = {
                    f"{o['codigo']} — {o['indicador']} ({o['generador_demanda'] or 's/g'})": o["codigo"]
                    for o in opciones_ref
                }
                seleccion_ref = st.multiselect(
                    "🔗 Indicador Referenciado", list(mapa_ref.keys()),
                    help="El MISMO indicador que aparece bajo otro Generador de demanda. "
                         "El sistema también detecta vínculos automáticamente al guardar. "
                         "⚠️ Al vincular, se copian automáticamente su fuente, su "
                         "factibilidad y su descripción hacia el/los indicador(es) "
                         "seleccionados (sobrescribiendo lo que tuvieran). Verifique la "
                         "selección antes de guardar: si se vincula por error, la "
                         "sincronización no se revierte sola — debe corregir manualmente "
                         "los datos sobrescritos en el indicador afectado.",
                )
                codigos_ref_manual = [mapa_ref[s] for s in seleccion_ref]
                generador_txt, generador_id = selectbox_auxiliar("generador_demanda", "🧩 Generador de demanda")
                eje_txt, eje_id = selectbox_auxiliar("eje", "🧩 Eje")
                sector_txt, sector_id = selectbox_auxiliar("sector_ioe", "🧩 Sector IOE")
                area_misional_txt, area_misional_id = selectbox_auxiliar("area_misional_one", "🧩 Área Misional ONE")
            with col2:
                politica_txt, politica_id = selectbox_auxiliar("politica_gobierno", "🧩 Política de gobierno")
                nombre_ind = st.text_input("Nombre Oficial del Indicador")
                dominio_txt, dominio_id = selectbox_auxiliar(
                    "dominio_actividad_estadistica", "🧩 Dominio de actividad estadística"
                )
                subdominio_txt, subdominio_id = selectbox_auxiliar(
                    "subdominio_actividad_estadistica", "🧩 Sub-Dominio de actividad estadística"
                )
                periodicidad_txt, periodicidad_id = selectbox_auxiliar(
                    "periodicidad_indicador", "🧩 Periodicidad del indicador"
                )

            st.markdown("**Ejes/Políticas de gobierno adicionales** 🧩 *(opcional)*")
            st.caption(
                "Use esto solo si el indicador corresponde a más de un Eje/Política "
                "a la vez (como ocurre con algunos indicadores PNPSP). El par de arriba "
                "siempre se guarda; aquí solo agregue pares EXTRA."
            )
            pares_extra_raw = []
            for _i in range(1, 4):
                cxe1, cxe2 = st.columns(2)
                with cxe1:
                    _, eje_extra_id = selectbox_auxiliar("eje", f"Eje adicional {_i}", key=f"eje_extra_{_i}", opcional=True)
                with cxe2:
                    _, pol_extra_id = selectbox_auxiliar(
                        "politica_gobierno", f"Política adicional {_i}", key=f"pol_extra_{_i}"
                    , opcional=True)
                pares_extra_raw.append((eje_extra_id, pol_extra_id))
            # Ver comentario equivalente en views/actualizar_indicador.py:
            # un par extra con un solo lado presente (eje O política) es
            # válido — la tabla lo permite y sincronizar_ejes_politicas() lo
            # soporta — así que se conserva con `or`, no se descarta con `and`.
            ejes_politicas_extra = [(e, p) for e, p in pares_extra_raw if e or p]

            col3, col4 = st.columns(2)
            with col3:
                metodo_txt, metodo_id = selectbox_auxiliar("metodo_calculo", "🧩 Método de cálculo")
                ficha_txt, ficha_id = selectbox_auxiliar("ficha_tecnica", "🧩 Ficha técnica")
                alcance_txt, alcance_id = selectbox_auxiliar("alcance_metodologico", "🧩 Alcance metodológico")
                ente = st.text_input("Ente responsable de metodología")
            with col4:
                numerador = st.text_input("Numerador")
                denominador = st.text_input("Denominador")
                unidad = st.text_input("Unidad de medida")
                req_clas_txt, req_clas_id = selectbox_auxiliar(
                    "requerimiento_clasificacion", "🧩 Requerimiento de clasificación"
                )
                esp_clas = st.text_input("Especificar clasificación")

            st.markdown("**Desagregaciones del indicador** 🧩")
            c1d, c2d, c3d, c4d, c5d = st.columns(5)
            with c1d: sexo_txt, sexo_id = selectbox_auxiliar("sexo_indicador", "Sexo", key="si_sexo")
            with c2d: edad_txt, edad_id = selectbox_auxiliar("edad_indicador", "Edad", key="si_edad")
            with c3d: terr_txt, terr_id = selectbox_auxiliar("territorio_indicador", "Territorio", key="si_terr")
            with c4d: disc_txt, disc_id = selectbox_auxiliar("discapacidad_indicador", "Discapacidad", key="si_disc")
            with c5d: ning_txt, ning_id = selectbox_auxiliar("nivel_ingreso_indicador", "Nivel ingreso", key="si_ning")

            st.markdown("---")
            campos_custom_indicador, etiquetas_custom_indicador = campos_personalizados(
                "indicador", "ci_custom"
            )

        # ── Tab: Componente de Fuente ─────────────────────────────────────────
        with tab_oferta:
            st.caption(
                "Se asocia 1 fuente al crear. Para agregar más fuentes (o editar/eliminar "
                "cualquiera de ellas), use la sección '📡 Fuentes de información' en "
                "'Actualizar indicador' una vez creado."
            )
            col1, col2 = st.columns(2)
            with col1:
                exist_txt, exist_id = selectbox_auxiliar("existencia_fuente", "🧩 Existencia de fuente")
                nombre_f_txt, nombre_f_id = selectbox_auxiliar("nombre_fuente", "🧩 Nombre de la fuente")
                tipo_txt, tipo_id = selectbox_auxiliar("tipo_fuente", "🧩 Tipo de fuente")
                inst_f_txt, inst_f_id = selectbox_auxiliar("institucion_productora", "🧩 Institución productora")
                per_f_txt, per_f_id = selectbox_auxiliar("periodicidad_fuente", "🧩 Periodicidad de la fuente")
            with col2:
                ioe_txt, ioe_id = selectbox_auxiliar("ioe_fuente", "🧩 IOE", key="sf_ioe")
                ra_txt, ra_id = selectbox_auxiliar("ra_fuente", "🧩 RA", key="sf_ra")
                cal_txt, cal_id = selectbox_auxiliar("calculado_datos_agregados", "🧩 Calculado/Dato agregado")
                hiper_f = st.text_input("Hipervínculo del último cálculo")
                anio_f = st.text_input("Año del último dato disponible")

            st.markdown("**Desagregaciones de la fuente** 🧩")
            cf1, cf2, cf3, cf4, cf5 = st.columns(5)
            with cf1: sexo_f_txt, sexo_f_id = selectbox_auxiliar("sexo_fuente", "Sexo", key="sf_sexo")
            with cf2: edad_f_txt, edad_f_id = selectbox_auxiliar("edad_fuente", "Edad", key="sf_edad")
            with cf3: terr_f_txt, terr_f_id = selectbox_auxiliar("territorio_fuente", "Territorio", key="sf_terr")
            with cf4: disc_f_txt, disc_f_id = selectbox_auxiliar("discapacidad_fuente", "Discapacidad", key="sf_disc")
            with cf5: ning_f_txt, ning_f_id = selectbox_auxiliar("nivel_ingreso_fuente", "Nivel ingreso", key="sf_ning")
            com_f = st.text_area("Comentarios (opcional)")

            st.markdown("---")
            campos_custom_fuente, etiquetas_custom_fuente = campos_personalizados("fuente", "sf_custom")

        # ── Tab: Cálculo de Factibilidad ──────────────────────────────────────
        with tab_factibilidad:
            st.caption(
                "⚠️ Estos criterios NO se administran desde 'Auxiliares': son el vocabulario "
                "fijo de la fórmula oficial del Excel que usa el Engine para calcular el puntaje."
            )
            opts_c1 = OPCIONES_C1_METODOLOGIA
            col1, col2 = st.columns(2)
            with col1:
                c1 = st.selectbox("C1. Existencia de Metodología", opts_c1)
                c21 = st.selectbox("C2.1 Existencia fuente", OPCIONES_C21_EXISTENCIA_FUENTE)
                c22 = st.selectbox("C2.2 Disponibilidad/accesibilidad", OPCIONES_SI_NO)
                c23 = st.selectbox("C2.3 Periodicidad establecida", OPCIONES_SI_NO)
                c31 = st.selectbox("C3.1 Posee desagregación requerida", OPCIONES_C31_DESAGREGACION)
                req_num = st.number_input("Desagregaciones requeridas por el indicador", min_value=0, value=0)
                disp_num = st.number_input("Desagregaciones disponibles en la fuente", min_value=0, value=0)
            with col2:
                art = st.selectbox("Articulación de fuentes", OPCIONES_ARTICULACION_FUENTES)
                arm = st.selectbox("Armonización conceptual", OPCIONES_SI_NO)
                sub = st.selectbox("Subregistro/Subcobertura", OPCIONES_SI_NO)
                cob = st.selectbox("Cobertura Territorial", OPCIONES_SI_NO)
                est = st.selectbox("Estructura de datos", OPCIONES_ESTRUCTURA_DATOS)
                var = st.selectbox("Uso de Clasificaciones", OPCIONES_VARIABLES_CALCULO)

        # ── Guardar ───────────────────────────────────────────────────────────
        if st.form_submit_button("💾 Guardar Nuevo Indicador"):
            codigo_limpio = codigo.strip()
            nombre_limpio = nombre_ind.strip()

            # Punto 2 y 3: todos los campos son obligatorios, salvo los
            # explícitamente marcados como opcionales en el formulario
            # ("Ejes/Políticas adicionales", "Comentarios" y "Especificar
            # clasificación", que solo aplica si el requerimiento lo exige).
            campos_obligatorios = {
                "Código Único del Indicador": codigo_limpio,
                "Nombre Oficial del Indicador": nombre_limpio,
                "🧩 Generador de demanda": generador_id,
                "🧩 Eje": eje_id,
                "🧩 Sector IOE": sector_id,
                "Área Misional ONE": area_misional_id,
                "🧩 Política de gobierno": politica_id,
                "🧩 Dominio de actividad estadística": dominio_id,
                "🧩 Sub-Dominio de actividad estadística": subdominio_id,
                "🧩 Periodicidad del indicador": periodicidad_id,
                "🧩 Método de cálculo": metodo_id,
                "🧩 Ficha técnica": ficha_id,
                "🧩 Alcance metodológico": alcance_id,
                "Ente responsable de metodología": ente,
                "Numerador": numerador,
                "Denominador": denominador,
                "Unidad de medida": unidad,
                "🧩 Requerimiento de clasificación": req_clas_id,
                "Sexo (Indicador)": sexo_id,
                "Edad (Indicador)": edad_id,
                "Territorio (Indicador)": terr_id,
                "Discapacidad (Indicador)": disc_id,
                "Nivel ingreso (Indicador)": ning_id,
                "🧩 Existencia de fuente": exist_id,
                "🧩 Nombre de la fuente": nombre_f_id,
                "🧩 Tipo de fuente": tipo_id,
                "🧩 Institución productora": inst_f_id,
                "🧩 Periodicidad de la fuente": per_f_id,
                "🧩 IOE": ioe_id,
                "🧩 RA": ra_id,
                "🧩 Calculado/Dato agregado": cal_id,
                "Hipervínculo del último cálculo": hiper_f,
                "Año del último dato disponible": anio_f,
                "Sexo (Fuente)": sexo_f_id,
                "Edad (Fuente)": edad_f_id,
                "Territorio (Fuente)": terr_f_id,
                "Discapacidad (Fuente)": disc_f_id,
                "Nivel ingreso (Fuente)": ning_f_id,
                **etiquetas_custom_indicador,
                **etiquetas_custom_fuente,
            }
            errores_faltantes = _campos_vacios(campos_obligatorios)

            # Punto 1: si "Existencia de fuente" es "No hay fuente", los
            # criterios de factibilidad dependientes deben ir en "No".
            valores_criterios_factibilidad = {
                "c21": c21, "c22": c22, "c23": c23, "c31": c31,
                "art": art, "arm": arm, "sub": sub, "cob": cob,
                "est": est, "var": var,
            }
            errores_sin_fuente = _errores_consistencia_sin_fuente(
                exist_txt, valores_criterios_factibilidad
            )

            # Validaciones de consistencia adicionales (formulario de
            # consistencia, agosto-2026): cada una es independiente y todas
            # se acumulan para mostrarse juntas, igual que _campos_vacios.
            errores_consistencia: list[str] = []
            errores_consistencia += errores_requerimiento_clasificacion(req_clas_txt, esp_clas, var)
            errores_consistencia += errores_fuente_sin_fuente(
                exist_txt,
                nombre_f_txt=nombre_f_txt, tipo_txt=tipo_txt, inst_f_txt=inst_f_txt,
                per_f_txt=per_f_txt, ioe_txt=ioe_txt, ra_txt=ra_txt, cal_txt=cal_txt,
                hiper_f=hiper_f, anio_f=anio_f,
                sexo_f_txt=sexo_f_txt, edad_f_txt=edad_f_txt, terr_f_txt=terr_f_txt,
                disc_f_txt=disc_f_txt, ning_f_txt=ning_f_txt,
            )
            errores_consistencia += errores_ioe_ra_cuestionario_global(exist_txt, tipo_txt, ioe_txt, ra_txt)
            errores_consistencia += errores_metodo_calculo(metodo_txt, numerador, denominador)
            errores_consistencia += errores_ficha_tecnica_metodologia(ficha_txt, c1)
            errores_consistencia += errores_desagregacion(c31, req_num, disp_num, exist_txt)

            if errores_faltantes:
                st.error(
                    "⚠️ Faltan los siguientes campos obligatorios:\n\n"
                    + "\n".join(f"- {campo}" for campo in errores_faltantes)
                )
            elif errores_sin_fuente:
                st.error(
                    "⚠️ Indicó que **no hay fuente** para este indicador. "
                    "En ese caso, todos los criterios de factibilidad que dependen "
                    "de la fuente deben establecerse en su valor negativo:\n\n"
                    + "\n".join(f"- {e}" for e in errores_sin_fuente)
                )
            elif errores_consistencia:
                st.error(
                    "⚠️ Se encontraron inconsistencias entre campos relacionados:\n\n"
                    + "\n".join(f"- {e}" for e in errores_consistencia)
                )
            else:
                datos_indicador = construir_datos_indicador(
                    codigo=codigo_limpio,
                    estado_indicador=estado_indicador,
                    estado_publicacion=estado_publicacion,
                    referencias_manuales=codigos_ref_manual,
                    ejes_politicas_extra=ejes_politicas_extra,
                    eje_id=eje_id, politica_gobierno_id=politica_id,
                    generador_demanda_id=generador_id,
                    indicador=nombre_limpio,
                    dominio_actividad_estadistica_id=dominio_id,
                    subdominio_actividad_estadistica_id=subdominio_id,
                    area_misional_one_id=area_misional_id,
                    sector_ioe_id=sector_id,
                    metodo_calculo_id=metodo_id, ficha_tecnica_id=ficha_id,
                    numerador=numerador, denominador=denominador,
                    unidad_medida=unidad,
                    requerimiento_clasificacion_id=req_clas_id,
                    especificar_clasificacion=esp_clas,
                    sexo_id=sexo_id, edad_id=edad_id,
                    territorio_id=terr_id, discapacidad_id=disc_id,
                    nivel_ingreso_id=ning_id,
                    periodicidad_indicador_id=periodicidad_id,
                    ente_responsable_metodologia=ente,
                    alcance_metodologico_id=alcance_id,
                )
                datos_fuentes = [construir_datos_fuente(
                    existencia_fuente_id=exist_id,
                    nombre_fuente_id=nombre_f_id,
                    tipo_fuente_id=tipo_id,
                    institucion_productora_id=inst_f_id,
                    periodicidad_id=per_f_id,
                    sexo_id=sexo_f_id, edad_id=edad_f_id,
                    territorio_id=terr_f_id, discapacidad_id=disc_f_id,
                    nivel_ingreso_socioeconomico_id=ning_f_id,
                    ioe_id=ioe_id, ra_id=ra_id,
                    calculado_datos_agregados_id=cal_id,
                    hipervinculo_ultimo_calculo=hiper_f,
                    anio_ultimo_dato_disponible=anio_f,
                    comentarios=com_f,
                )]
                datos_factibilidad = construir_datos_factibilidad(
                    c1_metodologia=c1,
                    c21_existencia_fuente=c21,
                    c22_disponibilidad=c22,
                    c23_periodicidad_establecida=c23,
                    c31_posee_desagregacion=c31,
                    num_desagregaciones_requeridas=req_num,
                    num_desagregaciones_disponibles=disp_num,
                    articulacion_fuentes=art,
                    armonizacion_conceptual=arm,
                    subregistro_cobertura=sub,
                    cobertura_territorial=cob,
                    estructura_datos=est,
                    variables_calculo=var,
                )
                exito, msg = guardar_indicador(
                    datos_indicador, datos_fuentes, datos_factibilidad,
                    usuario_id(),
                    campos_personalizados_indicador=campos_custom_indicador,
                    campos_personalizados_fuentes=[campos_custom_fuente],
                )
                st.success(msg) if exito else st.error(msg)
