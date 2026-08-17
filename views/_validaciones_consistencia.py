"""views/_validaciones_consistencia.py — Validaciones de consistencia entre
campos relacionados del formulario de indicadores (reglas de negocio
enviadas por Randy, agosto-2026, sobre el "formulario de consistencia").

Distinción con `_campos_vacios` (crear_indicador.py) y con
`_errores_consistencia_sin_fuente` (crear_indicador.py, ya existente):
  - `_campos_vacios` valida que un campo tenga ALGÚN valor.
  - Este módulo valida que la COMBINACIÓN de valores entre campos
    relacionados sea lógicamente consistente (p. ej.: si no hay fuente, no
    puede haber IOE = "Sí"; si el requerimiento de clasificación es "No",
    "Especificar clasificación" debe quedar vacío).

Por qué son funciones de validación en el submit y no un bloqueo dinámico
de widgets:
  Los formularios usan `st.form`, y los widgets dentro de un `st.form` no
  disparan reruns hasta que se presiona el botón de envío — "cerrar" o
  "deshabilitar" un campo en función de otro campo del MISMO form no es
  posible sin sacarlo del `st.form` (ver la nota ya existente en
  `_form_indicador_shared.py` sobre `st.dialog` dentro de `st.form`, y
  `docs/` sobre por qué el formulario de creación es un único `st.form`).
  Cada requerimiento de origen ofrecía explícitamente dos alternativas
  ("obligar a colocar X" / "otra opción sería bloquear los campos
  posteriores"); aquí se implementa la alternativa de validación explícita
  con mensaje de error, consistente con el patrón ya usado en
  `crear_indicador._errores_consistencia_sin_fuente`.

Todas las funciones son puras (sin Streamlit, sin BD) para poder probarse
con pytest normal, igual que `_campos_vacios` / `_errores_consistencia_sin_fuente`.
"""

NO_APLICA = "No aplica"
NO = "No"
SI = "Sí"

C1_METODOLOGIA_DEFINIDA = "Indicador con metodología nacional o internacional definida"
C1_NO_CUMPLE = "No cumple con los criterios anteriores"

VALOR_SIN_FUENTE = "No hay fuente"
TIPO_FUENTE_CUESTIONARIO_GLOBAL = "Cuestionario global"


# ---------------------------------------------------------------------------
# Requerimiento de clasificación (Componente de Indicador) <-> Especificar
# clasificación (Componente de Indicador) <-> Uso de Clasificaciones (C3.2,
# tab Cálculo de Factibilidad)
# ---------------------------------------------------------------------------
def errores_requerimiento_clasificacion(
    req_clas_txt: str | None, esp_clas: str | None, uso_clasificacion_txt: str | None
) -> list[str]:
    """Valida la coherencia entre "🧩 Requerimiento de clasificación",
    "Especificar clasificación" y "Uso de Clasificaciones" (C3.2).

    - Sí: exige un nombre en "Especificar clasificación" y que "Uso de
      Clasificaciones" quede en "Sí" o "No" (la fuente sí o no utiliza esa
      clasificación) — "No requerida" no es válido en este caso.
    - No: "Especificar clasificación" debe quedar vacío y "Uso de
      Clasificaciones" debe ser "No requerida".
    - No identificada: "Especificar clasificación" debe quedar vacío y "Uso
      de Clasificaciones" debe reflejar el mismo "No identificada".
    """
    esp = (esp_clas or "").strip()
    errores: list[str] = []

    if req_clas_txt == "Si":
        if not esp:
            errores.append(
                "«Especificar clasificación» es obligatorio cuando «Requerimiento "
                "de clasificación» es «Sí»."
            )
        if uso_clasificacion_txt not in (SI, NO):
            errores.append(
                "«Uso de Clasificaciones» debe ser «Sí» o «No» (la fuente debe "
                "determinar si utiliza o no esa clasificación) cuando «Requerimiento "
                f"de clasificación» es «Sí» — no es válido «{uso_clasificacion_txt}»."
            )
    elif req_clas_txt == "No":
        if esp:
            errores.append(
                "«Especificar clasificación» debe quedar vacío cuando «Requerimiento "
                "de clasificación» es «No»."
            )
        if uso_clasificacion_txt != "No requerida":
            errores.append(
                "«Uso de Clasificaciones» debe ser «No requerida» cuando "
                f"«Requerimiento de clasificación» es «No» (actualmente: «{uso_clasificacion_txt}»)."
            )
    elif req_clas_txt == "No identificada":
        if esp:
            errores.append(
                "«Especificar clasificación» debe quedar vacío cuando «Requerimiento "
                "de clasificación» es «No identificada»."
            )
        if uso_clasificacion_txt != "No identificada":
            errores.append(
                "«Uso de Clasificaciones» debe ser «No identificada» cuando "
                "«Requerimiento de clasificación» es «No identificada» "
                f"(actualmente: «{uso_clasificacion_txt}»)."
            )
    return errores


# ---------------------------------------------------------------------------
# Existencia de fuente (Componente de Fuente) -> resto de Componente de
# Fuente ("No aplica" / IOE-RA "No")
# ---------------------------------------------------------------------------
def errores_fuente_sin_fuente(
    exist_txt: str | None,
    *,
    nombre_f_txt: str | None,
    tipo_txt: str | None,
    inst_f_txt: str | None,
    per_f_txt: str | None,
    ioe_txt: str | None,
    ra_txt: str | None,
    cal_txt: str | None,
    hiper_f: str | None,
    anio_f: str | None,
    sexo_f_txt: str | None,
    edad_f_txt: str | None,
    terr_f_txt: str | None,
    disc_f_txt: str | None,
    ning_f_txt: str | None,
) -> list[str]:
    """Si "🧩 Existencia de fuente" es "No hay fuente", exige que todos los
    campos posteriores del Componente de Fuente queden en "No aplica" (IOE
    y RA en "No", como pide el requerimiento)."""
    if exist_txt != VALOR_SIN_FUENTE:
        return []

    campos_no_aplica = {
        "Nombre de la fuente": nombre_f_txt,
        "Tipo de fuente": tipo_txt,
        "Institución productora": inst_f_txt,
        "Periodicidad de la fuente": per_f_txt,
        "Calculado/Dato agregado": cal_txt,
        "Hipervínculo del último cálculo": hiper_f,
        "Año del último dato disponible": anio_f,
        "Sexo (Fuente)": sexo_f_txt,
        "Edad (Fuente)": edad_f_txt,
        "Territorio (Fuente)": terr_f_txt,
        "Discapacidad (Fuente)": disc_f_txt,
        "Nivel ingreso (Fuente)": ning_f_txt,
    }
    campos_no = {"IOE": ioe_txt, "RA": ra_txt}

    errores = []
    motivo = " Motivo: «Existencia de fuente» = «No hay fuente»."
    for etiqueta, actual in campos_no_aplica.items():
        if (actual or "").strip() != NO_APLICA:
            errores.append(f"«{etiqueta}» debe ser «No aplica» (actualmente: «{actual}»).{motivo}")
    for etiqueta, actual in campos_no.items():
        if (actual or "").strip() != NO:
            errores.append(f"«{etiqueta}» debe ser «No» (actualmente: «{actual}»).{motivo}")
    return errores


# ---------------------------------------------------------------------------
# IOE / RA <-> Tipo de fuente = "Cuestionario global"
# ---------------------------------------------------------------------------
def errores_ioe_ra_cuestionario_global(
    exist_txt: str | None, tipo_txt: str | None, ioe_txt: str | None, ra_txt: str | None
) -> list[str]:
    """Aunque exista fuente, si "🧩 Tipo de fuente" es "Cuestionario global",
    IOE y RA deben quedar en "No". (El caso "No hay fuente" ya bloquea IOE/RA
    vía `errores_fuente_sin_fuente`; no se duplica aquí.)"""
    if exist_txt == VALOR_SIN_FUENTE or tipo_txt != TIPO_FUENTE_CUESTIONARIO_GLOBAL:
        return []
    errores = []
    if (ioe_txt or "").strip() != NO:
        errores.append(
            f"«IOE» debe ser «No» cuando «Tipo de fuente» es «Cuestionario global» (actualmente: «{ioe_txt}»)."
        )
    if (ra_txt or "").strip() != NO:
        errores.append(
            f"«RA» debe ser «No» cuando «Tipo de fuente» es «Cuestionario global» (actualmente: «{ra_txt}»)."
        )
    return errores


# ---------------------------------------------------------------------------
# Método de cálculo -> Numerador / Denominador
# ---------------------------------------------------------------------------
def errores_metodo_calculo(
    metodo_txt: str | None, numerador: str | None, denominador: str | None
) -> list[str]:
    """Si "🧩 Método de cálculo" es "No" o "No aplica", Numerador y
    Denominador deben quedar vacíos."""
    if metodo_txt not in ("No", NO_APLICA):
        return []
    errores = []
    if (numerador or "").strip():
        errores.append(f"«Numerador» debe quedar vacío cuando «Método de cálculo» es «{metodo_txt}».")
    if (denominador or "").strip():
        errores.append(f"«Denominador» debe quedar vacío cuando «Método de cálculo» es «{metodo_txt}».")
    return errores


# ---------------------------------------------------------------------------
# Ficha técnica <-> C1. Existencia de Metodología
# ---------------------------------------------------------------------------
def errores_ficha_tecnica_metodologia(ficha_txt: str | None, c1_txt: str | None) -> list[str]:
    """Valida la coherencia entre "🧩 Ficha técnica" y "C1. Existencia de
    Metodología":

    - Definido: C1 debe ser exactamente "...metodología definida" (única
      opción válida).
    - Por definir: C1 no puede ser ni "...metodología definida" ni "No
      cumple con los criterios anteriores".
    - No: C1 debe ser exactamente "No cumple con los criterios anteriores".
    """
    if ficha_txt == "Definido":
        if c1_txt != C1_METODOLOGIA_DEFINIDA:
            return [
                "«C1. Existencia de Metodología» debe ser «Indicador con metodología "
                "nacional o internacional definida» (única opción válida) cuando "
                f"«Ficha técnica» es «Definido» (actualmente: «{c1_txt}»)."
            ]
    elif ficha_txt == "Por definir":
        if c1_txt in (C1_METODOLOGIA_DEFINIDA, C1_NO_CUMPLE):
            return [
                f"«C1. Existencia de Metodología» no puede ser «{c1_txt}» cuando "
                "«Ficha técnica» es «Por definir»."
            ]
    elif ficha_txt == "No":
        if c1_txt != C1_NO_CUMPLE:
            return [
                "«C1. Existencia de Metodología» debe ser «No cumple con los "
                f"criterios anteriores» cuando «Ficha técnica» es «No» (actualmente: «{c1_txt}»)."
            ]
    return []


# ---------------------------------------------------------------------------
# Desagregación: C3.1 <-> desagregaciones requeridas/disponibles (numéricas)
# ---------------------------------------------------------------------------
def errores_desagregacion(
    c31_txt: str | None, req_num: float | int | None, disp_num: float | int | None,
    exist_txt: str | None = None,
) -> list[str]:
    """Valida "C3.1 Posee desagregación requerida" contra "Desagregaciones
    requeridas por el indicador" y "Desagregaciones disponibles en la
    fuente".

    - Sí: requeridas > 0. Si además no hay fuente (`exist_txt` ==
      "No hay fuente"), disponibles debe ser 0 (dato real: no existe fuente
      de la cual tomar desagregaciones, aunque el indicador sí las requiera
      — caso descrito explícitamente en el requerimiento).
    - No: requeridas debe quedar en 0 (valor predeterminado).
    - No es requerida: ambos campos son un valor centinela = 1, salvo que
      no haya fuente, en cuyo caso disponibles vuelve a ser 0 (mismo
      criterio que el caso "Sí": el dato real prevalece sobre el
      centinela).
    """
    req_num = req_num or 0
    disp_num = disp_num if disp_num is not None else 0
    errores: list[str] = []

    if c31_txt == "Sí":
        if req_num <= 0:
            errores.append(
                "«Desagregaciones requeridas por el indicador» debe ser mayor a 0 "
                "cuando «C3.1 Posee desagregación requerida» es «Sí»."
            )
        if exist_txt == VALOR_SIN_FUENTE and disp_num != 0:
            errores.append(
                "«Desagregaciones disponibles en la fuente» debe ser 0: no hay "
                "fuente registrada de la cual tomarlas."
            )
    elif c31_txt == "No":
        if req_num != 0:
            errores.append(
                "«Desagregaciones requeridas por el indicador» debe quedar en 0 "
                "(valor predeterminado) cuando «C3.1» es «No»."
            )
    elif c31_txt == "No es requerida":
        if exist_txt == VALOR_SIN_FUENTE:
            if req_num != 1:
                errores.append(
                    "«Desagregaciones requeridas por el indicador» debe ser 1 (valor "
                    "centinela) cuando «C3.1» es «No es requerida»."
                )
            if disp_num != 0:
                errores.append(
                    "«Desagregaciones disponibles en la fuente» debe ser 0: no hay "
                    "fuente registrada de la cual tomarlas."
                )
        elif req_num != 1 or disp_num != 1:
            errores.append(
                "«Desagregaciones requeridas por el indicador» y «Desagregaciones "
                "disponibles en la fuente» deben ser 1 (valor centinela) cuando "
                "«C3.1» es «No es requerida»."
            )
    return errores
