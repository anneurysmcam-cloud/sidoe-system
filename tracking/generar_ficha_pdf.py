"""
tracking/generar_ficha_pdf.py
==============================
Genera la ficha PDF institucional de un indicador usando fpdf2.

Compatible con fpdf2 >= 2.7 (output() devuelve bytearray; se normaliza a bytes).
"""

import os

from fpdf import FPDF

# Ruta al logo institucional relativa a la raíz del proyecto
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGO_PATH = os.path.join(_BASE_DIR, "tracking", "logo_one.png")


def _dibujar_fila(pdf, clave, texto, col_width, row_height, x_izq, margen_inferior):
    """Dibuja una fila etiqueta/valor con salto de página manual si no cabe."""
    lineas = pdf.multi_cell(0, row_height, texto, border=1, dry_run=True, output="LINES")
    alto_fila = row_height * max(len(lineas), 1)

    if pdf.get_y() + alto_fila > pdf.h - margen_inferior:
        pdf.add_page()

    y_fila = pdf.get_y()

    pdf.set_xy(x_izq, y_fila)
    pdf.set_font("Arial", "B", 11)
    pdf.set_fill_color(220, 230, 241)
    pdf.multi_cell(
        col_width, alto_fila, str(clave), border=1, fill=True,
        new_x="RIGHT", new_y="TOP",
    )

    pdf.set_xy(x_izq + col_width, y_fila)
    pdf.set_font("Arial", size=11)
    pdf.multi_cell(0, row_height, texto, border=1, new_x="LMARGIN", new_y="NEXT")


def generar_ficha_pdf(indicador: dict, fuentes: list[dict] | None = None) -> bytes:
    """Genera la ficha PDF de un indicador y la devuelve como bytes.

    Args:
        indicador: Dict con los campos del indicador ya resueltos a texto
                   (sin claves _id ni estado_indicador).
        fuentes: Lista de dicts con los campos de cada fuente asociada al
                 indicador (ya resueltos a texto, sin claves _id). Si el
                 indicador tiene varias fuentes, cada una se imprime en su
                 propia subsección numerada dentro de la ficha.

    Returns:
        Contenido del PDF como bytes, listo para st.download_button.
    """
    fuentes = fuentes or []
    pdf = FPDF()
    pdf.add_page()

    # ── Encabezado con logo ──────────────────────────────────────────────────
    if os.path.exists(_LOGO_PATH):
        pdf.image(_LOGO_PATH, x=80, y=10, w=50)

    pdf.set_font("Arial", "B", 18)
    pdf.set_text_color(0, 51, 102)
    pdf.ln(60)
    pdf.cell(0, 12, "Oficina Nacional de Estadística (ONE)", ln=True, align="C")
    pdf.ln(5)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Ficha del Indicador", ln=True, align="C")
    pdf.ln(15)

    # ── Nombre del indicador destacado ──────────────────────────────────────
    pdf.set_font("Arial", "B", 12)
    pdf.set_text_color(200, 0, 0)
    pdf.multi_cell(
        0, 8, f"Indicador: {indicador.get('indicador', '')}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(10)

    # ── Tabla de campos ──────────────────────────────────────────────────────
    pdf.set_font("Arial", size=11)
    col_width = 70
    row_height = 10
    x_izq = pdf.l_margin
    margen_inferior = 15

    # Desactivar salto automático para controlar manualmente que etiqueta
    # y valor queden siempre en la misma página.
    pdf.set_auto_page_break(False)

    for clave, valor in indicador.items():
        texto = str(valor) if valor is not None else ""
        _dibujar_fila(pdf, clave, texto, col_width, row_height, x_izq, margen_inferior)

    # ── Sección de fuentes (todas las fuentes del indicador) ────────────────
    if fuentes:
        pdf.ln(8)
        if pdf.get_y() + row_height > pdf.h - margen_inferior:
            pdf.add_page()
        pdf.set_font("Arial", "B", 13)
        pdf.set_text_color(0, 51, 102)
        titulo_fuentes = (
            f"Fuentes del Indicador ({len(fuentes)})"
            if len(fuentes) > 1
            else "Fuente del Indicador"
        )
        pdf.cell(0, 10, titulo_fuentes, ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

        for idx, fuente in enumerate(fuentes, start=1):
            if pdf.get_y() + row_height > pdf.h - margen_inferior:
                pdf.add_page()
            pdf.set_font("Arial", "B", 12)
            pdf.set_fill_color(0, 47, 108)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 9, f"Fuente {idx} de {len(fuentes)}", ln=True, fill=True)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)

            campos_fuente = {
                k: v for k, v in fuente.items()
                if not k.endswith("_id") and k not in ("id",)
            }
            for clave, valor in campos_fuente.items():
                texto = str(valor) if valor is not None else ""
                _dibujar_fila(pdf, clave, texto, col_width, row_height, x_izq, margen_inferior)
            pdf.ln(4)

    pdf.set_auto_page_break(True, margin=margen_inferior)

    # ── Pie de página ────────────────────────────────────────────────────────
    pdf.ln(15)
    pdf.set_font("Arial", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0, 10,
        "Sistema Integrado de Demanda y Oferta Estadística (SIDOE)",
        ln=True, align="C",
    )

    return bytes(pdf.output())
