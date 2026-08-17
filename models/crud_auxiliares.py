"""
models/crud_auxiliares.py
=========================
CRUD para el sistema de Auxiliares: catálogos controlados y reutilizables
para todo campo de la SIDOE que requiera una lista de valores normalizados.

Modelo híbrido
--------------
Las tablas de datos (indicadores, fuentes_indicador) guardan el ID del auxiliar
(auxiliares_valores.id), no el texto. Renombrar un auxiliar actualiza
automáticamente cómo se ve en todos los indicadores vía JOIN en consulta.

auxiliares_historial guarda cada creación/renombrado/baja para trazabilidad.

Reglas de negocio
-----------------
- Solo roles 'editor' y 'administrador' pueden modificar el catálogo
  (impuesto en la vista; este módulo no vuelve a validar el rol).
- No se permiten valores vacíos ni duplicados (comparación insensible a
  mayúsculas/espacios) dentro de una misma categoría.
- Los valores no se borran físicamente si están en uso; se desactivan.
- Las categorías de sistema (las declaradas en config.py con columna fija)
  no pueden eliminarse ni reasignarse su aplica_a.
"""

import logging
import sqlite3

import streamlit as st

from config import CAMPOS_HIBRIDOS_FUENTES, CAMPOS_HIBRIDOS_INDICADORES
from data import database as db_mod

logger = logging.getLogger(__name__)

# TTL de caché para lecturas de catálogos (segundos). Los auxiliares cambian
# con poca frecuencia y se consultan en casi cada render de formulario; un
# TTL corto limita cuánto puede durar una inconsistencia si alguna ruta de
# mutación futura olvidara invalidar la caché explícitamente.
_CACHE_TTL_AUXILIARES = 300


def _invalidar_cache_auxiliares() -> None:
    """Limpia la caché de lecturas de catálogos tras cualquier mutación.

    Se llama al final de toda operación de escritura sobre categorías o
    valores de Auxiliares (crear/editar/activar/desactivar/eliminar), para
    que el próximo `st.selectbox` en cualquier vista refleje el cambio de
    inmediato en vez de esperar el TTL.
    """
    listar_categorias.clear()
    listar_categorias_personalizadas.clear()
    obtener_categoria_por_clave.clear()
    obtener_categoria_por_id.clear()
    obtener_valores.clear()


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _normalizar(valor: str) -> str:
    """Elimina espacios al borde de un texto, o devuelve '' si es None."""
    return (valor or "").strip()


def _existe_duplicado(
    cursor, categoria_id: int, valor: str, excluir_id: int | None = None
) -> bool:
    """Comprueba si ya existe un valor con el mismo texto (case/space insensitive)."""
    sql = """
        SELECT id FROM auxiliares_valores
        WHERE categoria_id = ? AND LOWER(TRIM(valor)) = LOWER(TRIM(?))
    """
    params: list = [categoria_id, valor]
    if excluir_id is not None:
        sql += " AND id != ?"
        params.append(excluir_id)
    return cursor.execute(sql, params).fetchone() is not None


def _columnas_referencia(categoria_clave: str) -> list[tuple[str, str]]:
    """Devuelve las tuplas (tabla, columna_id) que referencian esta categoría
    según los campos declarados en config.py. Una lista vacía indica que es
    una categoría personalizada (sin columna fija).
    """
    referencias = []
    for columna, clave, _nombre, _valores in CAMPOS_HIBRIDOS_INDICADORES:
        if clave == categoria_clave:
            referencias.append(("indicadores", f"{columna}_id"))
    for columna, clave, _nombre, _valores in CAMPOS_HIBRIDOS_FUENTES:
        if clave == categoria_clave:
            referencias.append(("fuentes_indicador", f"{columna}_id"))
    return referencias


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------

@st.cache_data(ttl=_CACHE_TTL_AUXILIARES)
def listar_categorias(solo_activas: bool = True) -> list[dict]:
    """Lista todas las categorías de Auxiliares ordenadas por nombre visible."""
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        sql = "SELECT * FROM auxiliares_categorias"
        if solo_activas:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre_visible"
        return [dict(f) for f in cursor.execute(sql).fetchall()]
    finally:
        conn.close()


@st.cache_data(ttl=_CACHE_TTL_AUXILIARES)
def obtener_categoria_por_clave(clave: str) -> dict | None:
    """Busca una categoría por su clave interna."""
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT * FROM auxiliares_categorias WHERE clave = ?", (clave,)
        ).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


@st.cache_data(ttl=_CACHE_TTL_AUXILIARES)
def obtener_categoria_por_id(categoria_id: int) -> dict | None:
    """Busca una categoría por su ID."""
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT * FROM auxiliares_categorias WHERE id = ?", (categoria_id,)
        ).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


@st.cache_data(ttl=_CACHE_TTL_AUXILIARES)
def listar_categorias_personalizadas(
    aplica_a: str | None = None, solo_activas: bool = True
) -> list[dict]:
    """Lista categorías creadas manualmente (aplica_a IS NOT NULL).

    Las 27 categorías de sistema dejan aplica_a en NULL porque ya tienen
    columna fija en las tablas de datos.

    Args:
        aplica_a: 'indicador', 'fuente', o None para todas las personalizadas.
        solo_activas: Si True, solo incluye las categorías activas.
    """
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        sql = "SELECT * FROM auxiliares_categorias WHERE aplica_a IS NOT NULL"
        params: list = []
        if aplica_a:
            sql += " AND aplica_a = ?"
            params.append(aplica_a)
        if solo_activas:
            sql += " AND activo = 1"
        sql += " ORDER BY nombre_visible"
        return [dict(f) for f in cursor.execute(sql, params).fetchall()]
    finally:
        conn.close()


def crear_categoria(
    clave: str,
    nombre_visible: str,
    descripcion: str = "",
    usuario_id: int | None = None,
    aplica_a: str | None = None,
) -> tuple[bool, str]:
    """Crea una nueva categoría de Auxiliares.

    Returns:
        Tupla (éxito, mensaje).
    """
    clave = _normalizar(clave).lower().replace(" ", "_")
    nombre_visible = _normalizar(nombre_visible)

    if not clave or not nombre_visible:
        return False, "La clave y el nombre visible de la categoría son obligatorios."
    if aplica_a not in (None, "indicador", "fuente"):
        return False, "El componente debe ser 'indicador' o 'fuente'."

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        existe = cursor.execute(
            "SELECT id FROM auxiliares_categorias WHERE LOWER(clave) = LOWER(?)",
            (clave,),
        ).fetchone()
        if existe:
            return False, f"Ya existe una categoría con la clave '{clave}'."

        cursor.execute(
            """
            INSERT INTO auxiliares_categorias (clave, nombre_visible, descripcion, aplica_a, activo)
            VALUES (?, ?, ?, ?, 1)
            """,
            (clave, nombre_visible, descripcion or "", aplica_a),
        )
        conn.commit()
        _invalidar_cache_auxiliares()
        logger.info("Categoría de Auxiliares creada: clave='%s'.", clave)
        return True, f"Categoría '{nombre_visible}' creada correctamente."
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        logger.error("Error de integridad al crear categoría '%s': %s.", clave, exc)
        return False, f"No se pudo crear la categoría: {exc}"
    finally:
        conn.close()


def actualizar_aplica_a_categoria(
    categoria_id: int, aplica_a: str, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Asigna o cambia el componente (indicador/fuente) de una categoría personalizada.

    No se permite sobre categorías de sistema (con columna fija).
    """
    if aplica_a not in ("indicador", "fuente"):
        return False, "El componente debe ser 'indicador' o 'fuente'."

    categoria = obtener_categoria_por_id(categoria_id)
    if not categoria:
        return False, "La categoría indicada no existe."
    if _columnas_referencia(categoria["clave"]):
        return False, (
            f"'{categoria['nombre_visible']}' es una categoría del sistema "
            "(tiene una columna fija) y no puede reasignarse."
        )

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE auxiliares_categorias SET aplica_a = ? WHERE id = ?",
            (aplica_a, categoria_id),
        )
        conn.commit()
        _invalidar_cache_auxiliares()
        componente_txt = (
            "Componente de Indicador" if aplica_a == "indicador" else "Componente de Fuente"
        )
        return True, f"'{categoria['nombre_visible']}' ahora es visible en {componente_txt}."
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Valores de cada categoría
# ---------------------------------------------------------------------------

@st.cache_data(ttl=_CACHE_TTL_AUXILIARES)
def obtener_valores(categoria_clave: str, solo_activos: bool = True) -> list[dict]:
    """Devuelve la lista de valores de una categoría con id, valor y activo."""
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        sql = """
            SELECT av.id, av.valor, av.activo
            FROM auxiliares_valores av
            JOIN auxiliares_categorias ac ON ac.id = av.categoria_id
            WHERE ac.clave = ?
        """
        params: list = [categoria_clave]
        if solo_activos:
            sql += " AND av.activo = 1"
        sql += " ORDER BY av.valor"
        return [dict(f) for f in cursor.execute(sql, params).fetchall()]
    finally:
        conn.close()


def obtener_valores_activos(categoria_clave: str) -> list[str]:
    """Lista de texto de valores activos de una categoría (para selectboxes simples)."""
    return [v["valor"] for v in obtener_valores(categoria_clave, solo_activos=True)]


def opciones_selectbox(categoria_clave: str) -> tuple[list[str], dict[str, int]]:
    """Devuelve (lista_textos, mapa_texto→id) listos para un st.selectbox."""
    valores = obtener_valores(categoria_clave, solo_activos=True)
    return [v["valor"] for v in valores], {v["valor"]: v["id"] for v in valores}


def resolver_o_crear_id(
    categoria_clave: str,
    texto: str,
    usuario_id: int | None = None,
    valor_por_defecto: str = "No identificado",
) -> int | None:
    """Resuelve un texto crudo al ID del auxiliar correspondiente.

    Si el valor no existe en el catálogo, lo crea automáticamente para no
    perder datos durante el ETL. Devuelve None si la operación falla.
    """
    # Normalizar entrada (maneja None y NaN de pandas)
    if texto is None or (isinstance(texto, float) and texto != texto):
        texto = ""
    texto = str(texto).strip() or valor_por_defecto

    for v in obtener_valores(categoria_clave, solo_activos=False):
        if v["valor"].strip().lower() == texto.lower():
            return v["id"]

    ok, _msg, nuevo_id = crear_valor(categoria_clave, texto, usuario_id=usuario_id)
    if ok:
        return nuevo_id

    # Último intento: recargar tras posible inserción concurrente
    for v in obtener_valores(categoria_clave, solo_activos=False):
        if v["valor"].strip().lower() == texto.lower():
            return v["id"]
    logger.error(
        "No se pudo resolver ni crear el valor '%s' en categoría '%s'.",
        texto, categoria_clave,
    )
    return None


def resolver_texto(auxiliar_id: int) -> str | None:
    """Dado un ID de auxiliar, devuelve su valor de texto actual."""
    if auxiliar_id is None:
        return None
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT valor FROM auxiliares_valores WHERE id = ?", (auxiliar_id,)
        ).fetchone()
        return fila[0] if fila else None
    finally:
        conn.close()


def crear_valor(
    categoria_clave: str, valor: str, usuario_id: int | None = None
) -> tuple[bool, str, int | None]:
    """Agrega un nuevo valor al catálogo de una categoría.

    Returns:
        Tupla (éxito, mensaje, nuevo_id_o_None).
    """
    valor = _normalizar(valor)
    if not valor:
        return False, "El valor no puede estar vacío.", None

    categoria = obtener_categoria_por_clave(categoria_clave)
    if not categoria:
        return False, f"La categoría '{categoria_clave}' no existe.", None

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        if _existe_duplicado(cursor, categoria["id"], valor):
            return (
                False,
                f"El valor '{valor}' ya existe en '{categoria['nombre_visible']}'.",
                None,
            )
        cursor.execute(
            """
            INSERT INTO auxiliares_valores (categoria_id, valor, activo, creado_por)
            VALUES (?, ?, 1, ?)
            """,
            (categoria["id"], valor, usuario_id),
        )
        nuevo_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO auxiliares_historial
                (auxiliar_id, accion, valor_anterior, valor_nuevo, usuario_id)
            VALUES (?, 'CREACION', NULL, ?, ?)
            """,
            (nuevo_id, valor, usuario_id),
        )
        conn.commit()
        _invalidar_cache_auxiliares()
        logger.info(
            "Valor '%s' agregado a categoría '%s'.", valor, categoria_clave
        )
        return True, f"Valor '{valor}' agregado a '{categoria['nombre_visible']}'.", nuevo_id
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        logger.error("Error de integridad al crear valor '%s': %s.", valor, exc)
        return False, f"El valor '{valor}' ya existe o viola una restricción: {exc}", None
    finally:
        conn.close()


def editar_valor(
    auxiliar_id: int, nuevo_valor: str, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Renombra un auxiliar. El cambio se refleja automáticamente en todos los
    indicadores que lo referencian por ID.
    """
    nuevo_valor = _normalizar(nuevo_valor)
    if not nuevo_valor:
        return False, "El nuevo valor no puede estar vacío."

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT categoria_id, valor FROM auxiliares_valores WHERE id = ?",
            (auxiliar_id,),
        ).fetchone()
        if not fila:
            return False, "El auxiliar indicado no existe."
        categoria_id, valor_anterior = fila

        if valor_anterior.strip().lower() == nuevo_valor.lower():
            return False, "El nuevo valor es igual al actual."
        if _existe_duplicado(cursor, categoria_id, nuevo_valor, excluir_id=auxiliar_id):
            return False, f"Ya existe otro valor '{nuevo_valor}' en esta categoría."

        cursor.execute(
            "UPDATE auxiliares_valores SET valor = ? WHERE id = ?",
            (nuevo_valor, auxiliar_id),
        )
        cursor.execute(
            """
            INSERT INTO auxiliares_historial
                (auxiliar_id, accion, valor_anterior, valor_nuevo, usuario_id)
            VALUES (?, 'RENOMBRADO', ?, ?, ?)
            """,
            (auxiliar_id, valor_anterior, nuevo_valor, usuario_id),
        )
        conn.commit()
        _invalidar_cache_auxiliares()
        logger.info(
            "Auxiliar id=%d renombrado: '%s' → '%s'.",
            auxiliar_id, valor_anterior, nuevo_valor,
        )
        return (
            True,
            f"Renombrado: '{valor_anterior}' → '{nuevo_valor}'. "
            "Se actualizó en todos los indicadores que lo usan.",
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        logger.error("Error de integridad al renombrar auxiliar id=%d: %s.", auxiliar_id, exc)
        return False, f"No se pudo renombrar: {exc}"
    finally:
        conn.close()


def cambiar_estado_valor(
    auxiliar_id: int, activo: bool, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Activa o desactiva un valor del catálogo."""
    accion = "ACTIVACION" if activo else "DESACTIVACION"
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT valor FROM auxiliares_valores WHERE id = ?", (auxiliar_id,)
        ).fetchone()
        if not fila:
            return False, "El auxiliar indicado no existe."

        cursor.execute(
            "UPDATE auxiliares_valores SET activo = ? WHERE id = ?",
            (1 if activo else 0, auxiliar_id),
        )
        cursor.execute(
            """
            INSERT INTO auxiliares_historial
                (auxiliar_id, accion, valor_anterior, valor_nuevo, usuario_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (auxiliar_id, accion, fila[0], fila[0], usuario_id),
        )
        conn.commit()
        _invalidar_cache_auxiliares()
        verbo = (
            "activado"
            if activo
            else "desactivado (deja de aparecer en formularios, pero los "
                 "indicadores que ya lo usan lo conservan)"
        )
        return True, f"Valor '{fila[0]}' {verbo}."
    finally:
        conn.close()


def eliminar_valor(
    auxiliar_id: int,
    usuario_id: int | None = None,
    columna_referencia: str | None = None,
) -> tuple[bool, str]:
    """Elimina físicamente un auxiliar SOLO si ningún registro lo referencia.

    Si está en uso, rechaza el borrado y sugiere desactivar en su lugar.
    """
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            """
            SELECT av.valor, ac.clave
            FROM auxiliares_valores av
            JOIN auxiliares_categorias ac ON ac.id = av.categoria_id
            WHERE av.id = ?
            """,
            (auxiliar_id,),
        ).fetchone()
        if not fila:
            return False, "El auxiliar indicado no existe."
        valor, categoria_clave = fila

        referencias = (
            [("indicadores", columna_referencia)]
            if columna_referencia
            else _columnas_referencia(categoria_clave)
        )

        total_en_uso = sum(
            cursor.execute(
                f"SELECT COUNT(*) FROM {tabla} WHERE {col} = ?", (auxiliar_id,)
            ).fetchone()[0]
            for tabla, col in referencias
        )
        # Categorías personalizadas guardan valores en tablas EAV
        total_en_uso += cursor.execute(
            "SELECT COUNT(*) FROM indicador_campos_personalizados WHERE valor_id = ?",
            (auxiliar_id,),
        ).fetchone()[0]
        total_en_uso += cursor.execute(
            "SELECT COUNT(*) FROM fuente_campos_personalizados WHERE valor_id = ?",
            (auxiliar_id,),
        ).fetchone()[0]

        if total_en_uso > 0:
            return (
                False,
                f"'{valor}' está en uso por {total_en_uso} registro(s). "
                "No se puede eliminar; desactívelo en su lugar.",
            )

        cursor.execute(
            """
            INSERT INTO auxiliares_historial
                (auxiliar_id, accion, valor_anterior, valor_nuevo, usuario_id)
            VALUES (?, 'ELIMINACION', ?, NULL, ?)
            """,
            (auxiliar_id, valor, usuario_id),
        )
        cursor.execute("DELETE FROM auxiliares_valores WHERE id = ?", (auxiliar_id,))
        conn.commit()
        _invalidar_cache_auxiliares()
        logger.info("Auxiliar id=%d ('%s') eliminado permanentemente.", auxiliar_id, valor)
        return True, f"Valor '{valor}' eliminado permanentemente."
    finally:
        conn.close()


def eliminar_categoria(
    categoria_id: int, usuario_id: int | None = None
) -> tuple[bool, str]:
    """Elimina una categoría personalizada completa (y sus valores en cascada).

    Restricciones:
    - No se puede eliminar una categoría de sistema (columna fija en config.py).
    - No se puede eliminar si alguno de sus valores está en uso.
    """
    categoria = obtener_categoria_por_id(categoria_id)
    if not categoria:
        return False, "La categoría indicada no existe."
    if _columnas_referencia(categoria["clave"]):
        return False, (
            f"'{categoria['nombre_visible']}' es una categoría del sistema "
            "(tiene una columna fija en Indicadores/Fuentes) y no puede eliminarse."
        )

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        valores_ids = [
            r[0]
            for r in cursor.execute(
                "SELECT id FROM auxiliares_valores WHERE categoria_id = ?", (categoria_id,)
            ).fetchall()
        ]

        total_en_uso = sum(
            cursor.execute(
                "SELECT COUNT(*) FROM indicador_campos_personalizados WHERE valor_id = ?",
                (vid,),
            ).fetchone()[0]
            + cursor.execute(
                "SELECT COUNT(*) FROM fuente_campos_personalizados WHERE valor_id = ?",
                (vid,),
            ).fetchone()[0]
            for vid in valores_ids
        )

        if total_en_uso > 0:
            return (
                False,
                f"'{categoria['nombre_visible']}' tiene valores en uso por "
                f"{total_en_uso} registro(s). Elimine o cambie esos valores antes "
                "de borrar la categoría.",
            )

        # ON DELETE CASCADE elimina auxiliares_valores, historial y campos EAV
        cursor.execute(
            "DELETE FROM auxiliares_categorias WHERE id = ?", (categoria_id,)
        )
        conn.commit()
        _invalidar_cache_auxiliares()

        from models.logs import registrar_log_standalone  # import tardío para evitar ciclo

        registrar_log_standalone(
            usuario_id,
            "ELIMINAR",
            f"Categoría de Auxiliares '{categoria['nombre_visible']}' "
            f"(clave={categoria['clave']}) eliminada junto con {len(valores_ids)} valor(es).",
        )
        logger.info(
            "Categoría Auxiliares '%s' eliminada con %d valor(es).",
            categoria["nombre_visible"], len(valores_ids),
        )
        return True, f"Categoría '{categoria['nombre_visible']}' eliminada correctamente."
    except sqlite3.Error as exc:
        conn.rollback()
        logger.warning("Error de BD al eliminar categoría id=%d: %s.", categoria_id, exc)
        return False, (
            "No se pudo eliminar la categoría porque los datos violan una "
            "restricción de la base de datos. Si el problema persiste, "
            f"contacta al administrador con este detalle: {exc}"
        )
    except Exception:
        conn.rollback()
        logger.exception("Error al eliminar categoría id=%d.", categoria_id)
        return False, (
            "Ocurrió un error inesperado al eliminar la categoría. El "
            "equipo técnico ya cuenta con el detalle en los registros del "
            "sistema; si el problema persiste, contacta al administrador."
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Campos personalizados (tablas EAV)
# ---------------------------------------------------------------------------

def obtener_valores_personalizados(componente: str, entidad_id: int) -> dict[int, int]:
    """Devuelve {categoria_id: valor_id} de campos personalizados guardados
    para un indicador (componente='indicador') o fuente (componente='fuente').
    """
    tabla = (
        "indicador_campos_personalizados"
        if componente == "indicador"
        else "fuente_campos_personalizados"
    )
    col_entidad = "indicador_id" if componente == "indicador" else "fuente_id"
    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    try:
        filas = cursor.execute(
            f"SELECT categoria_id, valor_id FROM {tabla} WHERE {col_entidad} = ?",
            (entidad_id,),
        ).fetchall()
        return {r[0]: r[1] for r in filas}
    finally:
        conn.close()


def guardar_campos_personalizados(
    componente: str,
    entidad_id: int,
    valores: dict | None,
    cursor=None,
) -> None:
    """Guarda (upsert) {categoria_id: valor_id} en la tabla EAV del componente.

    Si se pasa un ``cursor`` externo, participa de esa transacción y no hace
    commit (caller responsable). Si no, abre su propia conexión y transacción.
    """
    if not valores:
        return

    tabla = (
        "indicador_campos_personalizados"
        if componente == "indicador"
        else "fuente_campos_personalizados"
    )
    col_entidad = "indicador_id" if componente == "indicador" else "fuente_id"

    propio = cursor is None
    conn = None
    if propio:
        conn = db_mod.obtener_conexion()
        cursor = conn.cursor()
    try:
        for categoria_id, valor_id in valores.items():
            cursor.execute(
                f"""
                INSERT INTO {tabla} ({col_entidad}, categoria_id, valor_id) VALUES (?, ?, ?)
                ON CONFLICT({col_entidad}, categoria_id) DO UPDATE SET valor_id = excluded.valor_id
                """,
                (entidad_id, categoria_id, valor_id),
            )
        if propio:
            conn.commit()
    except Exception:
        if propio and conn:
            conn.rollback()
        raise
    finally:
        if propio and conn:
            conn.close()


def obtener_historial(
    auxiliar_id: int | None = None, categoria_clave: str | None = None
) -> list[dict]:
    """Devuelve el historial de cambios de Auxiliares, filtrado opcionalmente."""
    conn = db_mod.obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        if auxiliar_id is not None:
            filas = cursor.execute(
                """
                SELECT h.*, u.username AS usuario
                FROM auxiliares_historial h
                LEFT JOIN usuarios u ON u.id = h.usuario_id
                WHERE h.auxiliar_id = ?
                ORDER BY h.timestamp DESC
                """,
                (auxiliar_id,),
            ).fetchall()
        else:
            sql = """
                SELECT h.*, u.username AS usuario,
                       av.valor AS valor_actual, ac.nombre_visible AS categoria
                FROM auxiliares_historial h
                LEFT JOIN usuarios u ON u.id = h.usuario_id
                LEFT JOIN auxiliares_valores av ON av.id = h.auxiliar_id
                LEFT JOIN auxiliares_categorias ac ON ac.id = av.categoria_id
            """
            params: list = []
            if categoria_clave:
                sql += " WHERE ac.clave = ?"
                params.append(categoria_clave)
            sql += " ORDER BY h.timestamp DESC"
            filas = cursor.execute(sql, params).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()
