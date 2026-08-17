"""
data/migraciones_historicas/ETL_migracion.py
=============================================
Migración masiva del Excel oficial MOYD 2026 hacia las tablas normalizadas.

Reubicado desde ``data/ETL_migracion.py`` a ``data/migraciones_historicas/``
junto con los demás scripts de un solo uso (Hallazgo #10 del informe de
revisión de código de agosto 2026).

Ejecutar UNA SOLA VEZ sobre una base de datos vacía:
    python -m data.migraciones_historicas.ETL_migracion

O desde la raíz del proyecto:
    python data/migraciones_historicas/ETL_migracion.py

⚠️  Para migrar desde cero, asegurarse de que la base de datos no tenga datos.
    El ETL es idempotente para PNPSP (salta códigos ya existentes), pero la
    función principal migrar_historico_excel() puede generar duplicados si se
    ejecuta dos veces. Usar con precaución en producción.
"""

import logging
import os

import numpy as np
import pandas as pd

from data import database as db_mod
from models.crud_auxiliares import resolver_o_crear_id
from models.crud_indicadores import guardar_indicador

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalizadores de vocabulario (texto Excel → vocabulario SIDOE)
# ---------------------------------------------------------------------------

def _str(val, default: str = "") -> str:
    """Convierte un valor Excel a str limpio; devuelve default si NaN/None."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return str(val).strip()


def _int(val, default: int = 0) -> int:
    """Convierte un valor Excel a int; devuelve default si no es numérico."""
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _desag_ind(val) -> str:
    """Normaliza valores de desagregación de indicador."""
    mapa = {
        "si": "Sí", "sí": "Sí", "no": "No", "no aplica": "No aplica",
        "no identificado": "No identificado", "no tiene meta data": "No tiene meta data",
    }
    return mapa.get(_str(val).lower(), "No")


def _desag_fuente(val) -> str:
    """Normaliza valores de desagregación de fuente."""
    mapa = {
        "si": "Si", "sí": "Si", "no": "No",
        "no aplica": "No aplica", "no identificado": "No identificado",
    }
    return mapa.get(_str(val).lower(), "No")


def _ioe(val) -> str:
    """Normaliza IOE/RA."""
    mapa = {"sí": "Si", "si": "Si", "no": "No", "no aplica": "No aplica"}
    return mapa.get(_str(val).lower(), "No")


def _ficha(val) -> str:
    """Normaliza texto de ficha técnica."""
    mapa = {"definida": "Definido", "definido": "Definido", "por definir": "Por definir", "no": "No"}
    return mapa.get(_str(val).lower(), "No")


def _tipo_fuente(val) -> str:
    """Normaliza tipo de fuente."""
    mapa = {
        "cuestionario global": "Cuestionario global",
        "encuesta": "Encuesta",
        "registro administrativo": "Registro administrativo",
        "otra": "Otra",
        "no identificado": "Otra",
        "no aplica": "No aplica",
    }
    return mapa.get(_str(val).lower(), "Otra")


def _req_clas(val) -> str:
    """Normaliza requerimiento de clasificación."""
    mapa = {"sí": "Si", "si": "Si", "no": "No", "no identificada": "No identificada"}
    return mapa.get(_str(val).lower(), "No")


def _c22(val) -> str:
    """Normaliza C2.2 disponibilidad/accesibilidad (solo Sí/No según Excel oficial)."""
    mapa = {"sí": "Sí", "si": "Sí", "no": "No"}
    return mapa.get(_str(val).lower(), "No")


def _uso_clasificaciones(val) -> str:
    """Normaliza Uso de Clasificaciones (sustituye "Variables para cálculo")."""
    mapa = {
        "sí": "Sí", "si": "Sí", "no": "No",
        "no identificada": "No identificada", "no identificado": "No identificada",
        "no requerida": "No requerida", "no requerido": "No requerida",
    }
    return mapa.get(_str(val).lower(), "No")


# ---------------------------------------------------------------------------
# Funciones principales de migración
# ---------------------------------------------------------------------------

def _resolver_excel(ruta_parametro: str | None) -> str | None:
    """Busca el archivo Excel oficial en la raíz del proyecto."""
    if ruta_parametro and os.path.exists(ruta_parametro):
        return ruta_parametro
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for nombre in [
        "OFICIAL MATRIZ DE OFERTA Y DEMANDA 2026 1.xlsx",
        "OFICIAL_MATRIZ_DE_OFERTA_Y_DEMANDA_2026_1.xlsx",
        "OFICIAL MATRIZ DE OFERTA Y DEMANDA 2026.xlsx",
        "OFICIAL_MATRIZ_DE_OFERTA_Y_DEMANDA_2026.xlsx",
    ]:
        candidato = os.path.join(raiz, nombre)
        if os.path.exists(candidato):
            return candidato
    return None


def migrar_historico_excel(archivo_excel: str | None = None) -> None:
    """Migra los indicadores del Excel oficial (END/ODS/CMV) a la base de datos.

    ⚠️  No es idempotente: ejecutar dos veces puede duplicar datos.
    """
    logger.info("Iniciando migración masiva (END/ODS/CMV)...")
    ruta = _resolver_excel(archivo_excel)
    if not ruta:
        logger.error("No se encontró el archivo Excel oficial.")
        return

    df_dem = pd.read_excel(ruta, sheet_name="Demanda y Oferta", header=2)
    df_fac = pd.read_excel(ruta, sheet_name="Factibilidad", header=3)
    df_dem.columns = df_dem.columns.str.strip()
    df_fac.columns = df_fac.columns.str.strip()
    df_dem["Código"] = df_dem["Código"].fillna("").astype(str).str.strip()
    df_fac["Código"] = df_fac["Código"].fillna("").astype(str).str.strip()

    cod_dem_validos = {c for c in df_dem["Código"].unique() if c.upper() not in ("NA", "NAN", "")}
    cod_fac_validos = {c for c in df_fac["Código"].unique() if c.upper() not in ("NA", "NAN", "")}
    huerfanos_fac = sorted(cod_fac_validos - cod_dem_validos)
    huerfanos_dem = sorted(cod_dem_validos - cod_fac_validos)
    if huerfanos_fac:
        logger.warning(
            "%d código(s) presentes en Factibilidad pero SIN correspondencia en Demanda y Oferta "
            "(se perderán silenciosamente en el merge inner): %s",
            len(huerfanos_fac), huerfanos_fac,
        )
    if huerfanos_dem:
        logger.warning(
            "%d código(s) presentes en Demanda y Oferta pero SIN correspondencia en Factibilidad "
            "(se perderán silenciosamente en el merge inner): %s",
            len(huerfanos_dem), huerfanos_dem,
        )

    df = pd.merge(df_fac, df_dem, on="Código", suffixes=("", "_dem"))
    df = df[~df["Código"].str.upper().isin(["NA", "NAN", ""])]
    logger.info("Registros a procesar: %d", len(df))

    ok = errores = 0
    for codigo, grupo in df.groupby("Código", sort=False):
        f = grupo.iloc[0]
        nombre = _str(f.get("Indicador"))
        if not codigo or not nombre or nombre.upper() == "NAN":
            continue

        sector_fac = _str(f.get("Sector IOE"))
        sector_dem = _str(f.get("Sector IOE_dem"))
        if sector_fac and sector_dem and sector_fac != sector_dem:
            logger.warning(
                "[%s] 'Sector IOE' difiere entre hojas: Factibilidad='%s' vs Demanda='%s'. "
                "Se usará el valor de Factibilidad.",
                codigo, sector_fac, sector_dem,
            )

        req = _int(f.get("Numero de desagregaciones requeridas por el indicador"))
        disp = _int(f.get("Numero de desagregaciones disponibles en la fuente"))
        if req and disp > req:
            logger.warning(
                "[%s] Desagregaciones disponibles (%d) mayor que requeridas (%d) en el Excel "
                "origen. Revisar carga de datos con ONE.",
                codigo, disp, req,
            )

        if len(grupo) > 1:
            logger.info("[%s] %d fuentes detectadas, se migran todas.", codigo, len(grupo))

        datos_indicador = {
            "codigo":                             codigo,
            "eje_id":                             resolver_o_crear_id("eje", f.get("Eje"), valor_por_defecto="No identificado"),
            "politica_gobierno_id":               resolver_o_crear_id("politica_gobierno", f.get("Politica de gobierno")),
            "generador_demanda_id":               resolver_o_crear_id("generador_demanda", f.get("Generador de demanda")),
            "indicador":                          nombre,
            "dominio_actividad_estadistica_id":   resolver_o_crear_id("dominio_actividad_estadistica", f.get("Dominio actividad estadistica")),
            "subdominio_actividad_estadistica_id":resolver_o_crear_id("subdominio_actividad_estadistica", f.get("Sub-Dominio actividad estadistica")),
            "area_misional_one":                  _str(f.get("Area misional ONE")),
            "sector_ioe_id":                      resolver_o_crear_id("sector_ioe", f.get("Sector IOE")),
            "requerimiento_clasificacion_id":     resolver_o_crear_id("requerimiento_clasificacion", _req_clas(f.get("Requerimiento de clasificacion"))),
            "especificar_clasificacion":          _str(f.get("Especificar clasificacion")),
            "metodo_calculo_id":                  resolver_o_crear_id("metodo_calculo", f.get("Metodo de calculo"), valor_por_defecto="No identificado"),
            "ficha_tecnica_id":                   resolver_o_crear_id("ficha_tecnica", _ficha(f.get("Ficha tecnica"))),
            "numerador":                          _str(f.get("Numerador")),
            "denominador":                        _str(f.get("Denominador")),
            "unidad_medida":                      _str(f.get("Unidad de medida")),
            "sexo_id":                            resolver_o_crear_id("sexo_indicador", _desag_ind(f.get("Sexo"))),
            "edad_id":                            resolver_o_crear_id("edad_indicador", _desag_ind(f.get("Edad"))),
            "territorio_id":                      resolver_o_crear_id("territorio_indicador", _desag_ind(f.get("Territorio"))),
            "discapacidad_id":                    resolver_o_crear_id("discapacidad_indicador", _desag_ind(f.get("Discapacidad"))),
            "nivel_ingreso_id":                   resolver_o_crear_id("nivel_ingreso_indicador", _desag_ind(f.get("Nivel de ingreso"))),
            "periodicidad_indicador_id":          resolver_o_crear_id("periodicidad_indicador", f.get("Periodicidad del indicador"), valor_por_defecto="No establecida"),
            "ente_responsable_metodologia":       _str(f.get("Ente responsable metodologia")),
            "alcance_metodologico_id":            resolver_o_crear_id("alcance_metodologico", f.get("Alcance metodologico"), valor_por_defecto="No identificado"),
            "indicadores_duplicados":             _str(f.get("Indicadores duplicados")),
        }
        datos_fuentes = [
            {
                "existencia_fuente_id":            resolver_o_crear_id("existencia_fuente", fila.get("Existencia de Fuente"), valor_por_defecto="No hay fuente"),
                "nombre_fuente":                   _str(fila.get("Nombre de fuente")),
                "tipo_fuente_id":                  resolver_o_crear_id("tipo_fuente", _tipo_fuente(fila.get("Tipo de fuente"))),
                "institucion_productora":          _str(fila.get("Institucion productora fuente")),
                "periodicidad_id":                 resolver_o_crear_id("periodicidad_fuente", fila.get("Periodicidad"), valor_por_defecto="No identificada"),
                "sexo_id":                         resolver_o_crear_id("sexo_fuente", _desag_fuente(fila.get("Sexo_dem"))),
                "edad_id":                         resolver_o_crear_id("edad_fuente", _desag_fuente(fila.get("Edad_dem"))),
                "territorio_id":                   resolver_o_crear_id("territorio_fuente", _desag_fuente(fila.get("Territorio_dem"))),
                "discapacidad_id":                 resolver_o_crear_id("discapacidad_fuente", _desag_fuente(fila.get("Discapacidad_dem"))),
                "nivel_ingreso_socioeconomico_id": resolver_o_crear_id("nivel_ingreso_fuente", _desag_fuente(fila.get("Nivel de Ingreso/ socioeconomico"))),
                "ioe_id":                          resolver_o_crear_id("ioe_fuente", _ioe(fila.get("IOE"))),
                "ra_id":                           resolver_o_crear_id("ra_fuente", _ioe(fila.get("R.A"))),
                "calculado_datos_agregados_id":    resolver_o_crear_id("calculado_datos_agregados", fila.get("Calculado y/o Datos agregados"), valor_por_defecto="Dato no disponible"),
                "hipervinculo_ultimo_calculo":     _str(fila.get("Hipervínculo del último cálculo")),
                "anio_ultimo_dato_disponible":     _str(fila.get("AÑO DE ÚLTIMO DATO DISPONIBLE PARA FUENTES GLOBALES")),
                "comentarios":                     _str(fila.get("Comentarios")),
            }
            for _, fila in grupo.iterrows()
        ]
        datos_factibilidad = {
            "c1_metodologia":                 _str(f.get("C1. Existencia de Metodología establecida o definida"), "No cumple con los criterios anteriores"),
            "c21_existencia_fuente":          _str(f.get("C2.1 Existencia (fuente de datos)"), "No hay fuente"),
            "c22_disponibilidad":             _c22(f.get("C2.2. Disponibilidad /accesibilidad")),
            "c23_periodicidad_establecida":   _str(f.get("C2.3 Periodicidad establecida"), "No"),
            "c31_posee_desagregacion":        _str(f.get("C3.1 Posee algún tipo desagregación requerida"), "No"),
            "num_desagregaciones_requeridas": _int(f.get("Numero de desagregaciones requeridas por el indicador")),
            "num_desagregaciones_disponibles":_int(f.get("Numero de desagregaciones disponibles en la fuente")),
            "articulacion_fuentes":           _str(f.get("Articulación de fuentes"), "No se articula"),
            "armonizacion_conceptual":        _str(f.get("Definiciones o armonización conceptual (Requiere y no tiene)"), "No"),
            "subregistro_cobertura":          _str(f.get("Subregistro  y/o Subcobertura"), "No"),
            "cobertura_territorial":          _str(f.get("Cobertura Territorial"), "No"),
            "variables_calculo":              _uso_clasificaciones(f.get("Uso de Clasificaciones")),
            "estructura_datos":               _str(f.get("Estructura de datos"), "No posee ninguna de las anteriores"),
        }

        exito, msg = guardar_indicador(datos_indicador, datos_fuentes, datos_factibilidad)
        if exito:
            ok += 1
        else:
            errores += 1
            logger.warning("[%s] %s", codigo, msg)

    logger.info(
        "Migración END/ODS/CMV completada: %d insertados, %d errores/duplicados.", ok, errores
    )
    print(f"✅ {ok} insertados, {errores} errores.")


def migrar_pnpsp_faltantes(archivo_excel: str | None = None) -> None:
    """Migración idempotente de indicadores PNPSP (sin código fijo en el Excel).

    Genera códigos estables PNPSP-### basados en la columna 'Orden', agrupa
    filas duplicadas por Eje/Política de gobierno, y guarda todos los pares
    en indicador_ejes_politicas. Es seguro re-ejecutar.
    """
    logger.info("Iniciando migración PNPSP...")
    ruta = _resolver_excel(archivo_excel)
    if not ruta:
        logger.error("No se encontró el archivo Excel oficial.")
        return

    df_fac = pd.read_excel(ruta, sheet_name="Factibilidad", header=3)
    df_dem = pd.read_excel(ruta, sheet_name="Demanda y Oferta", header=2)
    df_fac.columns = df_fac.columns.str.strip()
    df_dem.columns = df_dem.columns.str.strip()

    df_fac["_gen"] = df_fac["Generador de demanda"].astype(str).str.strip()
    pnpsp = df_fac[df_fac["_gen"] == "PNPSP"].copy()
    pnpsp = pnpsp[pnpsp["Orden"].notna()]

    ordenes_unicos = sorted(pnpsp["Orden"].unique())
    rango_codigo = {orden: idx + 1 for idx, orden in enumerate(ordenes_unicos)}

    conn = db_mod.obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT codigo FROM indicadores")
    codigos_existentes = {row[0] for row in cursor.fetchall()}
    conn.close()

    ok = saltados = errores = 0

    for orden, grupo in pnpsp.groupby("Orden"):
        codigo = f"PNPSP-{rango_codigo[orden]:03d}"
        if codigo in codigos_existentes:
            saltados += 1
            continue

        primera = grupo.iloc[0]
        nombre = _str(primera.get("Indicador"))
        if not nombre or nombre.upper() == "NAN":
            continue

        # "Requerimiento de clasificacion", "Especificar clasificacion" e
        # "Indicadores duplicados" NO existen en la hoja Factibilidad: solo
        # viven en Demanda y Oferta. Deben resolverse desde ahí (por Orden),
        # igual que las fuentes; leerlas de `primera` (fila de Factibilidad)
        # las devolvía siempre vacías/"No" para el 100% de los indicadores
        # PNPSP. Se usa la primera fila de Demanda del mismo Orden como
        # representativa (consistente en 366/374 casos; para el resto se
        # documenta la ambigüedad y se toma igualmente la primera fila).
        filas_fuente = df_dem[df_dem["Orden"] == orden]
        primera_dem = filas_fuente.iloc[0] if not filas_fuente.empty else primera

        datos_indicador = {
            "codigo":                             codigo,
            "eje_id":                             resolver_o_crear_id("eje", primera.get("Eje"), valor_por_defecto="No identificado"),
            "politica_gobierno_id":               resolver_o_crear_id("politica_gobierno", primera.get("Politica de gobierno")),
            "generador_demanda_id":               resolver_o_crear_id("generador_demanda", "PNPSP"),
            "indicador":                          nombre,
            "dominio_actividad_estadistica_id":   resolver_o_crear_id("dominio_actividad_estadistica", primera.get("Dominio actividad estadistica")),
            "subdominio_actividad_estadistica_id":resolver_o_crear_id("subdominio_actividad_estadistica", primera.get("Sub-Dominio actividad estadistica")),
            "area_misional_one":                  _str(primera.get("Area misional ONE")),
            "sector_ioe_id":                      resolver_o_crear_id("sector_ioe", primera.get("Sector IOE")),
            "requerimiento_clasificacion_id":     resolver_o_crear_id("requerimiento_clasificacion", _req_clas(primera_dem.get("Requerimiento de clasificacion"))),
            "especificar_clasificacion":          _str(primera_dem.get("Especificar clasificacion")),
            "metodo_calculo_id":                  resolver_o_crear_id("metodo_calculo", primera.get("Metodo de calculo"), valor_por_defecto="No identificado"),
            "ficha_tecnica_id":                   resolver_o_crear_id("ficha_tecnica", _ficha(primera.get("Ficha tecnica"))),
            "numerador":                          _str(primera.get("Numerador")),
            "denominador":                        _str(primera.get("Denominador")),
            "unidad_medida":                      _str(primera.get("Unidad de medida")),
            "sexo_id":                            resolver_o_crear_id("sexo_indicador", _desag_ind(primera.get("Sexo"))),
            "edad_id":                            resolver_o_crear_id("edad_indicador", _desag_ind(primera.get("Edad"))),
            "territorio_id":                      resolver_o_crear_id("territorio_indicador", _desag_ind(primera.get("Territorio"))),
            "discapacidad_id":                    resolver_o_crear_id("discapacidad_indicador", _desag_ind(primera.get("Discapacidad"))),
            "nivel_ingreso_id":                   resolver_o_crear_id("nivel_ingreso_indicador", _desag_ind(primera.get("Nivel de ingreso"))),
            "periodicidad_indicador_id":          resolver_o_crear_id("periodicidad_indicador", primera.get("Periodicidad del indicador"), valor_por_defecto="No establecida"),
            "ente_responsable_metodologia":       _str(primera.get("Ente responsable metodologia")),
            "alcance_metodologico_id":            resolver_o_crear_id("alcance_metodologico", primera.get("Alcance metodologico"), valor_por_defecto="No identificado"),
            "indicadores_duplicados":             _str(primera_dem.get("Indicadores duplicados")),
            "_ejes_politicas_extra": [
                (
                    resolver_o_crear_id("eje", f.get("Eje"), valor_por_defecto="No identificado"),
                    resolver_o_crear_id("politica_gobierno", f.get("Politica de gobierno")),
                )
                for _, f in grupo.iterrows()
            ],
        }

        datos_fuentes = [
            {
                "existencia_fuente_id":            resolver_o_crear_id("existencia_fuente", fdem.get("Existencia de Fuente"), valor_por_defecto="No hay fuente"),
                "nombre_fuente":                   _str(fdem.get("Nombre de fuente")),
                "tipo_fuente_id":                  resolver_o_crear_id("tipo_fuente", _tipo_fuente(fdem.get("Tipo de fuente"))),
                "institucion_productora":          _str(fdem.get("Institucion productora fuente")),
                "periodicidad_id":                 resolver_o_crear_id("periodicidad_fuente", fdem.get("Periodicidad"), valor_por_defecto="No identificada"),
                "sexo_id":                         resolver_o_crear_id("sexo_fuente", _desag_fuente(fdem.get("Sexo"))),
                "edad_id":                         resolver_o_crear_id("edad_fuente", _desag_fuente(fdem.get("Edad"))),
                "territorio_id":                   resolver_o_crear_id("territorio_fuente", _desag_fuente(fdem.get("Territorio"))),
                "discapacidad_id":                 resolver_o_crear_id("discapacidad_fuente", _desag_fuente(fdem.get("Discapacidad"))),
                "nivel_ingreso_socioeconomico_id": resolver_o_crear_id("nivel_ingreso_fuente", _desag_fuente(fdem.get("Nivel de Ingreso/ socioeconomico"))),
                "ioe_id":                          resolver_o_crear_id("ioe_fuente", _ioe(fdem.get("IOE"))),
                "ra_id":                           resolver_o_crear_id("ra_fuente", _ioe(fdem.get("R.A"))),
                "calculado_datos_agregados_id":    resolver_o_crear_id("calculado_datos_agregados", fdem.get("Calculado y/o Datos agregados"), valor_por_defecto="Dato no disponible"),
                "hipervinculo_ultimo_calculo":     _str(fdem.get("Hipervínculo del último cálculo")),
                "anio_ultimo_dato_disponible":     _str(fdem.get("AÑO DE ÚLTIMO DATO DISPONIBLE PARA FUENTES GLOBALES")),
                "comentarios":                     _str(fdem.get("Comentarios")),
            }
            for _, fdem in filas_fuente.iterrows()
        ] or [{"existencia_fuente_id": resolver_o_crear_id("existencia_fuente", None, valor_por_defecto="No hay fuente")}]

        datos_factibilidad = {
            "c1_metodologia":                 _str(primera.get("C1. Existencia de Metodología establecida o definida"), "No cumple con los criterios anteriores"),
            "c21_existencia_fuente":          _str(primera.get("C2.1 Existencia (fuente de datos)"), "No hay fuente"),
            "c22_disponibilidad":             _c22(primera.get("C2.2. Disponibilidad /accesibilidad")),
            "c23_periodicidad_establecida":   _str(primera.get("C2.3 Periodicidad establecida"), "No"),
            "c31_posee_desagregacion":        _str(primera.get("C3.1 Posee algún tipo desagregación requerida"), "No"),
            "num_desagregaciones_requeridas": _int(primera.get("Numero de desagregaciones requeridas por el indicador")),
            "num_desagregaciones_disponibles":_int(primera.get("Numero de desagregaciones disponibles en la fuente")),
            "articulacion_fuentes":           _str(primera.get("Articulación de fuentes"), "No se articula"),
            "armonizacion_conceptual":        _str(primera.get("Definiciones o armonización conceptual (Requiere y no tiene)"), "No"),
            "subregistro_cobertura":          _str(primera.get("Subregistro  y/o Subcobertura"), "No"),
            "cobertura_territorial":          _str(primera.get("Cobertura Territorial"), "No"),
            "variables_calculo":              _uso_clasificaciones(primera.get("Uso de Clasificaciones")),
            "estructura_datos":               _str(primera.get("Estructura de datos"), "No posee ninguna de las anteriores"),
        }

        exito, msg = guardar_indicador(datos_indicador, datos_fuentes, datos_factibilidad)
        if exito:
            ok += 1
            codigos_existentes.add(codigo)
        else:
            errores += 1
            logger.warning("[%s] %s", codigo, msg)

    logger.info(
        "Migración PNPSP completada: %d nuevos, %d ya existían, %d errores.", ok, saltados, errores
    )
    print(f"✅ PNPSP: {ok} nuevos, {saltados} ya existían (idempotencia), {errores} errores.")
    print(f"   Total únicos detectados: {len(ordenes_unicos)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Este script se ejecuta sobre una base de datos VACÍA (ver docstring
    # del módulo): db_mod.inicializar_base_datos() debe llamarse explícitamente
    # primero para crear el esquema, ya que importar data.database ya no lo
    # hace como efecto secundario (Hallazgo #4 del informe de revisión de
    # código de agosto 2026).
    db_mod.inicializar_base_datos()
    migrar_historico_excel()
    migrar_pnpsp_faltantes()
