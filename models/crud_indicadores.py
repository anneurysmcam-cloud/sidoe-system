"""
models/crud_indicadores.py
==========================
CRUD completo para indicadores, fuentes y factibilidad.

Cada operación de escritura (crear, modificar, eliminar) se ejecuta en una
única transacción de base de datos que incluye el registro de auditoría;
si cualquier paso falla, todo el cambio se revierte.

INVARIANTE DE SEGURIDAD (SQL):
Las funciones de INSERT/UPDATE de este módulo arman las columnas de la
sentencia SQL interpolando ``.keys()`` de los diccionarios ``datos_indicador``
/ ``datos_fuente`` / ``f`` (SQLite no permite parametrizar identificadores).
Los *valores* siempre viajan parametrizados con ``?``; nunca se concatenan
directamente. Esto es seguro únicamente porque esas claves se originan
siempre en literales de código fijos (definidos en ``views/crear_indicador.py``
y ``views/actualizar_indicador.py``), nunca en input libre del usuario ni en
estructuras construidas dinámicamente (JSON externo, claves de
``st.session_state``, etc.). Si en el futuro estos diccionarios llegan a
construirse a partir de claves no controladas por el propio código, este
patrón deja de ser seguro y debe reemplazarse por una lista blanca explícita
de columnas permitidas.

Funciones expuestas
-------------------
- obtener_indicadores_para_referencia
- obtener_indicador_por_id
- obtener_ejes_politicas_extra
- guardar_indicador
- modificar_indicador
- borrar_indicador
- aprobar_publicacion_indicador
- agregar_fuente
- actualizar_fuente
- eliminar_fuente
- sincronizar_indicadores_referenciados
- sincronizar_contenido_referenciados
"""

import logging
import sqlite3

from config import CAMPOS_HIBRIDOS_FUENTES, CAMPOS_HIBRIDOS_INDICADORES, UMBRAL_ELIMINACIONES_AUTOBLOQUEO
from data import database as db_mod
from features.engine_factibilidad import calcular_reglas_factibilidad
from models.crud_auxiliares import guardar_campos_personalizados, resolver_texto
from models.logs import registrar_log
from models.revision_pendiente import (
    ETIQUETAS_FACTIBILIDAD,
    ETIQUETAS_FUENTE,
    ETIQUETAS_INDICADOR,
    calcular_diferencias,
    formatear_pares_ejes_politicas,
    limpiar_revision_pendiente,
    marcar_pendiente_revision,
)
from utils.helpers import normalizar_titulo_indicador

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Listas blancas de columnas permitidas (defensa en profundidad)
# ---------------------------------------------------------------------------
# Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo. Reflejan el
# esquema real de cada tabla (ver data/database.py) y actúan como última
# barrera: aunque hoy las claves de los diccionarios de entrada siempre se
# originan en literales de código, si en el futuro llegaran a construirse a
# partir de input no controlado, cualquier columna fuera de esta lista
# provoca un ValueError explícito en vez de ejecutarse como SQL.
#
# Modelo híbrido: los campos declarados en CAMPOS_HIBRIDOS_INDICADORES /
# CAMPOS_HIBRIDOS_FUENTES (config.py) se escriben como ``<columna>_id`` (FK a
# auxiliares_valores), no con su nombre de columna base. Las whitelists se
# derivan de config.py para no desincronizarse si esas listas cambian.

_COLUMNAS_BASE_INDICADORES = frozenset({
    "eje", "politica_gobierno", "generador_demanda", "codigo", "estado_indicador",
    "estado_publicacion",
    "indicadores_duplicados", "indicador", "titulo_normalizado",
    "dominio_actividad_estadistica",
    "subdominio_actividad_estadistica", "area_misional_one", "sector_ioe",
    "requerimiento_clasificacion", "especificar_clasificacion", "metodo_calculo",
    "ficha_tecnica", "numerador", "denominador", "unidad_medida", "sexo", "edad",
    "territorio", "discapacidad", "nivel_ingreso", "periodicidad_indicador",
    "ente_responsable_metodologia", "alcance_metodologico",
})

COLUMNAS_INDICADORES = _COLUMNAS_BASE_INDICADORES | {
    f"{columna}_id" for columna, _clave, _nombre, _valores in CAMPOS_HIBRIDOS_INDICADORES
}

_COLUMNAS_BASE_FUENTES_INDICADOR = frozenset({
    "indicador_id", "existencia_fuente", "nombre_fuente", "tipo_fuente",
    "institucion_productora", "periodicidad", "sexo", "edad", "territorio",
    "discapacidad", "nivel_ingreso_socioeconomico", "ioe", "ra",
    "calculado_datos_agregados", "hipervinculo_ultimo_calculo",
    "anio_ultimo_dato_disponible", "comentarios",
})

COLUMNAS_FUENTES_INDICADOR = _COLUMNAS_BASE_FUENTES_INDICADOR | {
    f"{columna}_id" for columna, _clave, _nombre, _valores in CAMPOS_HIBRIDOS_FUENTES
}

COLUMNAS_CALCULO_FACTIBILIDAD = frozenset({
    "indicador_id", "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
    "c23_periodicidad_establecida", "c31_posee_desagregacion",
    "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
    "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
    "cobertura_territorial", "estructura_datos", "variables_calculo",
    "c1_valor", "c21_valor", "c22_valor", "c23_valor", "c31_valor", "c32_valor",
    "articulacion_valor", "armonizacion_valor", "subregistro_valor",
    "cobertura_valor", "estructura_valor", "variables_valor",
    "score_factibilidad_final", "categoria_factibilidad", "calc_timestamp",
})


def _resumen_fuente(datos_fuente: dict) -> str:
    """Etiqueta corta y legible de una fuente para el resumen de cambios
    del supervisor (ver models/revision_pendiente.py): resuelve
    nombre_fuente_id/tipo_fuente_id a texto cuando están presentes; si no,
    cae de vuelta a lo que haya disponible."""
    nombre = None
    if datos_fuente.get("nombre_fuente_id"):
        nombre = resolver_texto(datos_fuente["nombre_fuente_id"])
    tipo = None
    if datos_fuente.get("tipo_fuente_id"):
        tipo = resolver_texto(datos_fuente["tipo_fuente_id"])
    partes = [p for p in (nombre, tipo) if p]
    return " — ".join(partes) if partes else "(sin nombre de fuente)"


def _validar_columnas(claves, columnas_permitidas: frozenset, tabla: str) -> None:
    """Verifica que ``claves`` sea subconjunto de la whitelist de ``tabla``.

    Última barrera antes de interpolar nombres de columna en SQL (ver
    INVARIANTE DE SEGURIDAD arriba). Lanza ``ValueError`` ante cualquier
    columna no reconocida en vez de ejecutar SQL con identificadores
    arbitrarios.
    """
    invalidas = set(claves) - columnas_permitidas
    if invalidas:
        raise ValueError(
            f"Columnas no permitidas para la tabla '{tabla}': {sorted(invalidas)}"
        )


def _mensaje_error_bd(exc: sqlite3.Error) -> str:
    """Mensaje para el usuario final ante un error esperable de la base de
    datos (violación de constraint, fila bloqueada, etc.) — distinto del
    mensaje ante un bug real de programación (ver ``_mensaje_error_inesperado``).

    El detalle técnico de ``exc`` sí se conserva porque puede orientar a
    "los muchachos" (QA) sobre qué dato revisar, pero se enmarca como un
    problema de datos, no como una falla del sistema (Hallazgo #8 del
    informe de revisión de código de agosto 2026).
    """
    return (
        "No se pudo completar la operación porque los datos violan una "
        "restricción de la base de datos (por ejemplo, un valor duplicado "
        "o una referencia inválida). Revisa los campos relacionados. Si el "
        f"problema persiste, contacta al administrador con este detalle: {exc}"
    )


def _mensaje_error_inesperado(exc: Exception) -> str:
    """Mensaje para el usuario final ante una excepción que NO es un error
    esperable de la base de datos (``sqlite3.Error``), es decir, un
    probable bug de programación (``TypeError``, ``KeyError``, etc.). No
    expone ``str(exc)`` crudo al usuario de negocio -- el detalle técnico
    completo ya queda en el log vía ``logger.exception()`` (Hallazgo #8 del
    informe de revisión de código de agosto 2026).
    """
    return (
        "Ocurrió un error inesperado al procesar la operación. El equipo "
        "técnico ya cuenta con el detalle en los registros del sistema; si "
        "el problema persiste, contacta al administrador."
    )


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _sugerir_referencias_automaticas(
    cursor, nombre: str, generador_demanda_id: int | None, excluir_id: int | None = None
) -> list[tuple[int, str]]:
    """Detecta indicadores con título idéntico (normalizado) pero distinto
    Generador de demanda — el MISMO indicador apareciendo bajo otra fuente.
    Devuelve [(id, codigo), ...].

    Perf: resuelve por WHERE indexado sobre `indicadores.titulo_normalizado`
    en vez de traer toda la tabla a Python y comparar título por título
    (ver Hallazgo 2 del informe de rendimiento de agosto 2026). La columna
    se mantiene sincronizada con `indicador` en cada INSERT/UPDATE (ver
    guardar_indicador/modificar_indicador) y tiene un índice creado en
    data/database.py::crear_indices().
    """
    objetivo = normalizar_titulo_indicador(nombre)
    if not objetivo:
        return []
    candidatos = []
    for row_id, codigo, gen_id in cursor.execute(
        "SELECT id, codigo, generador_demanda_id FROM indicadores "
        "WHERE titulo_normalizado = ? AND id != ?",
        (objetivo, excluir_id if excluir_id is not None else -1),
    ).fetchall():
        if gen_id != generador_demanda_id:
            candidatos.append((row_id, codigo))
    return candidatos


def sincronizar_indicadores_referenciados(
    cursor,
    indicador_id: int,
    codigo: str,
    nombre: str,
    generador_demanda_id: int | None,
    codigos_manuales: list[str],
) -> str:
    """Combina referencias manuales y automáticas, guarda en indicadores_duplicados
    y sincroniza el vínculo de forma bidireccional. Devuelve el texto guardado.
    """
    auto = _sugerir_referencias_automaticas(cursor, nombre, generador_demanda_id, excluir_id=indicador_id)
    codigos_auto = [c for _id, c in auto]
    codigos_manuales_limpios = [c.strip() for c in (codigos_manuales or []) if c and c.strip()]
    todos = sorted(set(codigos_manuales_limpios + codigos_auto))
    texto = ", ".join(todos)
    cursor.execute(
        "UPDATE indicadores SET indicadores_duplicados = ? WHERE id = ?",
        (texto, indicador_id),
    )
    # Actualización bidireccional: el otro lado del vínculo también debe
    # apuntar acá — tanto para los detectados automáticamente (mismo
    # título) como para los seleccionados a mano (títulos distintos, que
    # es justo el caso de uso real del selector manual: si el título
    # coincidiera, ya habría salido como sugerencia automática).
    #
    # Perf: una sola consulta con IN en vez de un SELECT por código dentro
    # del loop (N round-trips -> 1). Ver Hallazgo 3 del informe de
    # rendimiento de agosto 2026.
    if todos:
        placeholders = ", ".join(["?"] * len(todos))
        filas_por_codigo = {
            fila_codigo: (fila_id, fila_dup)
            for fila_codigo, fila_id, fila_dup in cursor.execute(
                f"SELECT codigo, id, indicadores_duplicados FROM indicadores "
                f"WHERE codigo IN ({placeholders})",
                todos,
            ).fetchall()
        }
        for cod_otro in todos:
            fila = filas_por_codigo.get(cod_otro)
            if not fila:
                continue
            otro_id, actual = fila[0], (fila[1] or "")
            if otro_id == indicador_id:
                continue
            lista = {x.strip() for x in actual.split(",") if x.strip()}
            lista.add(codigo)
            cursor.execute(
                "UPDATE indicadores SET indicadores_duplicados = ? WHERE id = ?",
                (", ".join(sorted(lista)), otro_id),
            )
    return texto


# Los 13 criterios crudos que el Engine (features/engine_factibilidad.py) toma
# como input para calcular C1-C3.2. Se excluyen deliberadamente de
# CAMPOS_HIBRIDOS_INDICADORES/FUENTES (ver docstring de config.py): el Engine
# hace matching exacto contra el vocabulario oficial del Excel, así que estos
# valores nunca deben pasar por Auxiliares. Por el mismo motivo, al propagar
# contenido entre indicadores referenciados se copian estos 13 campos tal
# cual y SIEMPRE se recalculan c1_valor…score_factibilidad_final con el
# Engine en destino — nunca se copia el score directamente.
_CAMPOS_CRITERIO_FACTIBILIDAD = (
    "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
    "c23_periodicidad_establecida", "c31_posee_desagregacion",
    "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
    "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
    "cobertura_territorial", "estructura_datos", "variables_calculo",
)

# Campos de indicadores que NO se propagan entre referenciados aunque estén
# en COLUMNAS_INDICADORES (pedido de Randy 2026-07-26):
#   - codigo / generador_demanda(_id): identifican la fila y el motivo mismo
#     de que existan varias filas referenciadas entre sí (cada una puede
#     pertenecer a un generador de demanda distinto). Copiarlos rompería
#     la relación.
#   - estado_indicador / estado_publicacion: visibilidad por fila (interna y
#     pública respectivamente), decisión editorial individual — igual que
#     fuente/factibilidad NO propagan estos dos (ver docstring de
#     sincronizar_contenido_referenciados).
#   - indicadores_duplicados: es el vínculo en sí, lo gestiona
#     sincronizar_indicadores_referenciados(), no este flujo.
#   - indicador (nombre/título): excluido por pedido de la jefa de Randy en
#     ONE (2026-07-27). Dos indicadores referenciados pueden compartir
#     fuente y tratamiento metodológico sin llamarse igual — nombres
#     parecidos pero no idénticos siguen llevando el mismo tratamiento, así
#     que forzar el mismo texto sobrescribiría un título válido y distinto.
_CAMPOS_EXCLUIDOS_SYNC_DESCRIPCION = frozenset({
    "codigo", "generador_demanda", "generador_demanda_id",
    "estado_indicador", "estado_publicacion", "indicadores_duplicados",
    "indicador",
})

_CAMPOS_DESCRIPCION_INDICADOR = tuple(sorted(
    COLUMNAS_INDICADORES - _CAMPOS_EXCLUIDOS_SYNC_DESCRIPCION
))


def _reemplazar_fuentes_destino(
    cursor, id_dest: int, fuentes_origen: list, columnas_fuente: list[str]
) -> None:
    """Reemplaza por completo las fuentes de ``id_dest`` con una copia exacta
    de ``fuentes_origen`` (incluyendo sus campos personalizados).

    Bloque extraído de ``sincronizar_contenido_referenciados()`` (Hallazgo #3
    del informe de revisión de código de agosto 2026).
    """
    ids_fuentes_viejas = [
        r[0] for r in cursor.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id = ?", (id_dest,)
        ).fetchall()
    ]
    for id_fuente_vieja in ids_fuentes_viejas:
        cursor.execute(
            "DELETE FROM fuentes_indicador WHERE id = ?", (id_fuente_vieja,)
        )  # ON DELETE CASCADE limpia fuente_campos_personalizados asociados

    for fuente in fuentes_origen:
        datos_f = dict(zip(columnas_fuente, fuente, strict=True))
        id_fuente_origen = datos_f.pop("id")
        datos_f["indicador_id"] = id_dest
        cursor.execute(
            f"INSERT INTO fuentes_indicador ({', '.join(datos_f.keys())}) "
            f"VALUES ({', '.join(['?'] * len(datos_f))})",
            list(datos_f.values()),
        )
        id_fuente_nueva = cursor.lastrowid
        personalizados = {
            categoria_id: valor_id
            for categoria_id, valor_id in cursor.execute(
                "SELECT categoria_id, valor_id FROM fuente_campos_personalizados "
                "WHERE fuente_id = ?",
                (id_fuente_origen,),
            ).fetchall()
        }
        guardar_campos_personalizados(
            "fuente", id_fuente_nueva, personalizados, cursor=cursor
        )


def _recalcular_factibilidad_destino(
    cursor, id_dest: int, criterios_origen: dict | None
) -> None:
    """Copia los criterios crudos C1-C3.2 de ``criterios_origen`` hacia
    ``id_dest`` y SIEMPRE recalcula el score con el Engine en destino (nunca
    se copia el score directamente).

    Bloque extraído de ``sincronizar_contenido_referenciados()`` (Hallazgo #3
    del informe de revisión de código de agosto 2026).
    """
    if criterios_origen is None:
        return
    resultado = calcular_reglas_factibilidad(criterios_origen)
    resultado["indicador_id"] = id_dest
    cols = ", ".join(resultado.keys())
    placeholders = ", ".join(["?"] * len(resultado))
    # calc_timestamp se fuerza explícitamente en el UPDATE: el Engine
    # (calcular_reglas_factibilidad) no lo incluye en `resultado`, así
    # que sin esto el DEFAULT (datetime('now')) solo aplicaría en el
    # INSERT inicial y quedaría congelado en recalculos posteriores.
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in resultado.keys() if c != "indicador_id"
    ) + ", calc_timestamp = datetime('now')"
    cursor.execute(
        f"INSERT INTO calculo_factibilidad ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(indicador_id) DO UPDATE SET {updates}",
        list(resultado.values()),
    )


def _sincronizar_descripcion_destino(
    cursor,
    id_dest: int,
    descripcion_origen: dict | None,
    eje_politica_cambian: bool,
) -> None:
    """Copia los campos descriptivos de ``descripcion_origen`` (método de
    cálculo, numerador/denominador, dominio/subdominio, eje, política de
    gobierno, etc.) hacia ``id_dest``, salvo los explícitamente excluidos
    (ver ``_CAMPOS_EXCLUIDOS_SYNC_DESCRIPCION``). Si eje/política cambian,
    re-sincroniza también ``indicador_ejes_politicas`` en destino.

    Bloque extraído de ``sincronizar_contenido_referenciados()`` (Hallazgo #3
    del informe de revisión de código de agosto 2026).
    """
    if descripcion_origen is None:
        return
    set_q = ", ".join(f"{c} = ?" for c in descripcion_origen)
    cursor.execute(
        f"UPDATE indicadores SET {set_q} WHERE id = ?",
        [*descripcion_origen.values(), id_dest],
    )
    if eje_politica_cambian:
        sincronizar_ejes_politicas(cursor, id_dest)


def _reemplazar_campos_personalizados_indicador_destino(
    cursor, id_dest: int, personalizados_origen: dict
) -> None:
    """Reemplaza por completo los campos personalizados (Auxiliares) del
    indicador ``id_dest`` con una copia exacta de ``personalizados_origen``
    — mismo criterio que con las fuentes, no se "mezclan" (evita que una
    categoría eliminada en origen sobreviva en destino por un upsert
    parcial).

    Bloque extraído de ``sincronizar_contenido_referenciados()`` (Hallazgo #3
    del informe de revisión de código de agosto 2026).
    """
    cursor.execute(
        "DELETE FROM indicador_campos_personalizados WHERE indicador_id = ?",
        (id_dest,),
    )
    guardar_campos_personalizados(
        "indicador", id_dest, personalizados_origen, cursor=cursor
    )


def sincronizar_contenido_referenciados(cursor, indicador_id: int) -> list[str]:
    """Propaga fuente(s) y criterios de factibilidad del indicador recién
    guardado (``indicador_id``) hacia todos los códigos que aparecen en su
    campo ``indicadores_duplicados`` — ya combinado y sincronizado
    bidireccionalmente por ``sincronizar_indicadores_referenciados()``.

    Confirmado con la jefa de Randy en ONE (2026-07-24): un indicador
    referenciado comparte fuente y tratamiento metodológico con el
    indicador al que referencia, sin excepciones. Por eso:

    - **Fuentes**: se reemplazan por completo en cada destino con una copia
      exacta de las fuentes del origen (fuentes_indicador es 1:N; no tiene
      sentido "mezclar" fuentes entre dos filas que deben ser idénticas).
      Los campos personalizados de las fuentes reemplazadas se copian junto
      con ellas; los de las fuentes viejas del destino se eliminan en
      cascada al borrar esas filas (FK ON DELETE CASCADE).
    - **Factibilidad**: se copian los 13 criterios crudos C1-C3.2 del origen
      y SIEMPRE se recalculan c1_valor…score_factibilidad_final con el
      Engine en destino (nunca se copia el score directamente).
    - **Descripción del indicador** (pedido de Randy 2026-07-26): se copian
      también los demás campos descriptivos de ``indicadores`` (método de
      cálculo, numerador/denominador, dominio/subdominio, eje, política de
      gobierno, etc. — ver ``_CAMPOS_DESCRIPCION_INDICADOR``). El nombre
      (columna ``indicador``) queda explícitamente excluido: confirmado con
      la jefa de Randy (2026-07-27), dos indicadores referenciados pueden
      llevar el mismo tratamiento sin llamarse igual — nombres parecidos
      pero no idénticos son el caso de uso real.
      Si eje/política de gobierno cambian, se re-sincroniza también
      ``indicador_ejes_politicas`` en destino con el nuevo par principal.
    - **Campos personalizados del indicador** (Auxiliares, tabla EAV
      ``indicador_campos_personalizados``; reforzado 2026-08-01 tras
      confirmarse con la jefa que NO existen excepciones a la regla de
      fuente/tratamiento compartido): se reemplazan por completo en el
      destino con una copia exacta de los del origen — igual criterio que
      con las fuentes, no se "mezclan". Antes de este refuerzo estos campos
      no se propagaban, siendo la única brecha frente a "reflejar el 100%
      de los campos compartidos".

    NO propaga codigo, generador_demanda(_id), estado_indicador,
    estado_publicacion ni el nombre (``indicador``) — ver
    ``_CAMPOS_EXCLUIDOS_SYNC_DESCRIPCION``.
    Devuelve la lista de códigos destino actualizados (para logging/
    trazabilidad).
    """
    fila = cursor.execute(
        "SELECT indicadores_duplicados FROM indicadores WHERE id = ?",
        (indicador_id,),
    ).fetchone()
    if not fila:
        return []
    codigos_destino = [c.strip() for c in (fila[0] or "").split(",") if c.strip()]
    if not codigos_destino:
        return []

    fuentes_origen = cursor.execute(
        "SELECT * FROM fuentes_indicador WHERE indicador_id = ?", (indicador_id,)
    ).fetchall()
    columnas_fuente = [d[0] for d in cursor.description]

    fila_fact = cursor.execute(
        f"SELECT {', '.join(_CAMPOS_CRITERIO_FACTIBILIDAD)} FROM calculo_factibilidad "
        "WHERE indicador_id = ?",
        (indicador_id,),
    ).fetchone()
    criterios_origen = (
        dict(zip(_CAMPOS_CRITERIO_FACTIBILIDAD, fila_fact, strict=True))
        if fila_fact else None
    )

    fila_descripcion = cursor.execute(
        f"SELECT {', '.join(_CAMPOS_DESCRIPCION_INDICADOR)} FROM indicadores "
        "WHERE id = ?",
        (indicador_id,),
    ).fetchone()
    descripcion_origen = (
        dict(zip(_CAMPOS_DESCRIPCION_INDICADOR, fila_descripcion, strict=True))
        if fila_descripcion else None
    )
    _eje_politica_cambian = descripcion_origen is not None and (
        "eje_id" in descripcion_origen or "politica_gobierno_id" in descripcion_origen
    )

    personalizados_indicador_origen = {
        categoria_id: valor_id
        for categoria_id, valor_id in cursor.execute(
            "SELECT categoria_id, valor_id FROM indicador_campos_personalizados "
            "WHERE indicador_id = ?",
            (indicador_id,),
        ).fetchall()
    }

    # Perf: resolver codigo -> id de todos los destinos en una sola consulta
    # con IN, en vez de un SELECT por codigo_dest dentro del loop (N
    # round-trips -> 1). Ver Hallazgo 3 del informe de rendimiento de
    # agosto 2026.
    placeholders_dest = ", ".join(["?"] * len(codigos_destino))
    ids_por_codigo = dict(
        cursor.execute(
            f"SELECT codigo, id FROM indicadores WHERE codigo IN ({placeholders_dest})",
            codigos_destino,
        ).fetchall()
    )

    actualizados = []
    for codigo_dest in codigos_destino:
        id_dest = ids_por_codigo.get(codigo_dest)
        if id_dest is None:
            continue

        _reemplazar_fuentes_destino(cursor, id_dest, fuentes_origen, columnas_fuente)
        _recalcular_factibilidad_destino(cursor, id_dest, criterios_origen)
        _sincronizar_descripcion_destino(
            cursor, id_dest, descripcion_origen, _eje_politica_cambian
        )
        _reemplazar_campos_personalizados_indicador_destino(
            cursor, id_dest, personalizados_indicador_origen
        )

        actualizados.append(codigo_dest)

    return actualizados


def sincronizar_ejes_politicas(
    cursor, indicador_id: int, pares_extra: list[tuple] | None = None
) -> None:
    """Reescribe indicador_ejes_politicas: incluye el par principal del
    indicador más cualquier par adicional en pares_extra.
    """
    fila = cursor.execute(
        "SELECT eje_id, politica_gobierno_id FROM indicadores WHERE id = ?",
        (indicador_id,),
    ).fetchone()
    principal = (fila[0], fila[1]) if fila else (None, None)
    pares = {principal} | {tuple(p) for p in (pares_extra or [])}
    cursor.execute(
        "DELETE FROM indicador_ejes_politicas WHERE indicador_id = ?", (indicador_id,)
    )
    for eje_id, politica_id in pares:
        if eje_id is None and politica_id is None:
            continue
        cursor.execute(
            """
            INSERT OR IGNORE INTO indicador_ejes_politicas
                (indicador_id, eje_id, politica_gobierno_id)
            VALUES (?, ?, ?)
            """,
            (indicador_id, eje_id, politica_id),
        )


# ---------------------------------------------------------------------------
# Consultas de lectura
# ---------------------------------------------------------------------------

def obtener_indicadores_para_referencia(excluir_id: int | None = None) -> list[dict]:
    """Lista liviana de todos los indicadores para el selector de referencias."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        if excluir_id is not None:
            cursor.execute(
                "SELECT id, codigo, indicador, generador_demanda FROM indicadores_resuelto "
                "WHERE id != ? ORDER BY codigo",
                (excluir_id,),
            )
        else:
            cursor.execute(
                "SELECT id, codigo, indicador, generador_demanda FROM indicadores_resuelto "
                "ORDER BY codigo"
            )
        return [
            {"id": f[0], "codigo": f[1], "indicador": f[2], "generador_demanda": f[3]}
            for f in cursor.fetchall()
        ]
    finally:
        conn.close()


def obtener_ejes_politicas_extra(indicador_id: int) -> list[tuple]:
    """Devuelve los pares (eje_id, politica_id) adicionales (excluye el par principal)."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT eje_id, politica_gobierno_id FROM indicadores WHERE id = ?",
            (indicador_id,),
        ).fetchone()
        principal = (fila[0], fila[1]) if fila else (None, None)
        return [
            (r[0], r[1])
            for r in cursor.execute(
                "SELECT eje_id, politica_gobierno_id FROM indicador_ejes_politicas "
                "WHERE indicador_id = ?",
                (indicador_id,),
            ).fetchall()
            if (r[0], r[1]) != principal
        ]
    finally:
        conn.close()


def obtener_indicador_por_id(indicador_id: int) -> dict:
    """Devuelve indicador, fuentes y factibilidad para un indicador dado.

    Los campos categóricos vienen ya resueltos a texto (vía indicadores_resuelto /
    fuentes_resuelto), junto con sus _id para preseleccionar en formularios.
    """
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        indicador = cursor.execute(
            "SELECT * FROM indicadores_resuelto WHERE id = ?", (indicador_id,)
        ).fetchone()
        indicador_dict = dict(indicador) if indicador else {}

        # 'eje'/'politica_gobierno' en indicadores_resuelto son el par legado
        # único (1:1). Un indicador puede tener varios pares adicionales en
        # indicador_ejes_politicas (1:N); se agrega la lista completa ya
        # concatenada para que los consumidores (p. ej. la ficha PDF) puedan
        # mostrarlos todos en vez de solo el primero. No se sobrescriben
        # 'eje'/'politica_gobierno' ni sus *_id: el formulario de edición
        # sigue usándolos para la preselección del par principal.
        if indicador_dict:
            ejes_politicas = cursor.execute(
                "SELECT ejes_politicas_todos, num_ejes_politicas "
                "FROM ejes_politicas_por_indicador WHERE indicador_id = ?",
                (indicador_id,),
            ).fetchone()
            if ejes_politicas:
                indicador_dict["ejes_politicas_todos"] = ejes_politicas[0]
                indicador_dict["num_ejes_politicas"] = ejes_politicas[1]

        fuentes = cursor.execute(
            "SELECT * FROM fuentes_resuelto WHERE indicador_id = ?", (indicador_id,)
        ).fetchall()
        factibilidad = cursor.execute(
            "SELECT * FROM calculo_factibilidad WHERE indicador_id = ?", (indicador_id,)
        ).fetchone()
        return {
            "indicador": indicador_dict,
            "fuentes": [dict(f) for f in fuentes],
            "factibilidad": dict(factibilidad) if factibilidad else {},
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD de fuentes (fuera del ciclo principal del indicador)
# ---------------------------------------------------------------------------

def agregar_fuente(
    indicador_id: int,
    datos_fuente: dict,
    usuario_id: int | None = None,
    campos_personalizados: dict | None = None,
) -> tuple[bool, str]:
    """Inserta una fuente adicional para un indicador existente."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT codigo FROM indicadores WHERE id = ?", (indicador_id,)
        ).fetchone()
        if not fila:
            return False, f"No se encontró el indicador id={indicador_id}."
        codigo = fila[0]

        # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo:
        # f.keys() debe originarse siempre en literales de código, nunca en input libre.
        f = {**datos_fuente, "indicador_id": indicador_id}
        _validar_columnas(f.keys(), COLUMNAS_FUENTES_INDICADOR, "fuentes_indicador")
        cursor.execute(
            f"INSERT INTO fuentes_indicador ({', '.join(f.keys())}) "
            f"VALUES ({', '.join(['?'] * len(f))})",
            list(f.values()),
        )
        fuente_id = cursor.lastrowid
        guardar_campos_personalizados("fuente", fuente_id, campos_personalizados, cursor=cursor)

        # Una fuente agregada es contenido público nuevo del indicador —
        # pasa por el mismo flujo de borrador/aprobación que cualquier otra
        # edición (ver models/revision_pendiente.py). Antes esta función no
        # tocaba estado_publicacion en absoluto, así que un Editor podía
        # agregar una fuente a un indicador ya publicado sin que pasara por
        # revisión de un supervisor.
        cambios = [{
            "campo": "Fuente agregada",
            "anterior": "—",
            "nuevo": _resumen_fuente(datos_fuente),
        }]
        marcar_pendiente_revision(cursor, indicador_id, cambios)

        registrar_log(
            cursor, usuario_id, "ACTUALIZAR",
            f"Fuente agregada (id={fuente_id}) al indicador '{codigo}' (id={indicador_id})",
        )
        conn.commit()
        logger.info(
            "Fuente id=%d agregada al indicador '%s' (id=%d).",
            fuente_id, codigo, indicador_id,
        )
        return True, "Fuente agregada correctamente. Queda en borrador hasta que un supervisor la apruebe."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al agregar fuente al indicador id=%d: %s.", indicador_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al agregar fuente al indicador id=%d.", indicador_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def actualizar_fuente(
    fuente_id: int,
    datos_fuente: dict,
    usuario_id: int | None = None,
    campos_personalizados: dict | None = None,
) -> tuple[bool, str]:
    """Actualiza una fuente puntual por su id."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            """
            SELECT i.codigo, f.indicador_id
            FROM fuentes_indicador f
            JOIN indicadores i ON i.id = f.indicador_id
            WHERE f.id = ?
            """,
            (fuente_id,),
        ).fetchone()
        if not fila:
            return False, f"No se encontró la fuente id={fuente_id}."
        codigo, indicador_id = fila

        # Snapshot "antes" para el resumen de cambios del supervisor (mismo
        # patrón que modificar_indicador — ver models/revision_pendiente.py).
        columnas_a_comparar = [c for c in datos_fuente if c in COLUMNAS_FUENTES_INDICADOR]
        fuente_previa = {}
        if columnas_a_comparar:
            fila_ant = cursor.execute(
                f"SELECT {', '.join(columnas_a_comparar)} FROM fuentes_indicador WHERE id = ?",
                (fuente_id,),
            ).fetchone()
            if fila_ant:
                fuente_previa = dict(zip(columnas_a_comparar, fila_ant))

        # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo:
        # datos_fuente.keys() debe originarse siempre en literales de código, nunca en input libre.
        _validar_columnas(datos_fuente.keys(), COLUMNAS_FUENTES_INDICADOR, "fuentes_indicador")
        set_q = ", ".join(f"{col} = ?" for col in datos_fuente.keys())
        cursor.execute(
            f"UPDATE fuentes_indicador SET {set_q} WHERE id = ?",
            list(datos_fuente.values()) + [fuente_id],
        )
        if cursor.rowcount == 0:
            return False, f"No se encontró la fuente id={fuente_id}."
        guardar_campos_personalizados("fuente", fuente_id, campos_personalizados, cursor=cursor)

        # Igual que agregar_fuente: editar una fuente es contenido público
        # del indicador y debe pasar por revisión del supervisor antes de
        # quedar visible (ver models/revision_pendiente.py). Se llama
        # incondicionalmente —igual que agregar_fuente y modificar_indicador—
        # incluso cuando `cambios` queda vacío (guardar sin modificar nada,
        # o un borrador previo a esta función): el indicador debe volver a
        # borrador y pasar por supervisor de todas formas. Si no hay detalle
        # de campos, Aprobar Indicadores lo indica explícitamente en vez de
        # omitir la revisión.
        cambios = calcular_diferencias(
            fuente_previa, datos_fuente, ETIQUETAS_FUENTE, prefijo="Fuente: "
        )
        marcar_pendiente_revision(cursor, indicador_id, cambios)

        registrar_log(
            cursor, usuario_id, "ACTUALIZAR",
            f"Fuente id={fuente_id} actualizada (indicador '{codigo}', id={indicador_id})",
        )
        conn.commit()
        logger.info("Fuente id=%d actualizada.", fuente_id)
        mensaje = (
            "Fuente actualizada correctamente. El indicador vuelve a borrador "
            "hasta que un supervisor la apruebe."
        )
        return True, mensaje
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al actualizar fuente id=%d: %s.", fuente_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al actualizar fuente id=%d.", fuente_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def eliminar_fuente(
    fuente_id: int, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Elimina una fuente puntual sin afectar el indicador ni las demás fuentes."""
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            """
            SELECT f.indicador_id, f.nombre_fuente, i.codigo
            FROM fuentes_indicador f
            JOIN indicadores i ON i.id = f.indicador_id
            WHERE f.id = ?
            """,
            (fuente_id,),
        ).fetchone()
        if not fila:
            return False, f"No se encontró la fuente id={fuente_id}."
        indicador_id, nombre_fuente, codigo = fila

        cursor.execute("DELETE FROM fuentes_indicador WHERE id = ?", (fuente_id,))

        # Eliminar una fuente también es un cambio de contenido público —
        # mismo flujo de revisión que agregar/editar (ver
        # models/revision_pendiente.py).
        marcar_pendiente_revision(cursor, indicador_id, [{
            "campo": "Fuente eliminada",
            "anterior": nombre_fuente or "(sin nombre registrado)",
            "nuevo": "—",
        }])

        registrar_log(
            cursor, usuario_id, "ACTUALIZAR",
            f"Fuente '{nombre_fuente or 's/n'}' (id={fuente_id}) eliminada "
            f"del indicador '{codigo}' (id={indicador_id})",
        )
        conn.commit()
        logger.info("Fuente id=%d eliminada del indicador id=%d.", fuente_id, indicador_id)
        return True, (
            "Fuente eliminada correctamente. El indicador vuelve a borrador "
            "hasta que un supervisor apruebe el cambio."
        )
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al eliminar fuente id=%d: %s.", fuente_id, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al eliminar fuente id=%d.", fuente_id)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD principal de indicadores
# ---------------------------------------------------------------------------

def guardar_indicador(
    datos_indicador: dict,
    datos_fuentes: list[dict],
    datos_factibilidad: dict,
    usuario_id: int | None = None,
    campos_personalizados_indicador: dict | None = None,
    campos_personalizados_fuentes: list[dict] | None = None,
) -> tuple[bool, str]:
    """Crea un indicador en una sola transacción atómica.

    Flujo: INSERT indicadores → campos personalizados → sincronización de
    referencias → ejes/políticas → INSERT fuentes → Engine → INSERT
    calculo_factibilidad → log de auditoría.

    ``datos_indicador`` puede incluir las claves opcionales:
    - ``_ejes_politicas_extra``: lista de tuplas (eje_id, politica_id)
      adicionales (PNPSP multi-eje).
    - ``_referencias_manuales``: lista de códigos de indicadores referenciados
      manualmente por el usuario.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        pares_extra = datos_indicador.pop("_ejes_politicas_extra", None)
        codigos_manuales = datos_indicador.pop("_referencias_manuales", None)

        codigo = datos_indicador.get("codigo", "").strip()
        if not codigo:
            return False, "El código del indicador es obligatorio."
        nombre = datos_indicador.get("indicador", "").strip()
        if not nombre:
            return False, "El nombre del indicador es obligatorio."

        # titulo_normalizado se escribe en el mismo INSERT que `indicador`
        # para que _sugerir_referencias_automaticas() pueda resolver por
        # índice en vez de escanear la tabla completa (Hallazgo 2, informe
        # de rendimiento agosto 2026).
        datos_indicador["titulo_normalizado"] = normalizar_titulo_indicador(nombre)

        # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo:
        # datos_indicador.keys() debe originarse siempre en literales de código, nunca en input libre.
        _validar_columnas(datos_indicador.keys(), COLUMNAS_INDICADORES, "indicadores")
        columnas = ", ".join(datos_indicador.keys())
        signos = ", ".join(["?"] * len(datos_indicador))
        cursor.execute(
            f"INSERT INTO indicadores ({columnas}) VALUES ({signos})",
            list(datos_indicador.values()),
        )
        indicador_id = cursor.lastrowid

        guardar_campos_personalizados(
            "indicador", indicador_id, campos_personalizados_indicador, cursor=cursor
        )
        sincronizar_indicadores_referenciados(
            cursor, indicador_id, codigo, nombre,
            datos_indicador.get("generador_demanda_id"), codigos_manuales or [],
        )
        sincronizar_ejes_politicas(cursor, indicador_id, pares_extra)

        # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo.
        for i, fuente in enumerate(datos_fuentes):
            f = {**fuente, "indicador_id": indicador_id}
            _validar_columnas(f.keys(), COLUMNAS_FUENTES_INDICADOR, "fuentes_indicador")
            cursor.execute(
                f"INSERT INTO fuentes_indicador ({', '.join(f.keys())}) "
                f"VALUES ({', '.join(['?'] * len(f))})",
                list(f.values()),
            )
            fuente_id = cursor.lastrowid
            campos_f = (campos_personalizados_fuentes or [None] * len(datos_fuentes))[i]
            guardar_campos_personalizados("fuente", fuente_id, campos_f, cursor=cursor)

        # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo:
        # resultado.keys() proviene del Engine (calcular_reglas_factibilidad), controlado por código.
        resultado = calcular_reglas_factibilidad(datos_factibilidad)
        resultado["indicador_id"] = indicador_id
        _validar_columnas(resultado.keys(), COLUMNAS_CALCULO_FACTIBILIDAD, "calculo_factibilidad")
        cursor.execute(
            f"INSERT INTO calculo_factibilidad ({', '.join(resultado.keys())}) "
            f"VALUES ({', '.join(['?'] * len(resultado))})",
            list(resultado.values()),
        )

        codigos_propagados = sincronizar_contenido_referenciados(cursor, indicador_id)

        # Si este indicador se creó ya como borrador (flujo normal desde
        # views/crear_indicador.py), lo marcamos como pendiente de revisión
        # tipo 'nuevo' para que Aprobar Indicadores lo distinga de una
        # edición a algo que ya era público (ver models/revision_pendiente.py).
        if datos_indicador.get("estado_publicacion") == "borrador":
            marcar_pendiente_revision(cursor, indicador_id, [])

        registrar_log(
            cursor, usuario_id, "CREAR",
            f"Indicador '{codigo}' creado (id={indicador_id})",
        )
        if codigos_propagados:
            registrar_log(
                cursor, usuario_id, "SINCRONIZAR_REFERENCIA",
                f"Fuente y factibilidad de '{codigo}' propagadas a indicadores "
                f"referenciados: {', '.join(codigos_propagados)}",
            )
        conn.commit()
        logger.info("Indicador '%s' creado con id=%d.", codigo, indicador_id)
        return True, "Indicador guardado correctamente."

    except sqlite3.IntegrityError as exc:
        conn.rollback()
        codigo_val = datos_indicador.get("codigo", "desconocido")
        logger.warning("Integridad violada al crear indicador '%s': %s.", codigo_val, exc)
        if "UNIQUE" in str(exc) and "codigo" in str(exc):
            return (
                False,
                f"Ya existe un indicador con el código '{codigo_val}'. "
                "Usa un código distinto o edita el indicador existente en vez de crear uno nuevo.",
            )
        return (
            False,
            "No se pudo guardar el indicador porque viola una restricción de la "
            "base de datos. Revisa que los campos relacionados (generador de "
            "demanda, clasificaciones, fuentes) tengan valores válidos. Si el "
            "problema persiste, contacta al administrador con este detalle: "
            f"{exc}",
        )
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al guardar indicador: %s.", exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error inesperado al guardar indicador.")
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def _capturar_estado_publicacion_anterior(
    cursor, id_indicador: int, datos_indicador: dict | None
) -> str | None:
    """Devuelve estado_publicacion actual del indicador, solo si el
    formulario efectivamente va a tocar esa columna. Necesario para poder
    registrar el cambio borrador<->publicado explícitamente en el log de
    auditoría (evento CAMBIO_PUBLICACION) y para clasificar 'nuevo' vs
    'actualizado' en marcar_pendiente_revision().

    Debe llamarse ANTES del UPDATE de `indicadores` en modificar_indicador()
    (ver _actualizar_indicador_row()). Extraído del Hallazgo #1 del informe
    de revisión de código de agosto 2026.
    """
    if not datos_indicador or "estado_publicacion" not in datos_indicador:
        return None
    fila_prev = cursor.execute(
        "SELECT estado_publicacion FROM indicadores WHERE id = ?", (id_indicador,)
    ).fetchone()
    return fila_prev[0] if fila_prev else None


def _capturar_estado_indicador_anterior(
    cursor, id_indicador: int, datos_indicador: dict | None
) -> str | None:
    """Misma lógica que _capturar_estado_publicacion_anterior() pero para
    estado_indicador (Activo/Desactivado), para el evento de auditoría
    CAMBIO_ESTADO.

    Debe llamarse ANTES del UPDATE de `indicadores` en modificar_indicador().
    Extraído del Hallazgo #1 del informe de revisión de código de agosto
    2026.
    """
    if not datos_indicador or "estado_indicador" not in datos_indicador:
        return None
    fila_prev_ei = cursor.execute(
        "SELECT estado_indicador FROM indicadores WHERE id = ?", (id_indicador,)
    ).fetchone()
    return fila_prev_ei[0] if fila_prev_ei else None


def _capturar_indicador_previo(
    cursor, id_indicador: int, datos_indicador: dict | None
) -> dict:
    """Snapshot 'antes' de los campos de `indicadores` que datos_indicador
    va a tocar, para el resumen de cambios que ve el supervisor en Aprobar
    Indicadores (ver models/revision_pendiente.py y
    _construir_resumen_cambios()). Limitado a las columnas presentes en
    datos_indicador -- comparar solo lo que el formulario realmente envía,
    no toda la fila.

    Debe llamarse ANTES del UPDATE de `indicadores` Y ANTES de que
    titulo_normalizado se agregue a datos_indicador (ver
    _actualizar_indicador_row()): titulo_normalizado no es un campo
    editable por el usuario y no debe aparecer como "cambio" en el diff.
    Extraído del Hallazgo #1 del informe de revisión de código de agosto
    2026.
    """
    if not datos_indicador:
        return {}
    columnas_a_comparar = [c for c in datos_indicador if c in COLUMNAS_INDICADORES]
    if not columnas_a_comparar:
        return {}
    fila_ant = cursor.execute(
        f"SELECT {', '.join(columnas_a_comparar)} FROM indicadores WHERE id = ?",
        (id_indicador,),
    ).fetchone()
    return dict(zip(columnas_a_comparar, fila_ant)) if fila_ant else {}


def _actualizar_indicador_row(
    cursor, id_indicador: int, datos_indicador: dict | None
) -> None:
    """Agrega titulo_normalizado (si `indicador` viene en el payload),
    ejecuta el UPDATE dinámico de `indicadores` y retira titulo_normalizado
    de datos_indicador inmediatamente después.

    titulo_normalizado se recalcula junto con `indicador` para que
    _sugerir_referencias_automaticas() pueda resolver por índice en vez de
    escanear la tabla completa (Hallazgo 2, informe de rendimiento agosto
    2026). Se retira del dict enseguida para que
    _construir_resumen_cambios() no lo vea como un campo modificado más
    en Aprobar Indicadores.

    Debe llamarse DESPUÉS de _capturar_indicador_previo(). Extraído del
    Hallazgo #1 del informe de revisión de código de agosto 2026.
    """
    if not datos_indicador:
        return
    if "indicador" in datos_indicador:
        datos_indicador["titulo_normalizado"] = normalizar_titulo_indicador(
            datos_indicador["indicador"]
        )
    # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo.
    _validar_columnas(datos_indicador.keys(), COLUMNAS_INDICADORES, "indicadores")
    set_q = ", ".join(f"{col} = ?" for col in datos_indicador.keys())
    cursor.execute(
        f"UPDATE indicadores SET {set_q} WHERE id = ?",
        list(datos_indicador.values()) + [id_indicador],
    )
    datos_indicador.pop("titulo_normalizado", None)


def _capturar_campos_personalizados_previo(
    cursor, id_indicador: int, campos_personalizados_indicador: dict | None
) -> dict[int, int | None]:
    """Snapshot 'antes' de los campos personalizados (Auxiliares dinámicos
    del indicador, tabla EAV indicador_campos_personalizados) -- mismo
    propósito que _capturar_indicador_previo() pero para esta tabla aparte,
    ya que guardar_campos_personalizados() nunca pasa por
    calcular_diferencias(), así que sin este snapshot editar un campo
    personalizado no se vería en el cuadro de "qué cambió" del supervisor.
    Limitado a las categorías que el formulario realmente envía.

    Debe llamarse ANTES de guardar_campos_personalizados() en
    modificar_indicador(). Extraído del Hallazgo #1 del informe de revisión
    de código de agosto 2026.
    """
    if not campos_personalizados_indicador:
        return {}
    marcadores = ", ".join("?" * len(campos_personalizados_indicador))
    return dict(cursor.execute(
        "SELECT categoria_id, valor_id FROM indicador_campos_personalizados "
        f"WHERE indicador_id = ? AND categoria_id IN ({marcadores})",
        [id_indicador, *campos_personalizados_indicador.keys()],
    ).fetchall())


def _actualizar_fuente_si_aplica(
    cursor, id_indicador: int, datos_fuente: dict | None, fuente_id: int | None
) -> None:
    """Actualiza la fuente ``fuente_id`` si se provee, o inserta una fuente
    nueva para ``id_indicador`` en caso contrario. No hace nada si
    ``datos_fuente`` es falsy.

    Extraído del Hallazgo #1 del informe de revisión de código de agosto
    2026.
    """
    if not datos_fuente:
        return
    # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo.
    if fuente_id:
        _validar_columnas(datos_fuente.keys(), COLUMNAS_FUENTES_INDICADOR, "fuentes_indicador")
        set_q = ", ".join(f"{col} = ?" for col in datos_fuente.keys())
        cursor.execute(
            f"UPDATE fuentes_indicador SET {set_q} WHERE id = ?",
            list(datos_fuente.values()) + [fuente_id],
        )
    else:
        f = {**datos_fuente, "indicador_id": id_indicador}
        _validar_columnas(f.keys(), COLUMNAS_FUENTES_INDICADOR, "fuentes_indicador")
        cursor.execute(
            f"INSERT INTO fuentes_indicador ({', '.join(f.keys())}) "
            f"VALUES ({', '.join(['?'] * len(f))})",
            list(f.values()),
        )


def _capturar_factibilidad_previa(
    cursor, id_indicador: int, datos_factibilidad: dict | None
) -> dict:
    """Snapshot 'antes' de los criterios C1-C3 de factibilidad -- mismo
    propósito que _capturar_indicador_previo() pero para
    calculo_factibilidad. Solo los campos que el formulario de "Cálculo de
    Factibilidad" realmente edita, no los campos derivados (score,
    categoría, *_valor) que el Engine recalcula siempre y no son ediciones
    del usuario.

    Debe llamarse ANTES de _recalcular_y_guardar_factibilidad(). Extraído
    del Hallazgo #1 del informe de revisión de código de agosto 2026.
    """
    if not datos_factibilidad:
        return {}
    fila_fact_ant = cursor.execute(
        f"SELECT {', '.join(datos_factibilidad.keys())} "
        "FROM calculo_factibilidad WHERE indicador_id = ?",
        (id_indicador,),
    ).fetchone()
    return dict(zip(datos_factibilidad.keys(), fila_fact_ant)) if fila_fact_ant else {}


def _recalcular_y_guardar_factibilidad(
    cursor, id_indicador: int, datos_factibilidad: dict
) -> dict:
    """Corre el Engine sobre ``datos_factibilidad`` y hace upsert del
    resultado en calculo_factibilidad. calc_timestamp se fuerza
    explícitamente en el UPDATE porque el Engine no lo incluye en el
    resultado -- sin esto quedaría congelado en la fecha del primer
    registro (bug reportado por Randy: no se veía actualizado en el Excel
    exportado). Devuelve el resultado calculado por si el llamador lo
    necesita.

    Extraído del Hallazgo #1 del informe de revisión de código de agosto
    2026.
    """
    # Ver INVARIANTE DE SEGURIDAD (SQL) en el docstring del módulo:
    # resultado.keys() proviene del Engine (calcular_reglas_factibilidad), controlado por código.
    resultado = calcular_reglas_factibilidad(datos_factibilidad)
    resultado["indicador_id"] = id_indicador
    _validar_columnas(resultado.keys(), COLUMNAS_CALCULO_FACTIBILIDAD, "calculo_factibilidad")
    cols = ", ".join(resultado.keys())
    placeholders = ", ".join(["?"] * len(resultado))
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in resultado.keys() if c != "indicador_id"
    ) + ", calc_timestamp = datetime('now')"
    cursor.execute(
        f"INSERT INTO calculo_factibilidad ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(indicador_id) DO UPDATE SET {updates}",
        list(resultado.values()),
    )
    return resultado


def _construir_resumen_cambios(
    cursor,
    indicador_previo: dict,
    datos_indicador: dict,
    factibilidad_previa: dict,
    datos_factibilidad: dict | None,
    ejes_extra_previo: list[tuple],
    pares_extra: list[tuple] | None,
    campos_personalizados_previo: dict[int, int | None],
    campos_personalizados_indicador: dict | None,
) -> list[dict]:
    """Arma la lista de cambios (campo/anterior/nuevo) que ve el supervisor
    en Aprobar Indicadores (ver models/revision_pendiente.py), combinando
    el diff de columnas de `indicadores` y `calculo_factibilidad` (vía
    calcular_diferencias()) con los ejes/políticas adicionales y los
    campos personalizados de Auxiliares, que viven en tablas aparte y no
    pasan por calcular_diferencias().

    estado_publicacion se excluye del diff de contenido: siempre se fuerza
    a 'borrador' en este flujo y ya se audita aparte como
    CAMBIO_PUBLICACION -- mostrarlo aquí también sería ruido, no una
    edición real de contenido.

    Solo debe llamarse cuando el indicador queda en 'borrador' tras esta
    edición, con los snapshots "previo" ya capturados en los puntos
    correctos del flujo (ver docstrings de cada _capturar_*_previo()).
    Extraído del Hallazgo #1 del informe de revisión de código de agosto
    2026.
    """
    _datos_para_diff = {
        k: v for k, v in datos_indicador.items() if k != "estado_publicacion"
    }
    cambios = calcular_diferencias(indicador_previo, _datos_para_diff, ETIQUETAS_INDICADOR)
    cambios += calcular_diferencias(
        factibilidad_previa, datos_factibilidad or {}, ETIQUETAS_FACTIBILIDAD
    )
    # Ejes/políticas adicionales: no son una columna de `indicadores` (viven
    # en indicador_ejes_politicas), así que no pasan por
    # calcular_diferencias() como el resto de los campos -- se comparan
    # aparte, formateados a texto legible, y solo se agregan al resumen si
    # el conjunto realmente cambió.
    ejes_extra_nuevo = pares_extra if pares_extra is not None else ejes_extra_previo
    texto_previo = formatear_pares_ejes_politicas(ejes_extra_previo)
    texto_nuevo = formatear_pares_ejes_politicas(ejes_extra_nuevo)
    if texto_previo != texto_nuevo:
        cambios.append({
            "campo": "Ejes/Políticas de gobierno adicionales",
            "anterior": texto_previo,
            "nuevo": texto_nuevo,
        })
    # Mismo caso que los ejes/políticas adicionales: los campos
    # personalizados de Auxiliares tampoco pasan por calcular_diferencias()
    # por ser una tabla EAV aparte, así que se comparan a mano contra el
    # snapshot tomado antes del guardado.
    for _cat_id, _valor_nuevo in (campos_personalizados_indicador or {}).items():
        _valor_anterior = campos_personalizados_previo.get(_cat_id)
        if _valor_anterior == _valor_nuevo:
            continue
        _fila_cat = cursor.execute(
            "SELECT nombre_visible FROM auxiliares_categorias WHERE id = ?",
            (_cat_id,),
        ).fetchone()
        cambios.append({
            "campo": _fila_cat[0] if _fila_cat else f"Campo personalizado {_cat_id}",
            "anterior": resolver_texto(_valor_anterior) or "—",
            "nuevo": resolver_texto(_valor_nuevo) or "—",
        })
    return cambios


def modificar_indicador(
    id_indicador: int,
    datos_indicador: dict,
    datos_factibilidad: dict,
    datos_fuente: dict | None = None,
    fuente_id: int | None = None,
    usuario_id: int | None = None,
    campos_personalizados_indicador: dict | None = None,
) -> tuple[bool, str]:
    """Actualiza un indicador, su fuente principal opcional y recalcula factibilidad.

    ``datos_fuente``: dict con columnas de fuentes_indicador a guardar (sin
    'id' ni 'indicador_id'). Si ``fuente_id`` se provee se actualiza esa fila;
    si no, se inserta una nueva fuente.

    Orquesta los pasos vía funciones privadas nombradas y testeables por
    separado (Hallazgo #1 del informe de revisión de código de agosto
    2026). El ORDEN de estas llamadas es significativo: varias funciones
    de captura "previo" dependen de ejecutarse antes o después de un
    UPDATE/INSERT específico (documentado en el docstring de cada una,
    p. ej. `ejes_extra_previo` se captura intencionalmente DESPUÉS del
    UPDATE de `indicadores` -- comportamiento preexistente a esta
    extracción) y no deben reordenarse sin revisar esas dependencias.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        pares_extra = datos_indicador.pop("_ejes_politicas_extra", None) if datos_indicador else None
        codigos_manuales = datos_indicador.pop("_referencias_manuales", None) if datos_indicador else None

        # Trazabilidad de estado_publicacion / estado_indicador: se captura
        # ANTES del UPDATE de indicadores para poder registrar el cambio
        # explícitamente en el log de auditoría (CAMBIO_PUBLICACION /
        # CAMBIO_ESTADO) más abajo.
        estado_publicacion_anterior = _capturar_estado_publicacion_anterior(
            cursor, id_indicador, datos_indicador
        )
        estado_indicador_anterior = _capturar_estado_indicador_anterior(
            cursor, id_indicador, datos_indicador
        )
        indicador_previo = _capturar_indicador_previo(cursor, id_indicador, datos_indicador)

        _actualizar_indicador_row(cursor, id_indicador, datos_indicador)

        # Campos personalizados del indicador (Auxiliares dinámicos): el
        # "antes" se captura justo antes de guardar_campos_personalizados().
        campos_personalizados_previo = _capturar_campos_personalizados_previo(
            cursor, id_indicador, campos_personalizados_indicador
        )
        guardar_campos_personalizados(
            "indicador", id_indicador, campos_personalizados_indicador, cursor=cursor
        )

        if datos_indicador:
            sincronizar_indicadores_referenciados(
                cursor, id_indicador,
                datos_indicador.get("codigo"), datos_indicador.get("indicador"),
                datos_indicador.get("generador_demanda_id"), codigos_manuales or [],
            )

        # Ejes/políticas ADICIONALES: el "antes" se captura justo antes de
        # sincronizar_ejes_politicas(), DESPUÉS del UPDATE de indicadores de
        # arriba a propósito -- comportamiento preexistente a esta
        # extracción, no modificado (ver Hallazgo #1).
        ejes_extra_previo = obtener_ejes_politicas_extra(id_indicador)
        if datos_indicador or pares_extra is not None:
            sincronizar_ejes_politicas(cursor, id_indicador, pares_extra)

        _actualizar_fuente_si_aplica(cursor, id_indicador, datos_fuente, fuente_id)

        factibilidad_previa = _capturar_factibilidad_previa(cursor, id_indicador, datos_factibilidad)
        _recalcular_y_guardar_factibilidad(cursor, id_indicador, datos_factibilidad)

        fila_cod = cursor.execute(
            "SELECT codigo FROM indicadores WHERE id = ?", (id_indicador,)
        ).fetchone()
        codigo = fila_cod[0] if fila_cod else "desconocido"

        codigos_propagados = sincronizar_contenido_referenciados(cursor, id_indicador)

        # Resumen de cambios para Aprobar Indicadores (ver
        # models/revision_pendiente.py). Solo tiene sentido si esta edición
        # deja el indicador en 'borrador' (siempre el caso desde
        # views/actualizar_indicador.py, pero se valida por si en el futuro
        # se reutiliza esta función con otro flujo).
        if datos_indicador and datos_indicador.get("estado_publicacion") == "borrador":
            cambios = _construir_resumen_cambios(
                cursor, indicador_previo, datos_indicador,
                factibilidad_previa, datos_factibilidad,
                ejes_extra_previo, pares_extra,
                campos_personalizados_previo, campos_personalizados_indicador,
            )
            # Ver docstring de marcar_pendiente_revision(): el UPDATE de
            # más arriba ya forzó estado_publicacion a 'borrador', así que
            # hay que pasarle explícitamente el valor de ANTES de esta
            # edición (capturado al inicio de esta función) para que la
            # clasificación 'nuevo' vs 'actualizado' sea correcta.
            marcar_pendiente_revision(
                cursor, id_indicador, cambios,
                estado_publicacion_previo=estado_publicacion_anterior,
            )

        registrar_log(
            cursor, usuario_id, "ACTUALIZAR",
            f"Indicador '{codigo}' (id={id_indicador}) actualizado",
        )
        if codigos_propagados:
            registrar_log(
                cursor, usuario_id, "SINCRONIZAR_REFERENCIA",
                f"Fuente y factibilidad de '{codigo}' propagadas a indicadores "
                f"referenciados: {', '.join(codigos_propagados)}",
            )

        estado_publicacion_nuevo = datos_indicador.get("estado_publicacion") if datos_indicador else None
        if (
            estado_publicacion_nuevo is not None
            and estado_publicacion_nuevo != estado_publicacion_anterior
        ):
            registrar_log(
                cursor, usuario_id, "CAMBIO_PUBLICACION",
                f"Indicador '{codigo}' (id={id_indicador}) cambió estado_publicacion: "
                f"'{estado_publicacion_anterior}' → '{estado_publicacion_nuevo}'",
            )

        estado_indicador_nuevo = datos_indicador.get("estado_indicador") if datos_indicador else None
        if (
            estado_indicador_nuevo is not None
            and estado_indicador_nuevo != estado_indicador_anterior
        ):
            registrar_log(
                cursor, usuario_id, "CAMBIO_ESTADO",
                f"Indicador '{codigo}' (id={id_indicador}) cambió estado_indicador: "
                f"'{estado_indicador_anterior}' → '{estado_indicador_nuevo}'",
            )

        conn.commit()
        logger.info("Indicador '%s' (id=%d) actualizado.", codigo, id_indicador)
        return True, "Indicador actualizado correctamente."

    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al actualizar indicador id=%d: %s.", id_indicador, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al actualizar indicador id=%d.", id_indicador)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def aprobar_publicacion_indicador(
    id_indicador: int, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Aprueba y publica un indicador que está en 'borrador' (flujo de
    aprobación del rol supervisor — ver views/aprobar_indicadores.py).

    Solo cambia ``estado_publicacion`` a 'publicado'; no toca ningún otro
    campo del indicador, sus fuentes ni su factibilidad. Todo indicador
    creado o actualizado (ver guardar_indicador / modificar_indicador)
    entra siempre en 'borrador'; esta es la única función que lo saca de
    ahí, y queda registrada en auditoría como acción separada.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT codigo, estado_publicacion FROM indicadores WHERE id = ?",
            (id_indicador,),
        ).fetchone()
        if not fila:
            return False, "Indicador no encontrado."
        codigo, estado_actual = fila
        if estado_actual == "publicado":
            return False, f"El indicador '{codigo}' ya está publicado."

        cursor.execute(
            "UPDATE indicadores SET estado_publicacion = 'publicado' WHERE id = ?",
            (id_indicador,),
        )
        limpiar_revision_pendiente(cursor, id_indicador)
        registrar_log(
            cursor, usuario_id, "APROBAR_PUBLICACION",
            f"Indicador '{codigo}' (id={id_indicador}) aprobado y publicado por "
            "supervisor (estado_publicacion: 'borrador' → 'publicado').",
        )
        conn.commit()
        logger.info("Indicador '%s' (id=%d) aprobado y publicado.", codigo, id_indicador)
        return True, f"Indicador '{codigo}' aprobado y publicado correctamente."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al aprobar publicación del indicador id=%d: %s.", id_indicador, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al aprobar publicación del indicador id=%d.", id_indicador)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()


def borrar_indicador(
    id_indicador: int, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Elimina un indicador. Fuentes y factibilidad se eliminan en cascada.

    Salvaguarda contra eliminación masiva (agosto-2026, a pedido de la
    jefa de Randy en ONE): si quien elimina tiene rol `supervisor`, se
    cuenta cuántas eliminaciones lleva desde el último reseteo
    (``usuarios.eliminaciones_recientes``). Al llegar a
    ``UMBRAL_ELIMINACIONES_AUTOBLOQUEO`` (config.py), la cuenta se
    desactiva (``activo = 0``) y el contador se resetea a 0 — la siguiente
    tanda de eliminaciones, ya reactivada por un administrador, vuelve a
    exigir el umbral completo antes de bloquearse de nuevo. Solo cuenta
    para `supervisor`: no afecta eliminaciones ejecutadas por scripts
    directos de mantenimiento (ver protocolo de eliminación masiva en
    DESPLIEGUE_PRODUCCION.md), que no pasan por esta vista ni por este rol.
    Esta función NO cierra la sesión activa del usuario — eso lo hace la
    vista (views/eliminar_indicador.py), que revisa `activo` después de
    llamar aquí y fuerza logout si corresponde; esta función solo persiste
    el estado en BD.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT codigo FROM indicadores WHERE id = ?", (id_indicador,)
        ).fetchone()
        codigo = fila[0] if fila else "desconocido"

        cursor.execute("DELETE FROM indicadores WHERE id = ?", (id_indicador,))
        registrar_log(
            cursor, usuario_id, "ELIMINAR",
            f"Indicador '{codigo}' (id={id_indicador}) eliminado",
        )

        mensaje_extra = ""
        if usuario_id is not None:
            fila_u = cursor.execute(
                "SELECT rol, eliminaciones_recientes FROM usuarios WHERE id = ?",
                (usuario_id,),
            ).fetchone()
            if fila_u and fila_u[0] == "supervisor":
                nuevo_contador = (fila_u[1] or 0) + 1
                if nuevo_contador >= UMBRAL_ELIMINACIONES_AUTOBLOQUEO:
                    cursor.execute(
                        "UPDATE usuarios SET eliminaciones_recientes = 0, "
                        "activo = 0 WHERE id = ?",
                        (usuario_id,),
                    )
                    registrar_log(
                        cursor, usuario_id, "AUTO_DESACTIVAR",
                        f"Cuenta desactivada automáticamente: alcanzó "
                        f"{UMBRAL_ELIMINACIONES_AUTOBLOQUEO} eliminaciones "
                        "de indicadores. Requiere reactivación por un "
                        "administrador.",
                    )
                    mensaje_extra = (
                        f" ⚠️ Alcanzaste el límite de "
                        f"{UMBRAL_ELIMINACIONES_AUTOBLOQUEO} eliminaciones — "
                        "tu cuenta fue desactivada por seguridad y tu "
                        "sesión se cerró. Un administrador debe reactivarla "
                        "para que puedas volver a iniciar sesión."
                    )
                else:
                    cursor.execute(
                        "UPDATE usuarios SET eliminaciones_recientes = ? "
                        "WHERE id = ?",
                        (nuevo_contador, usuario_id),
                    )

        conn.commit()
        logger.info("Indicador '%s' (id=%d) eliminado.", codigo, id_indicador)
        return True, "Indicador eliminado correctamente." + mensaje_extra

    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al eliminar indicador id=%d: %s.", id_indicador, exc)
        return False, _mensaje_error_bd(exc)
    except Exception as exc:
        conn.rollback()
        logger.exception("Error al eliminar indicador id=%d.", id_indicador)
        return False, _mensaje_error_inesperado(exc)
    finally:
        conn.close()
