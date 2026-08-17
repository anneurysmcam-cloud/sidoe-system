"""
tracking/export_excel.py
=========================
Utilidad para convertir DataFrames a Excel en memoria.
"""

import io

import pandas as pd


def generar_excel_memoria(df: pd.DataFrame, nombre_hoja: str = "Matriz_SIDOE_ONE") -> bytes:
    """Convierte un DataFrame a un archivo Excel en memoria.

    Args:
        df: DataFrame a exportar.
        nombre_hoja: Nombre de la hoja en el archivo Excel.

    Returns:
        Contenido del .xlsx como bytes, listo para st.download_button.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=nombre_hoja)
    return buffer.getvalue()
