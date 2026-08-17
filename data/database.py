"""
data/database.py
================
Capa de acceso a datos: conexión, inicialización de esquema y migraciones
idempotentes de la base SQLite de SIDOE.

Convenciones
------------
- `obtener_conexion()` es la única puerta de entrada a la BD; nunca se
  instancia `sqlite3.connect()` directamente fuera de este módulo.
- Todas las migraciones usan `PRAGMA table_info()` antes de alterar el
  esquema para ser seguras ante reinicios repetidos.
- `inicializar_base_datos()` ejecuta el DDL inicial + todas las
  migraciones idempotentes + índices/vistas. Debe llamarse EXPLÍCITAMENTE
  una sola vez al arrancar la aplicación (ver app.py) — importar este
  módulo YA NO dispara el bootstrap como efecto secundario (Hallazgo #4
  del informe de revisión de código de agosto 2026; antes de este cambio,
  cualquier `import data.database` ejecutaba las migraciones contra la BD
  real). Los scripts utilitarios que necesiten garantizar que el esquema
  existe antes de operar (creación del admin inicial, ETL histórico desde
  el Excel oficial) la invocan explícitamente al arrancar — ver
  security/crear_admin.py y data/migraciones_historicas/ETL_migracion.py.
"""

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager

from config import DB_PATH
from utils.helpers import normalizar_titulo_indicador

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def obtener_conexion() -> sqlite3.Connection:
    """Devuelve una conexión SQLite con FK, WAL y busy_timeout activados.

    El busy_timeout se fija en dos niveles complementarios:
    - `timeout=3` en `sqlite3.connect()`: cuánto espera el driver de Python
      antes de lanzar `sqlite3.OperationalError: database is locked`.
    - `PRAGMA busy_timeout = 3000`: instruye al motor SQLite mismo a
      reintentar internamente hasta 3000ms antes de reportar el lock.
    Con ambos, si dos usuarios insertan/actualizan al mismo tiempo, la
    segunda conexión espera hasta 3 segundos a que la primera libere el
    lock en vez de fallar de inmediato.
    """
    conn = sqlite3.connect(DB_PATH, timeout=3)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 3000;")
    return conn


@contextmanager
def conexion_transaccional():
    """Context manager que abre una conexión, hace commit al salir sin
    errores, o rollback si hay excepción, y siempre cierra la conexión.

    Uso:
        with conexion_transaccional() as (conn, cursor):
            cursor.execute(...)
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Inicialización del esquema
# ---------------------------------------------------------------------------

def inicializar_tablas() -> None:
    """Crea todas las tablas y tablas de soporte si no existen todavía."""
    with conexion_transaccional() as (conn, cursor):

        # 1. indicadores — componente de demanda
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eje TEXT,
                politica_gobierno TEXT,
                generador_demanda TEXT CHECK(generador_demanda IN ('END','ODS','CMV','PNPSP')),
                codigo TEXT UNIQUE NOT NULL,
                estado_indicador TEXT NOT NULL DEFAULT 'Activo'
                    CHECK(estado_indicador IN ('Activo','Desactivado')),
                estado_publicacion TEXT NOT NULL DEFAULT 'publicado'
                    CHECK(estado_publicacion IN ('publicado','borrador')),
                indicadores_duplicados TEXT,
                indicador TEXT NOT NULL,
                titulo_normalizado TEXT,
                dominio_actividad_estadistica TEXT,
                subdominio_actividad_estadistica TEXT,
                area_misional_one TEXT,
                sector_ioe TEXT,
                requerimiento_clasificacion TEXT
                    CHECK(requerimiento_clasificacion IN ('No','Si','No identificada')),
                especificar_clasificacion TEXT,
                metodo_calculo TEXT
                    CHECK(metodo_calculo IN ('Definido','No identificado','No aplica','No','Por definir')),
                ficha_tecnica TEXT CHECK(ficha_tecnica IN ('No','Por definir','Definido')),
                numerador TEXT,
                denominador TEXT,
                unidad_medida TEXT,
                sexo TEXT CHECK(sexo IN ('No','No aplica','Sí','No identificado','No tiene meta data')),
                edad TEXT CHECK(edad IN ('No','No aplica','Sí','No identificado','No tiene meta data')),
                territorio TEXT
                    CHECK(territorio IN ('No','No aplica','Sí','No identificado','No tiene meta data')),
                discapacidad TEXT
                    CHECK(discapacidad IN ('No','No aplica','Sí','No identificado','No tiene meta data')),
                nivel_ingreso TEXT
                    CHECK(nivel_ingreso IN ('No','No aplica','Sí','No identificado','No tiene meta data')),
                periodicidad_indicador TEXT
                    CHECK(periodicidad_indicador IN
                        ('Anual','Bienal','Otros','Quinquenal','Semestral',
                         'No establecida','Trimestral','Mensual')),
                ente_responsable_metodologia TEXT,
                alcance_metodologico TEXT
                    CHECK(alcance_metodologico IN
                        ('Nacional','Internacional','Regional','No identificado','Por definir'))
            )
        """)

        # 2. fuentes_indicador — componente de oferta (1:N por indicador)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fuentes_indicador (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER NOT NULL,
                existencia_fuente TEXT
                    CHECK(existencia_fuente IN ('Completamente','No hay fuente','Parcialmente')),
                nombre_fuente TEXT,
                tipo_fuente TEXT
                    CHECK(tipo_fuente IN
                        ('Cuestionario global','No aplica','Registro administrativo','Encuesta','Otra')),
                institucion_productora TEXT,
                periodicidad TEXT
                    CHECK(periodicidad IN
                        ('Anual','No identificada','Bienal','Trimestral','Otros','Mensual',
                         'Quinquenal','No establecida','No aplica','Semestral')),
                sexo TEXT CHECK(sexo IN ('Si','No aplica','No identificado','No')),
                edad TEXT CHECK(edad IN ('Si','No aplica','No identificado','No')),
                territorio TEXT CHECK(territorio IN ('Si','No aplica','No identificado','No')),
                discapacidad TEXT CHECK(discapacidad IN ('Si','No aplica','No identificado','No')),
                nivel_ingreso_socioeconomico TEXT
                    CHECK(nivel_ingreso_socioeconomico IN ('Si','No aplica','No identificado','No')),
                ioe TEXT CHECK(ioe IN ('Si','No aplica','No')),
                ra TEXT CHECK(ra IN ('Si','No aplica','No')),
                calculado_datos_agregados TEXT
                    CHECK(calculado_datos_agregados IN
                        ('Calculado','Dato no disponible','Dato agregado')),
                hipervinculo_ultimo_calculo TEXT,
                anio_ultimo_dato_disponible TEXT,
                comentarios TEXT,
                FOREIGN KEY (indicador_id) REFERENCES indicadores(id) ON DELETE CASCADE
            )
        """)

        # 3. calculo_factibilidad — resultado del Engine (1:1 con indicadores)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calculo_factibilidad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER UNIQUE NOT NULL,
                c1_metodologia TEXT CHECK(c1_metodologia IN (
                    'Indicador con metodología nacional o internacional definida',
                    'Indicador sin metodología definida, pero el método de cálculo es auto explicativo',
                    'Indicador sin metodología definida, pero el método de cálculo se puede establecer mediante criterio experto.',
                    'Indicador sin metodología definida, pero el método se establece por criterio experto',
                    'No cumple con los criterios anteriores')),
                c21_existencia_fuente TEXT
                    CHECK(c21_existencia_fuente IN ('Completamente','Parcialmente','No hay fuente')),
                c22_disponibilidad TEXT CHECK(c22_disponibilidad IN ('Sí','No')),
                c23_periodicidad_establecida TEXT
                    CHECK(c23_periodicidad_establecida IN ('Sí','No','No requiere de articulación')),
                c31_posee_desagregacion TEXT
                    CHECK(c31_posee_desagregacion IN ('Sí','No','No es requerida')),
                num_desagregaciones_requeridas INTEGER DEFAULT 0,
                num_desagregaciones_disponibles INTEGER DEFAULT 0,
                articulacion_fuentes TEXT
                    CHECK(articulacion_fuentes IN
                        ('Sí se articula','No se articula','No requiere de articulación')),
                armonizacion_conceptual TEXT CHECK(armonizacion_conceptual IN ('Sí','No')),
                subregistro_cobertura TEXT CHECK(subregistro_cobertura IN ('Sí','No')),
                cobertura_territorial TEXT CHECK(cobertura_territorial IN ('Sí','No')),
                estructura_datos TEXT,
                variables_calculo TEXT
                    CHECK(variables_calculo IN ('Sí','No','No identificada','No requerida')),
                c1_valor REAL, c21_valor REAL, c22_valor REAL, c23_valor REAL,
                c31_valor REAL, c32_valor REAL, articulacion_valor REAL,
                armonizacion_valor REAL, subregistro_valor REAL, cobertura_valor REAL,
                estructura_valor REAL, variables_valor REAL,
                score_factibilidad_final REAL,
                categoria_factibilidad TEXT
                    CHECK(categoria_factibilidad IN
                        ('Factibilidad I','Factibilidad II','Factibilidad III')),
                calc_timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (indicador_id) REFERENCES indicadores(id) ON DELETE CASCADE
            )
        """)

        # 3b. indicador_ejes_politicas — pares Eje/Política adicionales (1:N)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicador_ejes_politicas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER NOT NULL,
                eje_id INTEGER,
                politica_gobierno_id INTEGER,
                FOREIGN KEY (indicador_id) REFERENCES indicadores(id) ON DELETE CASCADE,
                FOREIGN KEY (eje_id) REFERENCES auxiliares_valores(id),
                FOREIGN KEY (politica_gobierno_id) REFERENCES auxiliares_valores(id),
                UNIQUE(indicador_id, eje_id, politica_gobierno_id)
            )
        """)

        # 4. usuarios
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL CHECK(rol IN ('editor','supervisor','administrador')),
                activo INTEGER DEFAULT 1,
                fecha_creacion DATETIME DEFAULT (datetime('now'))
            )
        """)

        # 5. auditoria
        # usuario_id es NULLABLE: los intentos de login fallido no tienen un
        # usuario autenticado que asociar (ver auditar_login_fallido).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                accion TEXT NOT NULL,
                detalle TEXT,
                timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
        """)

        # 6. auxiliares_categorias — tipos de lista controlada
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auxiliares_categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clave TEXT UNIQUE NOT NULL,
                nombre_visible TEXT NOT NULL,
                descripcion TEXT,
                aplica_a TEXT CHECK(aplica_a IN ('indicador','fuente')),
                activo INTEGER NOT NULL DEFAULT 1,
                fecha_creacion DATETIME DEFAULT (datetime('now'))
            )
        """)

        # 7. auxiliares_valores — valores de cada categoría
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auxiliares_valores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                categoria_id INTEGER NOT NULL,
                valor TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_por INTEGER,
                fecha_creacion DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (categoria_id)
                    REFERENCES auxiliares_categorias(id) ON DELETE CASCADE,
                FOREIGN KEY (creado_por) REFERENCES usuarios(id),
                UNIQUE(categoria_id, valor)
            )
        """)

        # 8. auxiliares_historial — trazabilidad de cambios en Auxiliares
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auxiliares_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auxiliar_id INTEGER NOT NULL,
                accion TEXT NOT NULL
                    CHECK(accion IN
                        ('CREACION','RENOMBRADO','DESACTIVACION','ACTIVACION','ELIMINACION')),
                valor_anterior TEXT,
                valor_nuevo TEXT,
                usuario_id INTEGER,
                timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (auxiliar_id)
                    REFERENCES auxiliares_valores(id) ON DELETE CASCADE,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
        """)

        # 9-10. Tablas EAV para categorías PERSONALIZADAS (aplica_a IS NOT NULL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicador_campos_personalizados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER NOT NULL,
                categoria_id INTEGER NOT NULL,
                valor_id INTEGER,
                FOREIGN KEY (indicador_id)
                    REFERENCES indicadores(id) ON DELETE CASCADE,
                FOREIGN KEY (categoria_id)
                    REFERENCES auxiliares_categorias(id) ON DELETE CASCADE,
                FOREIGN KEY (valor_id) REFERENCES auxiliares_valores(id),
                UNIQUE(indicador_id, categoria_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fuente_campos_personalizados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fuente_id INTEGER NOT NULL,
                categoria_id INTEGER NOT NULL,
                valor_id INTEGER,
                FOREIGN KEY (fuente_id)
                    REFERENCES fuentes_indicador(id) ON DELETE CASCADE,
                FOREIGN KEY (categoria_id)
                    REFERENCES auxiliares_categorias(id) ON DELETE CASCADE,
                FOREIGN KEY (valor_id) REFERENCES auxiliares_valores(id),
                UNIQUE(fuente_id, categoria_id)
            )
        """)

    logger.info("Esquema de tablas verificado/inicializado correctamente.")


# ---------------------------------------------------------------------------
# Migraciones idempotentes
# ---------------------------------------------------------------------------

def _columnas_de(tabla: str) -> list[str]:
    """Devuelve los nombres de columna actuales de *tabla*."""
    with conexion_transaccional() as (conn, cursor):
        filas = cursor.execute(f"PRAGMA table_info({tabla})").fetchall()
        return [f[1] for f in filas]


def migrar_estado_indicador() -> None:
    """Agrega la columna estado_indicador a bases creadas antes de este campo."""
    if "estado_indicador" not in _columnas_de("indicadores"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute("""
                ALTER TABLE indicadores
                ADD COLUMN estado_indicador TEXT NOT NULL DEFAULT 'Activo'
                    CHECK(estado_indicador IN ('Activo','Desactivado'))
            """)
        logger.info("Migración aplicada: estado_indicador agregado a indicadores.")


def migrar_estado_publicacion() -> None:
    """Agrega la columna estado_publicacion a bases creadas antes de este campo.

    Default 'publicado' para todo lo existente: los indicadores ya migrados
    desde el Excel oficial se asumen listos para el público (a diferencia de
    estado_indicador, este campo no pretende gatear una revisión editorial
    pendiente, sino reflejar que la migración histórica ya fue validada).
    Independiente de estado_indicador: no toca la visibilidad interna.
    """
    if "estado_publicacion" not in _columnas_de("indicadores"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute("""
                ALTER TABLE indicadores
                ADD COLUMN estado_publicacion TEXT NOT NULL DEFAULT 'publicado'
                    CHECK(estado_publicacion IN ('publicado','borrador'))
            """)
        logger.info("Migración aplicada: estado_publicacion agregado a indicadores.")


def migrar_revision_pendiente() -> None:
    """Agrega a `indicadores` los metadatos de proceso que usa el flujo de
    aprobación del supervisor (ver `models/revision_pendiente.py`):

    - `revision_tipo`: 'nuevo' | 'actualizado' | NULL (NULL = nada pendiente
      de aprobar, o ya aprobado).
    - `revision_detalle`: JSON con la lista de campos que cambiaron desde
      la última vez que el indicador fue público, para que Aprobar
      Indicadores lo muestre sin obligar al supervisor a releer el
      formulario completo.
    - `revision_fecha`: cuándo se generó ese resumen (informativo).

    No son datos de negocio (no rompen la regla de "3 tablas de datos" —
    indicadores, fuentes_indicador, calculo_factibilidad): son metadatos de
    proceso sobre una fila de `indicadores` que ya existía, en el mismo
    espíritu que `estado_publicacion`.
    """
    columnas = _columnas_de("indicadores")
    faltantes = [
        c for c in ("revision_tipo", "revision_detalle", "revision_fecha")
        if c not in columnas
    ]
    if not faltantes:
        return
    with conexion_transaccional() as (conn, cursor):
        if "revision_tipo" not in columnas:
            cursor.execute("""
                ALTER TABLE indicadores ADD COLUMN revision_tipo TEXT
                    CHECK(revision_tipo IN ('nuevo','actualizado') OR revision_tipo IS NULL)
            """)
        if "revision_detalle" not in columnas:
            cursor.execute("ALTER TABLE indicadores ADD COLUMN revision_detalle TEXT")
        if "revision_fecha" not in columnas:
            cursor.execute("ALTER TABLE indicadores ADD COLUMN revision_fecha TEXT")
    logger.info("Migración aplicada: metadatos de revisión pendiente agregados a indicadores.")


def migrar_titulo_normalizado() -> None:
    """Agrega `titulo_normalizado` a `indicadores` y hace backfill del histórico.

    Ver Hallazgo 2 del informe de rendimiento de agosto 2026:
    `_sugerir_referencias_automaticas()` (models/crud_indicadores.py) traía
    toda la tabla `indicadores` a Python y comparaba título por título con
    `.strip().lower()` + colapso de espacios, porque esa normalización no
    tenía equivalente indexable en SQL. Esta columna guarda el título ya
    normalizado (misma función que usa la capa de escritura,
    `utils.helpers.normalizar_titulo_indicador`, para no duplicar la lógica)
    y se mantiene sincronizada en cada INSERT/UPDATE que toca `indicador`
    (ver `guardar_indicador`/`modificar_indicador` en
    models/crud_indicadores.py). No es una columna GENERATED de SQLite
    porque la normalización corre en Python, no en SQL.

    El índice correspondiente (`idx_indicadores_titulo_normalizado`) se crea
    en `crear_indices()`, después de esta migración, siguiendo el mismo
    orden que ya usa `estado_publicacion` (la columna debe existir antes de
    poder indexarla en bases que vienen de antes de este cambio).
    """
    if "titulo_normalizado" not in _columnas_de("indicadores"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute("ALTER TABLE indicadores ADD COLUMN titulo_normalizado TEXT")
        logger.info("Migración aplicada: titulo_normalizado agregado a indicadores.")

    # Backfill: solo toca filas que todavía no tienen el valor calculado.
    # Idempotente por diseño (WHERE titulo_normalizado IS NULL) — en una
    # base ya migrada esta consulta no trae filas y el loop no hace nada.
    with conexion_transaccional() as (conn, cursor):
        filas = cursor.execute(
            "SELECT id, indicador FROM indicadores WHERE titulo_normalizado IS NULL"
        ).fetchall()
        for row_id, indicador in filas:
            cursor.execute(
                "UPDATE indicadores SET titulo_normalizado = ? WHERE id = ?",
                (normalizar_titulo_indicador(indicador), row_id),
            )
    if filas:
        logger.info("Backfill titulo_normalizado: %d fila(s) actualizadas.", len(filas))


def migrar_rol_supervisor() -> None:
    """Agrega 'supervisor' al CHECK de usuarios.rol (reestructuración de roles,
    agosto-2026: se crea el rol supervisor y se le transfieren las
    responsabilidades de eliminar indicadores, aprobar su publicación y
    administrar Auxiliares — ver views/aprobar_indicadores.py y el resto de
    vistas afectadas).

    SQLite no permite ALTER de un CHECK existente: se reconstruye la tabla
    igual que ``migrar_fix_check_c1_metodologia()``, preservando todas las
    columnas agregadas por migraciones posteriores (TOTP, requiere_2fa).
    Debe ejecutarse DESPUÉS de esas migraciones en el bootstrap para que
    existan al reconstruir.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='usuarios'"
        ).fetchone()
        if not fila or "'supervisor'" in fila[0]:
            return

        conn.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute("ALTER TABLE usuarios RENAME TO usuarios_old_rolfix;")
        cursor.execute("""
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL CHECK(rol IN ('editor','supervisor','administrador')),
                activo INTEGER DEFAULT 1,
                fecha_creacion DATETIME DEFAULT (datetime('now')),
                totp_secret TEXT,
                totp_habilitado INTEGER NOT NULL DEFAULT 0,
                requiere_2fa INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute(
            "INSERT INTO usuarios "
            "(id, username, password_hash, rol, activo, fecha_creacion, "
            "totp_secret, totp_habilitado, requiere_2fa) "
            "SELECT id, username, password_hash, rol, activo, fecha_creacion, "
            "totp_secret, totp_habilitado, requiere_2fa FROM usuarios_old_rolfix;"
        )
        cursor.execute("DROP TABLE usuarios_old_rolfix;")
        conn.commit()
        logger.info("Migración aplicada: rol 'supervisor' agregado al CHECK de usuarios.")
    finally:
        conn.close()


def migrar_totp_usuarios() -> None:
    """Agrega columnas de 2FA (TOTP) a usuarios en bases creadas antes de
    esta funcionalidad.

    - ``totp_secret``: secreto base32 del usuario, NULL hasta que activa 2FA.
      Nunca se expone en consultas de listado (SELECT explícitos evitan
      traerlo salvo donde se necesita verificar/mostrar el QR de enrolamiento).
    - ``totp_habilitado``: 0/1. Si es 1, el login exige el segundo factor
      además de usuario/contraseña (ver security/auth.py y app.py).
    """
    columnas = _columnas_de("usuarios")
    with conexion_transaccional() as (conn, cursor):
        if "totp_secret" not in columnas:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN totp_secret TEXT")
        if "totp_habilitado" not in columnas:
            cursor.execute(
                "ALTER TABLE usuarios ADD COLUMN totp_habilitado INTEGER NOT NULL DEFAULT 0"
            )
    if "totp_secret" not in columnas or "totp_habilitado" not in columnas:
        logger.info("Migración aplicada: columnas TOTP (2FA) agregadas a usuarios.")


def migrar_requiere_2fa() -> None:
    """Agrega la columna ``requiere_2fa`` a usuarios en bases creadas antes
    de esta funcionalidad.

    Distinta de ``totp_habilitado`` (que indica si el usuario YA activó y
    confirmó su 2FA): ``requiere_2fa`` es un mandato del administrador —
    si está en 1 y el usuario todavía no tiene ``totp_habilitado``, el
    login (ver app.py) lo fuerza a completar el enrolamiento TOTP antes de
    dejarlo entrar, en vez de ofrecérselo como opcional. Default 0 para no
    afectar retroactivamente a nadie que ya tenga sesión funcionando.
    """
    if "requiere_2fa" not in _columnas_de("usuarios"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "ALTER TABLE usuarios ADD COLUMN requiere_2fa INTEGER NOT NULL DEFAULT 0"
            )
        logger.info("Migración aplicada: requiere_2fa agregado a usuarios.")


def migrar_contador_eliminaciones_supervisor() -> None:
    """Agrega ``usuarios.eliminaciones_recientes`` en bases creadas antes
    de esta funcionalidad (agosto-2026): cuenta cuántos indicadores ha
    eliminado un usuario con rol `supervisor` desde la última vez que se
    reseteó (al llegar al umbral se resetea a 0 y la cuenta se desactiva —
    ver ``models.crud_indicadores.borrar_indicador`` y
    ``config.UMBRAL_ELIMINACIONES_AUTOBLOQUEO``). Default 0: nadie parte
    ya "a mitad de camino" del límite al aplicar esta migración.
    """
    if "eliminaciones_recientes" not in _columnas_de("usuarios"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute(
                "ALTER TABLE usuarios ADD COLUMN eliminaciones_recientes "
                "INTEGER NOT NULL DEFAULT 0"
            )
        logger.info(
            "Migración aplicada: eliminaciones_recientes agregado a usuarios."
        )


def migrar_totp_codigos_respaldo() -> None:
    """Crea la tabla ``totp_codigos_respaldo`` si no existe (recovery codes 2FA).

    Un código de respaldo es una credencial de un solo uso que permite
    completar el segundo factor sin el código TOTP del teléfono, para el
    caso en que el usuario pierda o no tenga acceso a su dispositivo
    autenticador. Se generan en lote al activar 2FA (y pueden regenerarse
    desde el perfil) y se guardan hasheados con bcrypt — igual que las
    contraseñas, ver ``security.auth.hash_password`` — nunca en texto plano.

    ``ON DELETE CASCADE`` en ``usuario_id``: si se elimina un usuario, sus
    códigos de respaldo (ya inútiles) se eliminan con él.
    """
    with conexion_transaccional() as (conn, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS totp_codigos_respaldo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                codigo_hash TEXT NOT NULL,
                usado INTEGER NOT NULL DEFAULT 0,
                usado_en DATETIME,
                fecha_creacion DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            )
        """)
    logger.info("Migración verificada: tabla totp_codigos_respaldo disponible.")


def migrar_auxiliares_aplica_a() -> None:
    """Agrega la columna aplica_a a auxiliares_categorias si no existe."""
    if "aplica_a" not in _columnas_de("auxiliares_categorias"):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute("""
                ALTER TABLE auxiliares_categorias
                ADD COLUMN aplica_a TEXT CHECK(aplica_a IN ('indicador','fuente'))
            """)
        logger.info("Migración aplicada: aplica_a agregado a auxiliares_categorias.")


def migrar_campo_hibrido(
    tabla: str,
    columna: str,
    clave: str,
    nombre_visible: str,
    valores_iniciales: list[str],
) -> None:
    """Migración idempotente y genérica para campos categóricos al modelo híbrido.

    Agrega ``<columna>_id`` (FK a auxiliares_valores), siembra el catálogo y
    hace backfill de los registros existentes resolviendo su texto legado al ID
    correspondiente. La columna de texto original se conserva como respaldo.

    INVARIANTE DE SEGURIDAD (SQL): ``tabla`` y ``columna`` se interpolan
    directamente en SQL (SQLite no permite parametrizar identificadores). Es
    seguro porque esta función solo se invoca desde
    ``migrar_todos_los_campos_hibridos()`` con literales fijos y constantes de
    ``config.py`` — nunca con input de usuario.
    """
    columna_id = f"{columna}_id"
    if columna_id not in _columnas_de(tabla):
        with conexion_transaccional() as (conn, cursor):
            cursor.execute(f"""
                ALTER TABLE {tabla}
                ADD COLUMN {columna_id} INTEGER REFERENCES auxiliares_valores(id)
            """)

    with conexion_transaccional() as (conn, cursor):
        # Obtener o crear la categoría
        fila = cursor.execute(
            "SELECT id FROM auxiliares_categorias WHERE clave = ?", (clave,)
        ).fetchone()
        if fila:
            categoria_id = fila[0]
        else:
            cursor.execute(
                "INSERT INTO auxiliares_categorias (clave, nombre_visible) VALUES (?, ?)",
                (clave, nombre_visible),
            )
            categoria_id = cursor.lastrowid

        # Sembrar valores iniciales
        for valor in valores_iniciales:
            cursor.execute(
                "INSERT OR IGNORE INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
                (categoria_id, valor),
            )

        # Backfill: asignar _id a filas que aún tienen solo texto legado
        pendientes = cursor.execute(
            f"SELECT id, {columna} FROM {tabla} WHERE {columna_id} IS NULL AND {columna} IS NOT NULL"
        ).fetchall()

        for fila_id, texto in pendientes:
            r = cursor.execute(
                "SELECT id FROM auxiliares_valores WHERE categoria_id = ? AND LOWER(TRIM(valor)) = LOWER(TRIM(?))",
                (categoria_id, texto),
            ).fetchone()
            if not r:
                # Valor legado atípico: se agrega al catálogo para no perder el dato
                cursor.execute(
                    "INSERT OR IGNORE INTO auxiliares_valores (categoria_id, valor, activo) VALUES (?, ?, 1)",
                    (categoria_id, texto),
                )
                r = cursor.execute(
                    "SELECT id FROM auxiliares_valores WHERE categoria_id = ? AND LOWER(TRIM(valor)) = LOWER(TRIM(?))",
                    (categoria_id, texto),
                ).fetchone()
            if r:
                cursor.execute(
                    f"UPDATE {tabla} SET {columna_id} = ? WHERE id = ?",
                    (r[0], fila_id),
                )


def crear_indices() -> None:
    """Crea los índices de rendimiento sobre las columnas más consultadas.

    `CREATE INDEX IF NOT EXISTS` es naturalmente idempotente (no necesita
    pasar por `_migracion_ya_aplicada`). Se ejecuta después de las
    migraciones de columnas (en particular `migrar_estado_publicacion` y
    `migrar_titulo_normalizado`) porque los índices compuestos/dedicados
    dependen de que esas columnas ya existan en bases creadas antes de
    esos campos.

    Selección basada en el uso real observado en el código, no genérica:
    - fuentes_indicador.indicador_id: FK sin índice, se usa en el JOIN y
      en el subquery COUNT(*) de cada consulta en views/consultas.py.
    - indicadores(estado_indicador, estado_publicacion): este par exacto
      de filtros aparece en el WHERE de views/consultas.py,
      views/dashboard.py y views/generar_ficha.py.
    - indicadores.titulo_normalizado: usado por
      _sugerir_referencias_automaticas() en cada guardado/edición de
      indicador para detectar el mismo título bajo otro Generador de
      demanda, sin escanear la tabla completa (Hallazgo 2, informe de
      rendimiento agosto 2026).
    - auditoria.timestamp: ORDER BY DESC sin límite en views/ver_auditoria.py,
      tabla que solo crece con el tiempo.
    """
    with conexion_transaccional() as (conn, cursor):
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_fuentes_indicador_indicador_id
            ON fuentes_indicador(indicador_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_indicadores_estado_publicacion
            ON indicadores(estado_indicador, estado_publicacion)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_indicadores_titulo_normalizado
            ON indicadores(titulo_normalizado)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_auditoria_timestamp
            ON auditoria(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_totp_codigos_respaldo_usuario_id
            ON totp_codigos_respaldo(usuario_id, usado)
        """)
    logger.info("Índices de rendimiento verificados/creados correctamente.")


def _migracion_ya_aplicada(clave: str) -> bool:
    """Comprueba si una migración con esta clave ya fue ejecutada."""
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _migraciones_aplicadas (
                clave TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        return cursor.execute(
            "SELECT 1 FROM _migraciones_aplicadas WHERE clave = ?", (clave,)
        ).fetchone() is not None
    finally:
        conn.close()


def _marcar_migracion_aplicada(clave: str) -> None:
    """Registra que una migración ya fue ejecutada."""
    with conexion_transaccional() as (conn, cursor):
        cursor.execute(
            "INSERT OR IGNORE INTO _migraciones_aplicadas (clave) VALUES (?)", (clave,)
        )


def recalcular_todas_las_factibilidades() -> int:
    """Vuelve a correr el Engine sobre todos los registros de calculo_factibilidad.

    Es idempotente: el resultado es determinístico dado el mismo criterio de
    entrada. Se ejecuta una sola vez en bootstrap (controlado por
    _migraciones_aplicadas) para corregir los puntajes con el motor revisado.
    Devuelve el número de registros recalculados.
    """
    from features.engine_factibilidad import calcular_reglas_factibilidad  # import tardío

    # INVARIANTE DE SEGURIDAD (SQL): esta lista se interpola directamente en la
    # query de abajo (SELECT ... FROM calculo_factibilidad). Es segura porque
    # es un literal hardcodeado en esta misma función, nunca input de usuario.
    campos_criterio = [
        "c1_metodologia", "c21_existencia_fuente", "c22_disponibilidad",
        "c23_periodicidad_establecida", "c31_posee_desagregacion",
        "num_desagregaciones_requeridas", "num_desagregaciones_disponibles",
        "articulacion_fuentes", "armonizacion_conceptual", "subregistro_cobertura",
        "cobertura_territorial", "estructura_datos", "variables_calculo",
    ]

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        filas = cursor.execute(
            f"SELECT indicador_id, {', '.join(campos_criterio)} FROM calculo_factibilidad"
        ).fetchall()
        for fila in filas:
            datos = {campo: fila[campo] for campo in campos_criterio}
            resultado = calcular_reglas_factibilidad(datos)
            cursor.execute("""
                UPDATE calculo_factibilidad SET
                    c1_valor=?, c21_valor=?, c22_valor=?, c23_valor=?,
                    c31_valor=?, c32_valor=?, articulacion_valor=?,
                    armonizacion_valor=?, subregistro_valor=?,
                    cobertura_valor=?, estructura_valor=?, variables_valor=?,
                    score_factibilidad_final=?, categoria_factibilidad=?
                WHERE indicador_id=?
            """, (
                resultado["c1_valor"], resultado["c21_valor"], resultado["c22_valor"],
                resultado["c23_valor"], resultado["c31_valor"], resultado["c32_valor"],
                resultado["articulacion_valor"], resultado["armonizacion_valor"],
                resultado["subregistro_valor"], resultado["cobertura_valor"],
                resultado["estructura_valor"], resultado["variables_valor"],
                resultado["score_factibilidad_final"], resultado["categoria_factibilidad"],
                fila["indicador_id"],
            ))
        conn.commit()
        logger.info("Recálculo de factibilidad completado: %d registros.", len(filas))
        return len(filas)
    except Exception:
        conn.rollback()
        logger.exception("Error durante recalcular_todas_las_factibilidades.")
        raise
    finally:
        conn.close()


_C1_VARIANTE_CORRECTA = (
    "Indicador sin metodología definida, pero el método de cálculo se puede "
    "establecer mediante criterio experto."
)


def migrar_fix_check_c1_metodologia() -> None:
    """Reconstruye calculo_factibilidad si su CHECK de c1_metodologia está incompleto.

    Bases ya pobladas antes de julio-2026 podían tener el CHECK sin la
    variante 'mediante criterio experto.', lo que rechazaba INSERTs/UPDATEs
    con el texto correcto del Engine.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        fila = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='calculo_factibilidad'"
        ).fetchone()
        if not fila or _C1_VARIANTE_CORRECTA in fila[0]:
            return

        conn.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute(
            "ALTER TABLE calculo_factibilidad RENAME TO calculo_factibilidad_old_c1fix;"
        )
        cursor.execute("""
            CREATE TABLE calculo_factibilidad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicador_id INTEGER UNIQUE NOT NULL,
                c1_metodologia TEXT CHECK(c1_metodologia IN (
                    'Indicador con metodología nacional o internacional definida',
                    'Indicador sin metodología definida, pero el método de cálculo es auto explicativo',
                    'Indicador sin metodología definida, pero el método de cálculo se puede establecer mediante criterio experto.',
                    'Indicador sin metodología definida, pero el método se establece por criterio experto',
                    'No cumple con los criterios anteriores')),
                c21_existencia_fuente TEXT
                    CHECK(c21_existencia_fuente IN ('Completamente','Parcialmente','No hay fuente')),
                c22_disponibilidad TEXT CHECK(c22_disponibilidad IN ('Sí','No')),
                c23_periodicidad_establecida TEXT
                    CHECK(c23_periodicidad_establecida IN ('Sí','No','No requiere de articulación')),
                c31_posee_desagregacion TEXT
                    CHECK(c31_posee_desagregacion IN ('Sí','No','No es requerida')),
                num_desagregaciones_requeridas INTEGER DEFAULT 0,
                num_desagregaciones_disponibles INTEGER DEFAULT 0,
                articulacion_fuentes TEXT
                    CHECK(articulacion_fuentes IN
                        ('Sí se articula','No se articula','No requiere de articulación')),
                armonizacion_conceptual TEXT CHECK(armonizacion_conceptual IN ('Sí','No')),
                subregistro_cobertura TEXT CHECK(subregistro_cobertura IN ('Sí','No')),
                cobertura_territorial TEXT CHECK(cobertura_territorial IN ('Sí','No')),
                estructura_datos TEXT,
                variables_calculo TEXT
                    CHECK(variables_calculo IN ('Sí','No','No identificada','No requerida')),
                c1_valor REAL, c21_valor REAL, c22_valor REAL, c23_valor REAL,
                c31_valor REAL, c32_valor REAL, articulacion_valor REAL,
                armonizacion_valor REAL, subregistro_valor REAL, cobertura_valor REAL,
                estructura_valor REAL, variables_valor REAL,
                score_factibilidad_final REAL,
                categoria_factibilidad TEXT
                    CHECK(categoria_factibilidad IN
                        ('Factibilidad I','Factibilidad II','Factibilidad III')),
                calc_timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (indicador_id) REFERENCES indicadores(id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "INSERT INTO calculo_factibilidad SELECT * FROM calculo_factibilidad_old_c1fix;"
        )
        cursor.execute("DROP TABLE calculo_factibilidad_old_c1fix;")
        conn.commit()
        logger.info("Migración aplicada: CHECK de c1_metodologia corregido.")
    finally:
        conn.close()


def migrar_auditoria_usuario_id_nullable() -> None:
    """Permite NULL en auditoria.usuario_id para registrar logins fallidos.

    Bases creadas antes de esta corrección tenían ``usuario_id INTEGER NOT
    NULL`` con FK a usuarios(id). Como un intento de login fallido no tiene
    un usuario autenticado, ``auditar_login_fallido`` insertaba el sentinel
    ``usuario_id = 0``, que no existe en ``usuarios`` — la FK rechazaba el
    INSERT y la excepción se descartaba silenciosamente (nunca quedaba
    registro). Esta migración reconstruye la tabla con usuario_id nullable,
    preservando todos los registros existentes.
    """
    conn = obtener_conexion()
    cursor = conn.cursor()
    try:
        columnas = cursor.execute("PRAGMA table_info(auditoria)").fetchall()
        col_usuario = next((c for c in columnas if c[1] == "usuario_id"), None)
        if col_usuario is None or col_usuario[3] == 0:
            return  # ya nullable, o la tabla aún no existe

        conn.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute("ALTER TABLE auditoria RENAME TO auditoria_old_nullfix;")
        cursor.execute("""
            CREATE TABLE auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                accion TEXT NOT NULL,
                detalle TEXT,
                timestamp DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
        """)
        cursor.execute("""
            INSERT INTO auditoria (id, usuario_id, accion, detalle, timestamp)
            SELECT id, NULLIF(usuario_id, 0), accion, detalle, timestamp
            FROM auditoria_old_nullfix
        """)
        cursor.execute("DROP TABLE auditoria_old_nullfix;")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON;")
        logger.info("Migración aplicada: auditoria.usuario_id ahora acepta NULL.")
    finally:
        conn.close()


def migrar_limpiar_huerfanos_factibilidad() -> None:
    """Elimina filas huérfanas de calculo_factibilidad sin indicador asociado."""
    with conexion_transaccional() as (conn, cursor):
        cursor.execute(
            "DELETE FROM calculo_factibilidad "
            "WHERE indicador_id NOT IN (SELECT id FROM indicadores)"
        )
        if cursor.rowcount:
            logger.info(
                "Migración: eliminados %d huérfanos de calculo_factibilidad.",
                cursor.rowcount,
            )


def migrar_backfill_ejes_politicas() -> None:
    """Asegura que todo indicador tenga al menos una fila en indicador_ejes_politicas."""
    with conexion_transaccional() as (conn, cursor):
        cursor.execute("""
            INSERT OR IGNORE INTO indicador_ejes_politicas
                (indicador_id, eje_id, politica_gobierno_id)
            SELECT i.id, i.eje_id, i.politica_gobierno_id
            FROM indicadores i
            WHERE NOT EXISTS (
                SELECT 1 FROM indicador_ejes_politicas iep WHERE iep.indicador_id = i.id
            )
        """)


def migrar_normalizar_nombre_fuente_conocidos() -> None:
    """Normaliza variantes de escritura conocidas en fuentes_indicador.nombre_fuente
    ANTES de que migrar_campo_hibrido() cree el catálogo Auxiliar (punto 4).

    Diagnóstico (data/migraciones_historicas/diagnostico_normalizacion_p4.py, corrido contra una
    copia de la BD de producción): de los 8 grupos de valores con variantes
    de escritura detectados en institucion_productora/nombre_fuente, 7 son
    diferencias puramente de mayúsculas/minúsculas — esas ya las resuelve
    automáticamente el matching LOWER(TRIM()) de migrar_campo_hibrido() al
    hacer el backfill, sin necesidad de tocar nada aquí.

    La única excepción real (en el diagnóstico original) fue 'Sistema de
    Información de Gestión Financiera (SIGEF)': 15 filas la tenían guardada
    sin tildes ('informacion', 'gestion'), lo cual SÍ produce un valor
    distinto tras LOWER() y hubiera creado una entrada duplicada en el
    Auxiliar. Se normalizan esas filas a la forma con tildes (usada ya por
    la fila restante) antes del backfill.

    Re-ejecutado el diagnóstico el 2026-07-25 contra una copia más reciente
    de producción (855 indicadores / 1,077 fuentes) se detectó un segundo
    caso del mismo tipo: 'Informe General sobre Estadisticas de Educación
    Superior' (3 filas, sin tilde en "Estadisticas") vs 'Informe general
    sobre estadísticas de educación superior' (1 fila, con tilde). Se
    normaliza a la forma con tilde y con el estilo de capitalización
    mayoritario (Title Case), igual que el criterio usado para SIGEF.

    Diseñada para poder agregar más mapeos aquí si el diagnóstico detecta
    otros casos de acentos/ortografía en el futuro — no solo los de arriba.
    """
    mapeo_nombre_fuente = {
        "Sistema de informacion de gestion financiera (SIGEF)":
            "Sistema de Información de Gestión Financiera (SIGEF)",
        "Informe general sobre estadísticas de educación superior":
            "Informe General sobre Estadísticas de Educación Superior",
        "Informe General sobre Estadisticas de Educación Superior":
            "Informe General sobre Estadísticas de Educación Superior",
    }
    with conexion_transaccional() as (conn, cursor):
        for valor_legado, valor_canonico in mapeo_nombre_fuente.items():
            cursor.execute(
                "UPDATE fuentes_indicador SET nombre_fuente = ? "
                "WHERE nombre_fuente = ?",
                (valor_canonico, valor_legado),
            )
            if cursor.rowcount:
                logger.info(
                    "Migración: %d filas de nombre_fuente normalizadas de %r a %r.",
                    cursor.rowcount, valor_legado, valor_canonico,
                )


def _normalizar_sin_acentos(valor: str) -> str:
    """Forma normalizada para AGRUPAR (no para guardar): minúsculas, sin
    acentos, espacios múltiples colapsados. Espejo de la función homónima
    en data/migraciones_historicas/diagnostico_normalizacion_p4.py."""
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", valor)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.strip().lower())


def migrar_fusionar_duplicados_auxiliares_texto_libre() -> None:
    """Fusiona entradas duplicadas de auxiliares_valores para institucion_productora
    y nombre_fuente (punto 4) causadas por variantes de acentuación que el
    backfill original de migrar_campo_hibrido() no detectó.

    migrar_campo_hibrido() empareja valores legados con el catálogo usando
    LOWER(TRIM(valor)) — esto resuelve diferencias de mayúsculas/espacios,
    pero NO diferencias de acentuación ('Estadisticas' vs 'Estadísticas').
    Antes de que migrar_normalizar_nombre_fuente_conocidos() cubriera el
    caso 'Informe General sobre Estadisticas/Estadísticas de Educación
    Superior', el backfill ya había corrido en producción y creó DOS
    entradas de catálogo para ese mismo valor institucional (auxiliares_valores
    950 y 951), con 3 fuentes apuntando a una y 1 a la otra.

    Esta migración es el mecanismo de reparación general: agrupa las
    entradas de auxiliares_valores de cada categoría por forma normalizada
    (sin acentos/mayúsculas/espacios); si un grupo tiene más de una entrada,
    conserva la que tiene más filas de fuentes_indicador apuntándole
    (desempate por id menor), repunta las filas huérfanas hacia la
    sobreviviente, actualiza su texto tomándolo de la tabla origen (ya
    normalizada por migrar_normalizar_nombre_fuente_conocidos(), que corre
    antes en el bootstrap) y elimina las entradas perdedoras. Idempotente:
    si no hay duplicados no hace nada.
    """
    categorias_a_revisar = {
        "institucion_productora": ("fuentes_indicador", "institucion_productora_id"),
        "nombre_fuente": ("fuentes_indicador", "nombre_fuente_id"),
    }

    with conexion_transaccional() as (conn, cursor):
        for clave_categoria, (tabla, columna_id) in categorias_a_revisar.items():
            fila_categoria = cursor.execute(
                "SELECT id FROM auxiliares_categorias WHERE clave = ?",
                (clave_categoria,),
            ).fetchone()
            if not fila_categoria:
                continue
            categoria_id = fila_categoria[0]

            valores = cursor.execute(
                "SELECT id, valor FROM auxiliares_valores WHERE categoria_id = ?",
                (categoria_id,),
            ).fetchall()

            grupos: dict[str, list[tuple[int, str]]] = {}
            for valor_id, valor in valores:
                grupos.setdefault(_normalizar_sin_acentos(valor), []).append((valor_id, valor))

            for _, entradas in grupos.items():
                if len(entradas) <= 1:
                    continue

                conteos = {}
                for valor_id, _ in entradas:
                    conteos[valor_id] = cursor.execute(
                        f"SELECT COUNT(*) FROM {tabla} WHERE {columna_id} = ?",
                        (valor_id,),
                    ).fetchone()[0]

                id_sobreviviente = max(entradas, key=lambda e: (conteos[e[0]], -e[0]))[0]

                for valor_id, _ in entradas:
                    if valor_id == id_sobreviviente:
                        continue
                    cursor.execute(
                        f"UPDATE {tabla} SET {columna_id} = ? WHERE {columna_id} = ?",
                        (id_sobreviviente, valor_id),
                    )
                    cursor.execute(
                        "DELETE FROM auxiliares_valores WHERE id = ?", (valor_id,)
                    )
                    logger.info(
                        "Migración: fusionada entrada duplicada de Auxiliar '%s' "
                        "(id %d -> id %d).",
                        clave_categoria, valor_id, id_sobreviviente,
                    )

                # El texto canónico se toma de la tabla origen (ya normalizada
                # por migrar_normalizar_nombre_fuente_conocidos() antes de este
                # paso), no de auxiliares_valores: una tilde no cambia la
                # longitud del string, así que comparar por len() no detecta
                # de forma confiable cuál variante es la correcta.
                fila_texto = cursor.execute(
                    f"SELECT {clave_categoria} FROM {tabla} "
                    f"WHERE {columna_id} = ? LIMIT 1",
                    (id_sobreviviente,),
                ).fetchone()
                if fila_texto and fila_texto[0]:
                    cursor.execute(
                        "UPDATE auxiliares_valores SET valor = ? WHERE id = ?",
                        (fila_texto[0], id_sobreviviente),
                    )


def migrar_limpiar_stub_eje_legado() -> None:
    """Elimina las entradas huérfanas 'Eje 1'..'Eje 4' (sin el nombre
    descriptivo oficial) que quedaron en el catálogo Auxiliar 'eje' de
    instalaciones ya existentes, de cuando config.py sembraba esa forma
    corta en vez del nombre oficial completo ("Eje 1: Institucional", etc.).

    migrar_campo_hibrido() nunca lograba emparejar esos valores sembrados
    con el texto legado real de los indicadores (comparación exacta
    LOWER(TRIM()), y el Excel oficial siempre trae el nombre completo), así
    que terminaba creando una entrada nueva y dejando la corta huérfana —
    de ahí que el selectbox de "Eje" mostrara tanto "Eje 1" (sin usar) como
    "Eje 1: Institucional" (la real). config.py ya siembra directamente el
    valor oficial completo, así que esta migración es solo para limpiar
    instalaciones que ya tenían el catálogo poblado con la forma vieja.

    Por seguridad, solo elimina las entradas que en efecto no tienen ninguna
    fila apuntándolas (en indicadores.eje_id o indicador_ejes_politicas.eje_id);
    si alguna llegó a usarse de verdad, se deja intacta y se registra un
    warning para revisión manual en vez de arriesgar perder el dato.
    """
    stubs_legados = ("Eje 1", "Eje 2", "Eje 3", "Eje 4")

    with conexion_transaccional() as (conn, cursor):
        fila_categoria = cursor.execute(
            "SELECT id FROM auxiliares_categorias WHERE clave = 'eje'"
        ).fetchone()
        if not fila_categoria:
            return
        categoria_id = fila_categoria[0]

        candidatos = cursor.execute(
            "SELECT id, valor FROM auxiliares_valores "
            "WHERE categoria_id = ? AND valor IN (?, ?, ?, ?)",
            (categoria_id, *stubs_legados),
        ).fetchall()

        for valor_id, valor in candidatos:
            en_uso = cursor.execute(
                "SELECT (SELECT COUNT(*) FROM indicadores WHERE eje_id = ?) + "
                "(SELECT COUNT(*) FROM indicador_ejes_politicas WHERE eje_id = ?)",
                (valor_id, valor_id),
            ).fetchone()[0]
            if en_uso:
                logger.warning(
                    "Migración: la entrada obsoleta '%s' del catálogo 'eje' "
                    "(id %d) tiene %d referencia(s) reales — no se elimina, "
                    "revisar manualmente.",
                    valor, valor_id, en_uso,
                )
                continue
            cursor.execute("DELETE FROM auxiliares_valores WHERE id = ?", (valor_id,))
            logger.info(
                "Migración: eliminada entrada obsoleta '%s' (id %d) del "
                "catálogo Auxiliar 'eje' — sin referencias, duplicaba la "
                "versión oficial completa en el selectbox.",
                valor, valor_id,
            )


def migrar_todos_los_campos_hibridos() -> None:
    """Ejecuta migrar_campo_hibrido() para todos los campos declarados en config."""
    from config import CAMPOS_HIBRIDOS_INDICADORES, CAMPOS_HIBRIDOS_FUENTES  # import tardío

    for columna, clave, nombre, valores in CAMPOS_HIBRIDOS_INDICADORES:
        migrar_campo_hibrido("indicadores", columna, clave, nombre, valores)
    for columna, clave, nombre, valores in CAMPOS_HIBRIDOS_FUENTES:
        migrar_campo_hibrido("fuentes_indicador", columna, clave, nombre, valores)


def crear_vistas_resueltas() -> None:
    """(Re)crea las vistas SQL que resuelven los _id de Auxiliares a texto legible.

    - ``indicadores_resuelto``: una fila por indicador con todos los campos
      categóricos ya resueltos a texto (via JOIN a auxiliares_valores).
    - ``fuentes_resuelto``: ídem para fuentes_indicador.
    - ``ejes_politicas_por_indicador``: agrega todos los pares Eje/Política de
      un indicador en una sola fila usando GROUP_CONCAT.
    - ``ejes_politicas_secundarios_por_indicador``: igual, pero excluyendo el
      par principal (indicadores.eje_id/politica_gobierno_id) — la usa el
      export a Excel de Consultas para no repetir esa información.

    INVARIANTE DE SEGURIDAD (SQL): los nombres de columna/alias se interpolan
    directamente en los CREATE VIEW (SQLite no permite parametrizar
    identificadores). Es seguro porque provienen exclusivamente de
    ``CAMPOS_HIBRIDOS_INDICADORES`` / ``CAMPOS_HIBRIDOS_FUENTES`` en
    ``config.py``, definidas en tiempo de desarrollo — nunca de input de
    usuario.
    """
    from config import CAMPOS_HIBRIDOS_INDICADORES, CAMPOS_HIBRIDOS_FUENTES  # import tardío

    with conexion_transaccional() as (conn, cursor):

        # --- indicadores_resuelto ---
        cursor.execute("DROP VIEW IF EXISTS indicadores_resuelto")
        joins, selects_resueltos, selects_ids = [], [], []
        for columna, _clave, _nombre, _valores in CAMPOS_HIBRIDOS_INDICADORES:
            alias = f"av_{columna}"
            joins.append(
                f"LEFT JOIN auxiliares_valores {alias} ON {alias}.id = i.{columna}_id"
            )
            selects_resueltos.append(f"{alias}.valor AS {columna}")
            selects_ids.append(f"i.{columna}_id")
        columnas_hibridas_ind = {columna for columna, _, _, _ in CAMPOS_HIBRIDOS_INDICADORES}
        campos_base = [
            c for c in [
                "i.id", "i.codigo", "i.estado_indicador", "i.estado_publicacion",
                "i.indicadores_duplicados",
                "i.indicador", "i.area_misional_one", "i.especificar_clasificacion",
                "i.numerador", "i.denominador", "i.unidad_medida",
                "i.ente_responsable_metodologia",
            ]
            if c.split(".", 1)[1] not in columnas_hibridas_ind
        ]
        cursor.execute(f"""
            CREATE VIEW indicadores_resuelto AS
            SELECT {', '.join(campos_base + selects_resueltos + selects_ids)}
            FROM indicadores i
            {' '.join(joins)}
        """)

        # --- fuentes_resuelto ---
        cursor.execute("DROP VIEW IF EXISTS fuentes_resuelto")
        joins, selects_resueltos, selects_ids = [], [], []
        for columna, _clave, _nombre, _valores in CAMPOS_HIBRIDOS_FUENTES:
            alias = f"av_{columna}"
            joins.append(
                f"LEFT JOIN auxiliares_valores {alias} ON {alias}.id = f.{columna}_id"
            )
            selects_resueltos.append(f"{alias}.valor AS {columna}")
            selects_ids.append(f"f.{columna}_id")
        columnas_hibridas_f = {columna for columna, _, _, _ in CAMPOS_HIBRIDOS_FUENTES}
        campos_base_f = [
            c for c in [
                "f.id", "f.indicador_id", "f.nombre_fuente", "f.institucion_productora",
                "f.hipervinculo_ultimo_calculo", "f.anio_ultimo_dato_disponible",
                "f.comentarios",
            ]
            if c.split(".", 1)[1] not in columnas_hibridas_f
        ]
        cursor.execute(f"""
            CREATE VIEW fuentes_resuelto AS
            SELECT {', '.join(campos_base_f + selects_resueltos + selects_ids)}
            FROM fuentes_indicador f
            {' '.join(joins)}
        """)

        # --- ejes_politicas_por_indicador ---
        # Trae TODOS los pares Eje/Política (incluyendo el primero/legado) —
        # la usan generar_ficha.py y crud_indicadores.py, que sí necesitan
        # mostrar la lista completa sin perder ningún par.
        cursor.execute("DROP VIEW IF EXISTS ejes_politicas_por_indicador")
        cursor.execute("""
            CREATE VIEW ejes_politicas_por_indicador AS
            SELECT
                iep.indicador_id,
                GROUP_CONCAT(
                    DISTINCT (COALESCE(e.valor,'') || ' / ' || COALESCE(p.valor,''))
                ) AS ejes_politicas_todos,
                COUNT(*) AS num_ejes_politicas
            FROM indicador_ejes_politicas iep
            LEFT JOIN auxiliares_valores e ON e.id = iep.eje_id
            LEFT JOIN auxiliares_valores p ON p.id = iep.politica_gobierno_id
            GROUP BY iep.indicador_id
        """)

        # --- ejes_politicas_secundarios_por_indicador ---
        # Igual que la vista anterior, pero excluye el par que coincide con
        # el eje/política "principal" del indicador (indicadores.eje_id /
        # indicadores.politica_gobierno_id). La usa exclusivamente el export
        # a Excel de Consultas (columna 'otros_ejes_politicas'): como ese
        # export ya muestra el eje/política principal en sus propias
        # columnas, incluir también el par principal aquí duplicaría la
        # información para el caso común de un indicador con un solo eje.
        # Comparación por ID (no por texto) vía SQLite `IS`, que trata
        # NULL = NULL como verdadero.
        cursor.execute("DROP VIEW IF EXISTS ejes_politicas_secundarios_por_indicador")
        cursor.execute("""
            CREATE VIEW ejes_politicas_secundarios_por_indicador AS
            SELECT
                iep.indicador_id,
                GROUP_CONCAT(
                    DISTINCT (COALESCE(e.valor,'') || ' / ' || COALESCE(p.valor,''))
                ) AS otros_ejes_politicas
            FROM indicador_ejes_politicas iep
            JOIN indicadores i ON i.id = iep.indicador_id
            LEFT JOIN auxiliares_valores e ON e.id = iep.eje_id
            LEFT JOIN auxiliares_valores p ON p.id = iep.politica_gobierno_id
            WHERE NOT (
                iep.eje_id IS i.eje_id
                AND iep.politica_gobierno_id IS i.politica_gobierno_id
            )
            GROUP BY iep.indicador_id
        """)

    logger.info("Vistas resueltas (re)creadas correctamente.")


# ---------------------------------------------------------------------------
# Bootstrap de la aplicación
# ---------------------------------------------------------------------------

def inicializar_base_datos() -> None:
    """Ejecuta el DDL inicial + todas las migraciones idempotentes + la
    (re)creación de índices y vistas resueltas, en el mismo orden en que
    se ejecutaban antes a nivel de módulo.

    Debe llamarse explícitamente UNA SOLA VEZ al arrancar la aplicación
    (ver app.py) o desde un script utilitario que necesite garantizar que
    el esquema existe antes de operar (ver security/crear_admin.py,
    data/migraciones_historicas/ETL_migracion.py). NO se ejecuta
    automáticamente al importar este módulo (Hallazgo #4 del informe de
    revisión de código de agosto 2026): antes, cualquier `import
    data.database` -- incluso desde una consola interactiva, un script de
    documentación, o la importación perezosa de una vista durante un test
    -- disparaba las 18 migraciones contra la BD real como efecto
    secundario del import. Acoplar "migrar el esquema" a "importar el
    módulo" en vez de a "arrancar la aplicación" es justamente el
    antipatrón que este cambio corrige.

    Es segura de llamar más de una vez (todas las migraciones son
    idempotentes vía `_migracion_ya_aplicada`/`PRAGMA table_info`), pero
    el punto de entrada real (app.py) solo la invoca una vez por proceso.
    """
    inicializar_tablas()
    for _migracion in MIGRACIONES:
        _migracion()
    crear_indices()
    crear_vistas_resueltas()

    _clave_recalculo = "recalculo_factibilidad_engine_2026_07"
    if not _migracion_ya_aplicada(_clave_recalculo):
        recalcular_todas_las_factibilidades()
        _marcar_migracion_aplicada(_clave_recalculo)


# Lista explícita de migraciones, en el orden en que deben aplicarse.
# Registrar una migración nueva aquí (en vez de agregar una llamada suelta
# más abajo) elimina el riesgo de "olvidarla" en el bootstrap — el loop
# las ejecuta todas sin excepción (Hallazgo #6 del informe de revisión de
# código de agosto 2026).
MIGRACIONES: list[Callable[[], None]] = [
    migrar_estado_indicador,
    migrar_estado_publicacion,
    migrar_fix_check_c1_metodologia,
    migrar_limpiar_huerfanos_factibilidad,
    migrar_auxiliares_aplica_a,
    migrar_normalizar_nombre_fuente_conocidos,
    migrar_todos_los_campos_hibridos,
    migrar_fusionar_duplicados_auxiliares_texto_libre,
    migrar_limpiar_stub_eje_legado,
    migrar_backfill_ejes_politicas,
    migrar_auditoria_usuario_id_nullable,
    migrar_totp_usuarios,
    migrar_requiere_2fa,
    migrar_totp_codigos_respaldo,
    migrar_rol_supervisor,
    migrar_revision_pendiente,
    migrar_contador_eliminaciones_supervisor,
    migrar_titulo_normalizado,
]

if __name__ == "__main__":
    print("Tablas inicializadas correctamente en", DB_PATH)
