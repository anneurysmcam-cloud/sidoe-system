"""
config.py
=========
Fuente única de verdad para la configuración global de SIDOE.

Todos los demás módulos importan desde aquí; nada de constantes dispersas
en varios archivos.
"""

import os

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

# Raíz del proyecto (directorio que contiene este archivo)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Nombre del ambiente actual (dev/staging/produccion). Informativo por ahora
# — no cambia comportamiento por sí solo, pero queda disponible para logging
# o para futuras ramas de configuración por ambiente.
SIDOE_ENV: str = os.environ.get("SIDOE_ENV", "dev")

# Ruta a la base de datos SQLite. Configurable vía SIDOE_DB_PATH para
# permitir alternar dev/staging/producción sin editar código fuente; si la
# variable de entorno no está definida, el comportamiento es idéntico al
# actual (ruta fija junto a este archivo).
DB_PATH = os.environ.get("SIDOE_DB_PATH", os.path.join(BASE_DIR, "sidoe.db"))

# ---------------------------------------------------------------------------
# Umbrales de factibilidad (replicados 1:1 del Excel oficial ONE)
# ---------------------------------------------------------------------------

UMBRAL_ALTA: float = 91.0   # score >= 91  → Factibilidad I
UMBRAL_MEDIA: float = 70.0  # score >= 70  → Factibilidad II
# score < 70 ó sin datos → Factibilidad III

CAT_I: str = "Factibilidad I"
CAT_II: str = "Factibilidad II"
CAT_III: str = "Factibilidad III"

# ---------------------------------------------------------------------------
# Auto-bloqueo de supervisor por eliminaciones (agosto-2026)
# ---------------------------------------------------------------------------
# Salvaguarda contra eliminación masiva accidental (o de una cuenta
# comprometida): al llegar a este número de indicadores eliminados por un
# mismo usuario con rol `supervisor`, su cuenta se desactiva automáticamente
# y el contador se resetea a 0 — la siguiente tanda de eliminaciones, tras
# la reactivación, vuelve a exigir otras UMBRAL_ELIMINACIONES_AUTOBLOQUEO
# antes de bloquearse de nuevo. Ver models.crud_indicadores.borrar_indicador
# y DESPLIEGUE_PRODUCCION.md para el protocolo de eliminación masiva vía TI
# que evita disparar este límite.
# Configurable vía SIDOE_UMBRAL_ELIMINACIONES_AUTOBLOQUEO — default idéntico
# al valor fijo anterior si la variable de entorno no está definida.
UMBRAL_ELIMINACIONES_AUTOBLOQUEO: int = int(
    os.environ.get("SIDOE_UMBRAL_ELIMINACIONES_AUTOBLOQUEO", "5")
)

# ---------------------------------------------------------------------------
# Estados de indicador
# ---------------------------------------------------------------------------

ESTADO_ACTIVO = "Activo"
ESTADO_DESACTIVADO = "Desactivado"
ESTADOS_INDICADOR = [ESTADO_ACTIVO, ESTADO_DESACTIVADO]

# ---------------------------------------------------------------------------
# Estados de publicación (visibilidad pública, independiente de estado_indicador)
# ---------------------------------------------------------------------------
# 'borrador'/'publicado' controla únicamente si un indicador es visible en la
# vista pública sin sesión (modo público, sin rol de login asociado). No
# afecta la visibilidad interna para editor/administrador, que sigue
# gobernada solo por estado_indicador.
# Default 'publicado': se asume que todo indicador migrado o creado ya está
# listo para el público; editor/administrador pueden pasarlo a 'borrador'
# explícitamente si necesitan ocultarlo mientras lo corrigen.

ESTADO_PUBLICACION_BORRADOR = "borrador"
ESTADO_PUBLICACION_PUBLICADO = "publicado"
ESTADOS_PUBLICACION = [ESTADO_PUBLICACION_PUBLICADO, ESTADO_PUBLICACION_BORRADOR]

# ---------------------------------------------------------------------------
# Vocabulario oficial de Factibilidad (C1–C3.2) — para los selectbox de UI
# ---------------------------------------------------------------------------
# Fuente única para las opciones que views/crear_indicador.py y
# views/actualizar_indicador.py ofrecen en sus selectbox de criterios C1-C3.2
# (antes vivían como listas literales duplicadas, byte-a-byte idénticas, en
# ambos archivos). NO se administran desde Auxiliares (ver nota en
# CAMPOS_HIBRIDOS_* más abajo): son el vocabulario fijo de la fórmula oficial.
#
# ⚠️ ADVERTENCIA DE TRIPLICACIÓN NO RESUELTA: features/engine_factibilidad.py
# mantiene su PROPIA copia de este mismo vocabulario como claves de sus
# `_C1_MAP`, `_C21_MAP`, `_C22_MAP`, `_C23_MAP`, `_C31_MAP`,
# `_USO_CLASIF_MAP` (más comparaciones inline para articulación/armonización/
# subregistro/cobertura/estructura_datos). Verificado julio-2026: las tres
# copias coinciden exactamente. Unificarlas requeriría tocar la estructura
# interna del Engine (ya validado contra 868/880 filas del Excel oficial),
# lo cual queda fuera de alcance de esta extracción — se documenta aquí para
# que un cambio futuro de vocabulario no se aplique solo en un lugar. Si se
# edita cualquiera de estas listas, hay que editar también el módulo del
# Engine en el mismo commit.

OPCIONES_SI_NO = ["Sí", "No"]  # C2.2, C2.3, Armonización, Subregistro, Cobertura

OPCIONES_C1_METODOLOGIA = [
    "Indicador con metodología nacional o internacional definida",
    "Indicador sin metodología definida, pero el método de cálculo es auto explicativo",
    "Indicador sin metodología definida, pero el método de cálculo se puede establecer "
    "mediante criterio experto.",
    "No cumple con los criterios anteriores",
]

OPCIONES_C21_EXISTENCIA_FUENTE = ["Completamente", "Parcialmente", "No hay fuente"]

OPCIONES_C31_DESAGREGACION = ["Sí", "No", "No es requerida"]

OPCIONES_ARTICULACION_FUENTES = [
    "Sí se articula", "No se articula", "No requiere de articulación",
]

OPCIONES_ESTRUCTURA_DATOS = [
    "a) La fuente de información utiliza en el procesamiento una base de datos estructurada",
    "b) No posee una base de datos estructurada, pero posee un formato para montar datos (Excel)",
    "No posee ninguna de las anteriores",
]

OPCIONES_VARIABLES_CALCULO = ["Sí", "No", "No identificada", "No requerida"]

# ---------------------------------------------------------------------------
# Campos híbridos — catálogos controlados del modelo híbrido
# ---------------------------------------------------------------------------
# Formato de cada tupla:
#   (columna_base, clave_auxiliar, nombre_visible, [valores_iniciales])
#
# ⚠️ El Engine de factibilidad usa cadenas EXACTAS del Excel oficial. Los
# criterios C1–C3.2 NO aparecen en estas listas: sus valores nunca deben
# estar sujetos a renombrado por Auxiliares, ya que eso rompería el scoring.

CAMPOS_HIBRIDOS_INDICADORES: list[tuple] = [
    ("generador_demanda", "generador_demanda", "Generador de Demanda",
     ["END", "ODS", "CMV", "PNPSP"]),
    # Los 4 valores oficiales del Excel matriz (hoja "Factibilidad", columna
    # Eje) siempre llevan el nombre descriptivo ("Eje 1: Institucional", no
    # "Eje 1" a secas). Antes se sembraba aquí la forma corta ["Eje 1", ...],
    # que migrar_campo_hibrido() nunca lograba emparejar con el texto legado
    # real (comparación exacta LOWER(TRIM())), así que terminaba creando UNA
    # entrada extra por eje en el catálogo — de ahí que el selectbox mostrara
    # tanto "Eje 1" (huérfano, sin usar) como "Eje 1: Institucional" (el
    # real). Se sembra directamente con el nombre oficial completo para que
    # no haya nada que desduplicar. "No identificado" también aparece en el
    # Excel oficial para indicadores sin eje asignado.
    ("eje", "eje", "Eje",
     ["Eje 1: Institucional", "Eje 2: Social", "Eje 3: Productivo",
      "Eje 4: Ambiental", "No identificado"]),
    ("politica_gobierno", "politica_gobierno", "Política de Gobierno",
     ["Política 1.1", "Política 1.2", "Política 2.1"]),
    ("dominio_actividad_estadistica", "dominio_actividad_estadistica",
     "Dominio de Actividad Estadística",
     ["Demografía y Población", "Economía", "Educación", "Salud", "Medio Ambiente",
      "Infraestructura y Vivienda", "Justicia y Seguridad", "Trabajo", "Sociedad"]),
    ("subdominio_actividad_estadistica", "subdominio_actividad_estadistica",
     "Sub-Dominio de Actividad Estadística",
     ["Fecundidad", "Mortalidad", "Migración", "Movimiento natural de la Población",
      "Proyecciones Poblacionales", "Vivienda"]),
    ("sector_ioe", "sector_ioe", "Sector IOE",
     ["Administración Pública", "Salud", "Educación",
      "Economía y Finanzas", "Trabajo y Seguridad Social",
      "Ambiente y Recursos Naturales", "Infraestructura",
      "Justicia y Seguridad", "Social"]),
    ("requerimiento_clasificacion", "requerimiento_clasificacion",
     "Requerimiento de Clasificación",
     ["No", "Si", "No identificada"]),
    ("metodo_calculo", "metodo_calculo", "Método de Cálculo",
     ["Definido", "No identificado", "No aplica", "No", "Por definir"]),
    ("ficha_tecnica", "ficha_tecnica", "Ficha Técnica",
     ["No", "Por definir", "Definido"]),
    ("sexo", "sexo_indicador", "Sexo (Indicador)",
     ["No", "No aplica", "Sí", "No identificado", "No tiene meta data"]),
    ("edad", "edad_indicador", "Edad (Indicador)",
     ["No", "No aplica", "Sí", "No identificado", "No tiene meta data"]),
    ("territorio", "territorio_indicador", "Territorio (Indicador)",
     ["No", "No aplica", "Sí", "No identificado", "No tiene meta data"]),
    ("discapacidad", "discapacidad_indicador", "Discapacidad (Indicador)",
     ["No", "No aplica", "Sí", "No identificado", "No tiene meta data"]),
    ("nivel_ingreso", "nivel_ingreso_indicador", "Nivel de Ingreso (Indicador)",
     ["No", "No aplica", "Sí", "No identificado", "No tiene meta data"]),
    ("periodicidad_indicador", "periodicidad_indicador", "Periodicidad del Indicador",
     ["Anual", "Bienal", "Otros", "Quinquenal", "Semestral",
      "No establecida", "Trimestral", "Mensual"]),
    ("alcance_metodologico", "alcance_metodologico", "Alcance Metodológico",
     ["Nacional", "Internacional", "Regional", "No identificado", "Por definir"]),
    # Punto 4: convertido de texto libre a catálogo controlado. Sin valores
    # iniciales explícitos a propósito — el catálogo se puebla por completo
    # desde los ~7 valores ya existentes en producción vía el backfill de
    # migrar_campo_hibrido() (matching case-insensitive), no hace falta
    # retipearlos aquí.
    ("area_misional_one", "area_misional_one", "Área Misional ONE", []),
]

CAMPOS_HIBRIDOS_FUENTES: list[tuple] = [
    ("existencia_fuente", "existencia_fuente", "Existencia de Fuente",
     ["Completamente", "No hay fuente", "Parcialmente"]),
    ("tipo_fuente", "tipo_fuente", "Tipo de Fuente",
     ["Cuestionario global", "No aplica", "Registro administrativo", "Encuesta", "Otra"]),
    ("periodicidad", "periodicidad_fuente", "Periodicidad de la Fuente",
     ["Anual", "No identificada", "Bienal", "Trimestral", "Otros",
      "Mensual", "Quinquenal", "No establecida", "No aplica", "Semestral"]),
    ("sexo", "sexo_fuente", "Sexo (Fuente)",
     ["Si", "No aplica", "No identificado", "No"]),
    ("edad", "edad_fuente", "Edad (Fuente)",
     ["Si", "No aplica", "No identificado", "No"]),
    ("territorio", "territorio_fuente", "Territorio (Fuente)",
     ["Si", "No aplica", "No identificado", "No"]),
    ("discapacidad", "discapacidad_fuente", "Discapacidad (Fuente)",
     ["Si", "No aplica", "No identificado", "No"]),
    ("nivel_ingreso_socioeconomico", "nivel_ingreso_fuente", "Nivel de Ingreso (Fuente)",
     ["Si", "No aplica", "No identificado", "No"]),
    ("ioe", "ioe_fuente", "IOE",
     ["Si", "No aplica", "No"]),
    ("ra", "ra_fuente", "RA",
     ["Si", "No aplica", "No"]),
    ("calculado_datos_agregados", "calculado_datos_agregados",
     "Calculado / Dato Agregado",
     ["Calculado", "Dato no disponible", "Dato agregado"]),
    # Punto 4: convertidos de texto libre a catálogo controlado. Sin valores
    # iniciales explícitos a propósito — a diferencia de los campos de arriba
    # (enumeraciones cerradas de pocas opciones), estos son catálogos amplios
    # y orgánicos (~124 y ~259 valores distintos en producción); se pueblan
    # por completo desde los datos existentes vía el backfill de
    # migrar_campo_hibrido() (matching case-insensitive). El único conflicto
    # real de escritura detectado en el diagnóstico (acentos faltantes en
    # 'SIGEF') se corrige antes, en migrar_normalizar_nombre_fuente_conocidos().
    ("institucion_productora", "institucion_productora", "Institución Productora", []),
    ("nombre_fuente", "nombre_fuente", "Nombre de la Fuente", []),
]
