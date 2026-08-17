"""
models/revision_pendiente.py
=============================
Soporte para el flujo de aprobación del rol supervisor (ver
``views/aprobar_indicadores.py``). No es una tabla nueva del modelo de
negocio (los datos siguen viviendo únicamente en ``indicadores``,
``fuentes_indicador`` y ``calculo_factibilidad`` — ver arquitectura
confirmada) sino tres columnas de metadatos de proceso en ``indicadores``
(``revision_tipo``, ``revision_detalle``, ``revision_fecha``, agregadas por
``data.database.migrar_revision_pendiente``) que responden a dos preguntas
que antes obligaban al supervisor a abrir "Actualizar Indicador" y comparar
a ojo:

1. ¿Este indicador en borrador es contenido nuevo o una edición de algo que
   el público ya veía? -> ``revision_tipo`` ('nuevo' / 'actualizado').
2. Si es una edición, ¿qué campos exactos cambiaron? -> ``revision_detalle``
   (JSON con una lista de ``{"campo", "anterior", "nuevo"}``).

Clasificación de 'nuevo' vs 'actualizado'
------------------------------------------
Se decide en el momento de marcar el borrador (``marcar_pendiente_revision``)
mirando el estado *actual* del indicador en BD (antes de esta edición):

- Si ``estado_publicacion`` actual es 'publicado' -> el público ya lo veía,
  así que esto es una actualización: 'actualizado'.
- Si ya había una revisión pendiente sin aprobar (varias ediciones seguidas
  antes de que el supervisor llegue a revisar) -> se conserva la
  clasificación que ya tenía, no se recalcula desde cero.
- Si no hay nada de lo anterior -> es la primera vez que existe, 'nuevo'.

Esto evita el caso borde de que dos ediciones consecutivas de un indicador
nuevo (todavía sin aprobar la primera vez) lo etiqueten incorrectamente como
'actualizado' solo porque ya estaba en estado 'borrador'.

Acumulación de cambios
-----------------------
Si el indicador ya tenía cambios pendientes sin aprobar y se edita de
nuevo antes de que el supervisor lo revise, los cambios se fusionan por
campo: se conserva el valor "anterior" más antiguo disponible (el que
tenía el indicador la última vez que estuvo publicado o recién creado) y
se actualiza el valor "nuevo" al más reciente — así el supervisor ve el
cambio completo de punta a punta en una sola revisión, no un historial
fragmentado de cada guardado intermedio.
"""

from __future__ import annotations

import json

from config import CAMPOS_HIBRIDOS_FUENTES, CAMPOS_HIBRIDOS_INDICADORES
from models.crud_auxiliares import resolver_texto

# ---------------------------------------------------------------------------
# Etiquetas legibles por campo (para no mostrarle al supervisor nombres de
# columna crudos como "c22_disponibilidad" o "sector_ioe_id").
# ---------------------------------------------------------------------------

ETIQUETAS_INDICADOR: dict[str, str] = {
    "codigo": "Código",
    "indicador": "Nombre del indicador",
    "estado_indicador": "Estado del indicador",
    "numerador": "Numerador",
    "denominador": "Denominador",
    "unidad_medida": "Unidad de medida",
    "especificar_clasificacion": "Especificar clasificación",
    "ente_responsable_metodologia": "Ente responsable de la metodología",
    "indicadores_duplicados": "Indicadores referenciados",
    **{
        f"{columna}_id": nombre
        for columna, _clave, nombre, _valores in CAMPOS_HIBRIDOS_INDICADORES
    },
}

ETIQUETAS_FACTIBILIDAD: dict[str, str] = {
    "c1_metodologia": "C1. Metodología",
    "c21_existencia_fuente": "C2.1 Existencia de fuente",
    "c22_disponibilidad": "C2.2 Disponibilidad/accesibilidad",
    "c23_periodicidad_establecida": "C2.3 Periodicidad",
    "c31_posee_desagregacion": "C3.1 Desagregación",
    "num_desagregaciones_requeridas": "Desagregaciones requeridas",
    "num_desagregaciones_disponibles": "Desagregaciones disponibles",
    "articulacion_fuentes": "Articulación de fuentes",
    "armonizacion_conceptual": "Armonización conceptual",
    "subregistro_cobertura": "Subregistro / cobertura",
    "cobertura_territorial": "Cobertura territorial",
    "estructura_datos": "Estructura de datos",
    "variables_calculo": "Uso de clasificaciones",
}

ETIQUETAS_FUENTE: dict[str, str] = {
    "hipervinculo_ultimo_calculo": "Hipervínculo al último cálculo",
    "anio_ultimo_dato_disponible": "Año del último dato disponible",
    "comentarios": "Comentarios",
    **{
        f"{columna}_id": nombre
        for columna, _clave, nombre, _valores in CAMPOS_HIBRIDOS_FUENTES
    },
}


def _etiqueta(campo: str, mapa: dict[str, str]) -> str:
    return mapa.get(campo, campo.removesuffix("_id").replace("_", " ").capitalize())


def _resolver_valor(campo: str, valor) -> str:
    """Convierte un valor crudo de columna en algo legible: resuelve IDs de
    Auxiliares a su texto, y usa '—' para vacíos."""
    if valor is None or valor == "":
        return "—"
    if campo.endswith("_id"):
        texto = resolver_texto(valor)
        return texto if texto else f"(id={valor})"
    return str(valor)


def formatear_pares_ejes_politicas(pares: list[tuple] | None) -> str:
    """Formatea los pares (eje_id, politica_id) *adicionales* de un
    indicador (ver models.crud_indicadores.obtener_ejes_politicas_extra) a
    texto legible para el resumen de cambios del supervisor.

    Se ordena el resultado (no el orden de inserción) para que reordenar el
    mismo conjunto de pares en el formulario no se reporte como un cambio
    de contenido cuando en realidad son los mismos ejes/políticas.
    """
    if not pares:
        return "—"
    partes = [
        f"{resolver_texto(eje_id) or '(eje no identificado)'} — "
        f"{resolver_texto(politica_id) or '(política no identificada)'}"
        for eje_id, politica_id in pares
    ]
    return "; ".join(sorted(partes))


def calcular_diferencias(
    anterior: dict, nuevo: dict, mapa_etiquetas: dict[str, str], prefijo: str = ""
) -> list[dict]:
    """Compara *anterior* vs *nuevo* campo por campo (solo las claves
    presentes en *nuevo*) y devuelve la lista de las que realmente
    cambiaron, con etiqueta legible y valores resueltos."""
    cambios = []
    for campo, valor_nuevo in nuevo.items():
        if campo.startswith("_"):
            continue
        valor_anterior = anterior.get(campo) if anterior else None
        if valor_anterior == valor_nuevo:
            continue
        cambios.append({
            "campo": f"{prefijo}{_etiqueta(campo, mapa_etiquetas)}",
            "anterior": _resolver_valor(campo, valor_anterior),
            "nuevo": _resolver_valor(campo, valor_nuevo),
        })
    return cambios


_SIN_PROVEER = object()


def marcar_pendiente_revision(
    cursor,
    indicador_id: int,
    cambios_nuevos: list[dict],
    estado_publicacion_previo=_SIN_PROVEER,
) -> None:
    """Pone el indicador en 'borrador' y guarda/fusiona el resumen de
    cambios para que Aprobar Indicadores lo muestre. Ver docstring del
    módulo para la lógica de clasificación 'nuevo' vs 'actualizado' y de
    fusión de cambios acumulados.

    ``estado_publicacion_previo``: override explícito del estado de
    publicación ANTERIOR a esta edición, usado para decidir 'nuevo' vs
    'actualizado'. Necesario porque ``modificar_indicador()`` ya ejecuta el
    UPDATE que fuerza ``estado_publicacion`` a 'borrador' ANTES de llamar
    a esta función (para poder incluirlo en el mismo UPDATE que el resto
    de los campos editados) — si no se provee este valor, la consulta de
    abajo leería el estado *ya sobrescrito* ('borrador') en vez del que
    tenía el indicador antes de esta edición, y JAMÁS clasificaría nada
    como 'actualizado' salvo que ya viniera arrastrando esa clasificación
    de una revisión pendiente previa (bug reportado por Randy: ediciones
    de indicadores ya publicados no se detectaban como edición). El resto
    de los llamadores (agregar_fuente, actualizar_fuente, eliminar_fuente,
    guardar_indicador) no tocan ``indicadores.estado_publicacion`` antes de
    llamar aquí, así que para ellos el valor en BD sigue siendo el correcto
    y no necesitan pasar este parámetro.
    """
    fila = cursor.execute(
        "SELECT estado_publicacion, revision_tipo, revision_detalle "
        "FROM indicadores WHERE id = ?",
        (indicador_id,),
    ).fetchone()
    if not fila:
        return
    estado_actual, tipo_previo, detalle_previo = fila
    if estado_publicacion_previo is not _SIN_PROVEER:
        estado_actual = estado_publicacion_previo

    if estado_actual == "publicado":
        tipo = "actualizado"
    elif tipo_previo:
        tipo = tipo_previo
    else:
        tipo = "nuevo"

    acumulado_por_campo: dict[str, dict] = {}
    if detalle_previo:
        try:
            for cambio in json.loads(detalle_previo):
                acumulado_por_campo[cambio["campo"]] = cambio
        except (TypeError, ValueError, KeyError):
            acumulado_por_campo = {}

    for cambio in cambios_nuevos:
        existente = acumulado_por_campo.get(cambio["campo"])
        anterior = existente["anterior"] if existente else cambio["anterior"]
        acumulado_por_campo[cambio["campo"]] = {
            "campo": cambio["campo"], "anterior": anterior, "nuevo": cambio["nuevo"],
        }

    # Un campo que terminó con el mismo valor que tenía originalmente (p.
    # ej. lo cambiaron y luego lo devolvieron a como estaba) no aporta nada
    # a la revisión — se descarta para no hacerle perder tiempo al supervisor.
    cambios_finales = [
        c for c in acumulado_por_campo.values() if c["anterior"] != c["nuevo"]
    ]

    cursor.execute(
        "UPDATE indicadores SET estado_publicacion = 'borrador', revision_tipo = ?, "
        "revision_detalle = ?, revision_fecha = datetime('now') WHERE id = ?",
        (
            tipo,
            json.dumps(cambios_finales, ensure_ascii=False) if cambios_finales else None,
            indicador_id,
        ),
    )


def limpiar_revision_pendiente(cursor, indicador_id: int) -> None:
    """Limpia los metadatos de revisión al aprobar: ya no hay nada
    pendiente que mostrarle al supervisor para este indicador."""
    cursor.execute(
        "UPDATE indicadores SET revision_tipo = NULL, revision_detalle = NULL, "
        "revision_fecha = NULL WHERE id = ?",
        (indicador_id,),
    )


def leer_cambios(revision_detalle: str | None) -> list[dict]:
    """Parsea el JSON de ``revision_detalle`` de forma defensiva (nunca
    revienta la vista si el contenido es inválido o antiguo)."""
    if not revision_detalle:
        return []
    try:
        datos = json.loads(revision_detalle)
        return datos if isinstance(datos, list) else []
    except (TypeError, ValueError):
        return []
