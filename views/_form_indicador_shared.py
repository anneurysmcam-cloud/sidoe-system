"""views/_form_indicador_shared.py — Helpers compartidos entre
crear_indicador.py y actualizar_indicador.py.

Extrae la duplicación real (no cosmética) entre ambos formularios:
  - usuario_id(): idéntico en los dos archivos.
  - indice_seguro(): variante de list.index() que no lanza excepción.
  - selectbox_auxiliar(): unifica los antiguos _sb/_sb_opcional (crear) y
    _sb_edit/_sb_edit_opcional (actualizar) — mismo comportamiento, la
    única diferencia real era la preselección por id_actual, que ahora es
    un parámetro opcional.
  - campos_personalizados(): unifica _campos_personalizados (crear) y
    _campos_personalizados_edit (actualizar) — la única diferencia real
    era si hay o no un entidad_id para precargar valores guardados.
  - construir_datos_indicador() / construir_datos_factibilidad() /
    construir_datos_fuente(): arman los dicts que se envían a
    models.crud_indicadores a partir de valores ya leídos del formulario
    — mismos campos, mismo orden, en crear_indicador.py y
    actualizar_indicador.py (Hallazgo #2 del informe de revisión de
    código de agosto 2026). Funciones PURAS (sin streamlit), testeables
    de forma aislada sin AppTest.

Lo que NO se unificó a propósito, porque no es duplicación sino
comportamiento genuinamente distinto entre crear y actualizar:
  - La validación de campos obligatorios y de consistencia "sin fuente"
    (CRITERIOS_NEGATIVOS_SIN_FUENTE) solo existe en crear_indicador.py.
    actualizar_indicador.py NO la aplica: un indicador ya guardado puede
    editarse campo por campo sin volver a pasar por esa validación
    completa. Esto es una asimetría de comportamiento existente, no
    introducida por esta extracción — se documenta aquí para que quede
    visible, pero decidir si actualizar_indicador.py debería aplicar la
    misma validación es una decisión de producto, no de refactor.
  - El flujo de "Gestión de fuentes" (agregar/editar/eliminar con diálogo
    de confirmación) solo existe en actualizar_indicador.py: crear_indicador
    únicamente registra la primera fuente al dar de alta el indicador.
"""

import streamlit as st

from models.crud_auxiliares import (
    listar_categorias_personalizadas,
    obtener_valores_personalizados,
    opciones_selectbox,
)

_OPCION_NINGUNO = "— Ninguno —"


def usuario_id() -> int | None:
    """Devuelve el id del usuario autenticado en la sesión actual de Streamlit, o None si no hay sesión."""
    return (st.session_state.get("usuario") or {}).get("id")


def indice_seguro(lista: list, valor) -> int:
    """Índice seguro: devuelve 0 si el valor no está en la lista."""
    try:
        return lista.index(valor)
    except ValueError:
        return 0


def selectbox_auxiliar(
    clave: str, label: str, id_actual=None, opcional: bool = False, **kwargs
) -> tuple:
    """Selectbox respaldado por Auxiliares. Devuelve (texto_seleccionado, id).

    - id_actual=None (default): sin preselección — comportamiento del
      antiguo _sb/_sb_opcional en "crear".
    - id_actual=<id>: preselecciona ese valor por ID (funciona aunque el
      texto se haya renombrado en Auxiliares) — comportamiento del
      antiguo _sb_edit/_sb_edit_opcional en "actualizar".
    - opcional=True: agrega "— Ninguno —" al inicio; devuelve (None, None)
      si se elige esa opción.
    """
    textos, mapa = opciones_selectbox(clave)
    opciones = [_OPCION_NINGUNO, *textos] if opcional else textos
    if id_actual is not None:
        mapa_inv = {v: k for k, v in mapa.items()}
        texto_actual = mapa_inv.get(id_actual)
        texto = st.selectbox(label, opciones, index=indice_seguro(opciones, texto_actual), **kwargs)
    else:
        texto = st.selectbox(label, opciones, **kwargs)
    if opcional and texto == _OPCION_NINGUNO:
        return None, None
    return texto, mapa.get(texto)


def campos_personalizados(
    componente: str, key_prefix: str, entidad_id: int | None = None, opcional: bool = False,
) -> tuple[dict, dict]:
    """Renderiza un selectbox por cada categoría personalizada del componente.

    Si `entidad_id` se provee, precarga los valores ya guardados para esa
    entidad (comportamiento de "actualizar"); si no, todos los selectbox
    arrancan sin preselección (comportamiento de "crear").

    `opcional` controla si aparece la opción "— Ninguno —":
      - crear_indicador.py llama con opcional=False (default): un valor
        siempre queda seleccionado, consistente con que estos campos se
        traten como obligatorios en `_campos_vacios`.
      - actualizar_indicador.py llama con opcional=True: permite dejar un
        campo personalizado sin valor al editar (comportamiento previo de
        _campos_personalizados_edit / _sb_edit_opcional).

    Devuelve (valores, etiquetas):
      - valores: {categoria_id: valor_id}
      - etiquetas: {"🧩 nombre_visible": valor_id} — para validación de
        campos obligatorios en crear_indicador.py (actualizar_indicador.py
        puede descartar este segundo valor, no valida obligatoriedad).
    """
    categorias = listar_categorias_personalizadas(aplica_a=componente)
    if not categorias:
        return {}, {}
    actuales = obtener_valores_personalizados(componente, entidad_id) if entidad_id else {}
    st.markdown("**Campos personalizados** 🧩 *(catálogo agregado en Auxiliares)*")
    valores = {}
    etiquetas = {}
    cols = st.columns(min(len(categorias), 4)) if len(categorias) > 1 else [st.container()]
    for i, cat in enumerate(categorias):
        with cols[i % len(cols)]:
            _texto, valor_id = selectbox_auxiliar(
                cat["clave"], f"🧩 {cat['nombre_visible']}",
                id_actual=actuales.get(cat["id"]), opcional=opcional,
                key=f"{key_prefix}_{cat['id']}",
            )
        valores[cat["id"]] = valor_id
        etiquetas[f"🧩 {cat['nombre_visible']}"] = valor_id
    return valores, etiquetas


# ---------------------------------------------------------------------------
# Construcción de payloads (Hallazgo #2 del informe de revisión de código de
# agosto 2026): lógica PURA (sin streamlit) que arma los dicts que se envían
# a models.crud_indicadores a partir de valores ya leídos del formulario.
# Idénticos en crear_indicador.py y actualizar_indicador.py -- esto era
# duplicación real, no cosmética, ahora extraída acá. Cada función es
# testeable de forma aislada sin necesidad de streamlit.testing.v1.AppTest.
# ---------------------------------------------------------------------------

def construir_datos_indicador(
    *,
    codigo: str,
    estado_indicador: str,
    estado_publicacion: str,
    referencias_manuales: list[str],
    ejes_politicas_extra: list[tuple],
    eje_id: int | None,
    politica_gobierno_id: int | None,
    generador_demanda_id: int | None,
    indicador: str,
    dominio_actividad_estadistica_id: int | None,
    subdominio_actividad_estadistica_id: int | None,
    area_misional_one_id: int | None,
    sector_ioe_id: int | None,
    metodo_calculo_id: int | None,
    ficha_tecnica_id: int | None,
    numerador: str,
    denominador: str,
    unidad_medida: str,
    requerimiento_clasificacion_id: int | None,
    especificar_clasificacion: str,
    sexo_id: int | None,
    edad_id: int | None,
    territorio_id: int | None,
    discapacidad_id: int | None,
    nivel_ingreso_id: int | None,
    periodicidad_indicador_id: int | None,
    ente_responsable_metodologia: str,
    alcance_metodologico_id: int | None,
) -> dict:
    """Arma el dict ``datos_indicador`` (payload para
    ``models.crud_indicadores.guardar_indicador``/``modificar_indicador``)
    a partir de los valores ya leídos del formulario principal. Todos los
    parámetros son *keyword-only* para que cada llamador sea explícito
    sobre qué valor de formulario va a qué campo de BD (el orden de ~28
    parámetros posicionales sería un riesgo de bug silencioso).
    """
    return {
        "codigo": codigo,
        "estado_indicador": estado_indicador,
        "estado_publicacion": estado_publicacion,
        "_referencias_manuales": referencias_manuales,
        "_ejes_politicas_extra": ejes_politicas_extra,
        "eje_id": eje_id, "politica_gobierno_id": politica_gobierno_id,
        "generador_demanda_id": generador_demanda_id,
        "indicador": indicador,
        "dominio_actividad_estadistica_id": dominio_actividad_estadistica_id,
        "subdominio_actividad_estadistica_id": subdominio_actividad_estadistica_id,
        "area_misional_one_id": area_misional_one_id,
        "sector_ioe_id": sector_ioe_id,
        "metodo_calculo_id": metodo_calculo_id, "ficha_tecnica_id": ficha_tecnica_id,
        "numerador": numerador, "denominador": denominador,
        "unidad_medida": unidad_medida,
        "requerimiento_clasificacion_id": requerimiento_clasificacion_id,
        "especificar_clasificacion": especificar_clasificacion,
        "sexo_id": sexo_id, "edad_id": edad_id,
        "territorio_id": territorio_id, "discapacidad_id": discapacidad_id,
        "nivel_ingreso_id": nivel_ingreso_id,
        "periodicidad_indicador_id": periodicidad_indicador_id,
        "ente_responsable_metodologia": ente_responsable_metodologia,
        "alcance_metodologico_id": alcance_metodologico_id,
    }


def construir_datos_factibilidad(
    *,
    c1_metodologia: str,
    c21_existencia_fuente: str,
    c22_disponibilidad: str,
    c23_periodicidad_establecida: str,
    c31_posee_desagregacion: str,
    num_desagregaciones_requeridas: int,
    num_desagregaciones_disponibles: int,
    articulacion_fuentes: str,
    armonizacion_conceptual: str,
    subregistro_cobertura: str,
    cobertura_territorial: str,
    estructura_datos: str,
    variables_calculo: str,
) -> dict:
    """Arma el dict ``datos_factibilidad`` (payload para el Engine vía
    ``guardar_indicador``/``modificar_indicador``) a partir de los valores
    ya leídos del formulario "Cálculo de Factibilidad". Idéntico en
    crear_indicador.py y actualizar_indicador.py.
    """
    return {
        "c1_metodologia": c1_metodologia,
        "c21_existencia_fuente": c21_existencia_fuente,
        "c22_disponibilidad": c22_disponibilidad,
        "c23_periodicidad_establecida": c23_periodicidad_establecida,
        "c31_posee_desagregacion": c31_posee_desagregacion,
        "num_desagregaciones_requeridas": num_desagregaciones_requeridas,
        "num_desagregaciones_disponibles": num_desagregaciones_disponibles,
        "articulacion_fuentes": articulacion_fuentes,
        "armonizacion_conceptual": armonizacion_conceptual,
        "subregistro_cobertura": subregistro_cobertura,
        "cobertura_territorial": cobertura_territorial,
        "estructura_datos": estructura_datos,
        "variables_calculo": variables_calculo,
    }


def construir_datos_fuente(
    *,
    existencia_fuente_id: int | None,
    nombre_fuente_id: int | None,
    tipo_fuente_id: int | None,
    institucion_productora_id: int | None,
    periodicidad_id: int | None,
    sexo_id: int | None,
    edad_id: int | None,
    territorio_id: int | None,
    discapacidad_id: int | None,
    nivel_ingreso_socioeconomico_id: int | None,
    ioe_id: int | None,
    ra_id: int | None,
    calculado_datos_agregados_id: int | None,
    hipervinculo_ultimo_calculo: str,
    anio_ultimo_dato_disponible: str,
    comentarios: str,
) -> dict:
    """Arma el dict de columnas de ``fuentes_indicador`` (payload para
    ``agregar_fuente``/``actualizar_fuente``, o el primer elemento de
    ``datos_fuentes`` en ``guardar_indicador``) a partir de los valores ya
    leídos del formulario de fuente. Misma estructura en los tres
    formularios de fuente del proyecto (alta en crear_indicador.py, alta y
    edición en actualizar_indicador.py).
    """
    return {
        "existencia_fuente_id": existencia_fuente_id,
        "nombre_fuente_id": nombre_fuente_id,
        "tipo_fuente_id": tipo_fuente_id,
        "institucion_productora_id": institucion_productora_id,
        "periodicidad_id": periodicidad_id,
        "sexo_id": sexo_id, "edad_id": edad_id,
        "territorio_id": territorio_id, "discapacidad_id": discapacidad_id,
        "nivel_ingreso_socioeconomico_id": nivel_ingreso_socioeconomico_id,
        "ioe_id": ioe_id, "ra_id": ra_id,
        "calculado_datos_agregados_id": calculado_datos_agregados_id,
        "hipervinculo_ultimo_calculo": hipervinculo_ultimo_calculo,
        "anio_ultimo_dato_disponible": anio_ultimo_dato_disponible,
        "comentarios": comentarios,
    }
