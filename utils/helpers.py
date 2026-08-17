"""
utils/helpers.py
================
Utilidades transversales de propósito general para el sistema SIDOE.
Ninguna función de este módulo importa Streamlit ni accede a la base de datos;
son transformaciones y validaciones puras.
"""

import re
import uuid
from datetime import timedelta, timezone

import pandas as pd

# SQLite's datetime('now') siempre devuelve UTC, independientemente del
# huso horario del sistema operativo donde corra el servidor. República
# Dominicana no observa horario de verano, así que un offset fijo de -4
# es correcto todo el año y evita depender de una base de datos IANA de
# husos horarios (zoneinfo) que no viene preinstalada en Windows.
HUSO_HORARIO_RD = timezone(timedelta(hours=-4))


def validar_campos(campos: list) -> bool:
    """Verifica que todos los campos tengan contenido válido (no None ni vacíos)."""
    return all(c is not None and str(c).strip() != "" for c in campos)


def formatear_fecha(fecha_str: str) -> str:
    """Convierte 'YYYY-MM-DD' a 'DD/MM/YYYY'. Devuelve el original si falla."""
    try:
        return pd.to_datetime(fecha_str).strftime("%d/%m/%Y")
    except Exception:
        return fecha_str


def limpiar_texto(texto: str | None) -> str:
    """Elimina espacios extra y normaliza el texto. Devuelve '' si es None."""
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", texto.strip())


def normalizar_titulo_indicador(texto: str | None) -> str:
    """Colapsa espacios y convierte a minúsculas para comparar títulos de
    indicadores sin depender de diferencias de capitalización o espacios.

    Vive en este módulo (puro, sin Streamlit ni acceso a BD) porque lo usan
    dos capas que no deben importarse entre sí: models/crud_indicadores.py
    (matching en Python al detectar referencias automáticas) y
    data/database.py (backfill de la columna `titulo_normalizado` en la
    migración correspondiente). Ver Hallazgo 2 del informe de rendimiento
    de agosto 2026.
    """
    return limpiar_texto(texto).lower()


def porcentaje(valor: float, decimales: int = 2) -> str:
    """Formatea un número como porcentaje. Ejemplo: 0.856 → '85.60%'."""
    try:
        return f"{round(valor * 100, decimales)}%"
    except Exception:
        return "N/A"


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte nombres de columnas a minúsculas y reemplaza espacios por '_'."""
    df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
    return df


def validar_dataframe(df: pd.DataFrame | None) -> bool:
    """Devuelve True si el DataFrame tiene datos."""
    return df is not None and not df.empty


def generar_codigo(prefix: str = "IND") -> str:
    """Genera un código único de indicador. Ejemplo: IND-20260702-abc123."""
    return f"{prefix}-{pd.Timestamp.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


def formatear_timestamp_local(valor, formato: str = "%d/%m/%Y %H:%M:%S") -> str:
    """Convierte un timestamp UTC (almacenado por SQLite vía datetime('now'))
    a hora local de República Dominicana (UTC-4) y lo formatea como texto.

    Diagnóstico del desfase reportado: calc_timestamp y demás columnas
    'timestamp' del esquema usan el DEFAULT (datetime('now')) de SQLite, que
    siempre devuelve UTC sin importar el huso horario del servidor. Como esos
    valores se mostraban tal cual (sin conversión), un usuario en RD (UTC-4)
    veía una hora adelantada. Esta función centraliza la conversión a hora
    local únicamente para presentación; el almacenamiento permanece en UTC
    (buena práctica para auditoría e independiente del huso del servidor).

    Devuelve el valor original (como string) si no se puede interpretar como
    fecha/hora, en vez de fallar.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return valor
    try:
        ts = pd.to_datetime(valor, utc=True)
        return ts.tz_convert(HUSO_HORARIO_RD).strftime(formato)
    except (ValueError, TypeError):
        return str(valor)


def convertir_columna_utc_a_rd(serie: pd.Series) -> pd.Series:
    """Aplica ``formatear_timestamp_local`` a cada valor de una columna/Serie.

    Pensado para columnas 'timestamp' o 'calc_timestamp' leídas directamente
    de SQLite antes de mostrarlas en pantalla o exportarlas a Excel.
    """
    return serie.apply(formatear_timestamp_local)


def resumen_dataframe(df: pd.DataFrame) -> dict:
    """Devuelve un resumen básico del DataFrame para depuración."""
    return {
        "total_registros": len(df),
        "columnas": list(df.columns),
        "primeras_filas": df.head(3).to_dict(orient="records"),
    }
