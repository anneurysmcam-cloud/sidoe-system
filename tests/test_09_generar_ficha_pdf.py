"""
tests/test_09_generar_ficha_pdf.py
===================================
Cobertura de tracking/generar_ficha_pdf.py, incluyendo el caso de
indicadores con múltiples fuentes (bug reportado y corregido).
"""

import io

from pypdf import PdfReader

from tracking.generar_ficha_pdf import generar_ficha_pdf

INDICADOR_SIMPLE = {
    "codigo": "TEST-001",
    "indicador": "Indicador de prueba",
    "unidad_medida": "Porcentaje",
    "cantidad_fuentes": 0,
}


def test_genera_pdf_sin_fuentes():
    pdf_bytes = generar_ficha_pdf(INDICADOR_SIMPLE)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_genera_pdf_con_fuentes_none_equivale_a_sin_fuentes():
    a = generar_ficha_pdf(INDICADOR_SIMPLE, None)
    b = generar_ficha_pdf(INDICADOR_SIMPLE)
    assert a[:4] == b"%PDF"
    assert b[:4] == b"%PDF"


def test_genera_pdf_con_una_fuente():
    fuentes = [
        {
            "id": 1,
            "nombre_fuente": "Encuesta Nacional",
            "institucion_productora": "ONE",
            "tipo_fuente": "Administrativa",
        }
    ]
    pdf_bytes = generar_ficha_pdf(INDICADOR_SIMPLE, fuentes)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_genera_pdf_con_multiples_fuentes():
    """Caso central del fix: un indicador con varias fuentes debe incluirlas todas."""
    fuentes = [
        {
            "id": i,
            "indicador_id": 99,
            "nombre_fuente": f"Fuente {i}",
            "institucion_productora": f"Institución {i}",
            "tipo_fuente": "Estadística",
            "periodicidad": "Anual",
            "comentarios": "Texto largo " * 20,  # fuerza salto de línea/página
        }
        for i in range(1, 6)
    ]
    pdf_bytes = generar_ficha_pdf(INDICADOR_SIMPLE, fuentes)
    assert pdf_bytes[:4] == b"%PDF"
    # Un PDF con 5 fuentes con texto largo debe ser sensiblemente más grande
    # que uno sin fuentes (evidencia indirecta de que todas se dibujaron).
    pdf_sin_fuentes = generar_ficha_pdf(INDICADOR_SIMPLE)
    assert len(pdf_bytes) > len(pdf_sin_fuentes)


def test_genera_pdf_con_muchas_fuentes_fuerza_salto_de_pagina():
    fuentes = [
        {"id": i, "nombre_fuente": f"Fuente {i}", "comentarios": "x" * 300}
        for i in range(1, 20)
    ]
    pdf_bytes = generar_ficha_pdf(INDICADOR_SIMPLE, fuentes)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 2000


def test_fuentes_ignoran_claves_id_y_estado():
    fuentes = [
        {
            "id": 1,
            "indicador_id": 99,
            "existencia_fuente_id": 3,
            "nombre_fuente": "Fuente con IDs internos",
        }
    ]
    # No debe lanzar excepción ni incluir procesamiento especial de _id
    pdf_bytes = generar_ficha_pdf(INDICADOR_SIMPLE, fuentes)
    assert pdf_bytes[:4] == b"%PDF"


def test_indicador_con_valores_none():
    indicador = {"codigo": "TEST-002", "indicador": "Sin datos", "campo_vacio": None}
    pdf_bytes = generar_ficha_pdf(indicador)
    assert pdf_bytes[:4] == b"%PDF"


def test_indicador_con_texto_largo_fuerza_multilinea():
    indicador = {
        "codigo": "TEST-003",
        "indicador": "Indicador con texto largo",
        "ficha_tecnica": "Lorem ipsum dolor sit amet. " * 50,
    }
    pdf_bytes = generar_ficha_pdf(indicador)
    assert pdf_bytes[:4] == b"%PDF"


def test_indicador_con_multiples_ejes_politicas_no_pierde_ninguno():
    """Punto 5: la ficha debe mostrar TODOS los ejes/políticas asociados
    (1:N vía indicador_ejes_politicas), no solo el primer par legado."""
    pares = " / ".join(f"Eje {i} / Política {i}" for i in range(1, 8))
    indicador = {
        "codigo": "TEST-EJES",
        "indicador": "Indicador con varios ejes y políticas",
        "ejes_politicas": pares,
    }
    pdf_bytes = generar_ficha_pdf(indicador)
    assert pdf_bytes[:4] == b"%PDF"

    reader = PdfReader(io.BytesIO(pdf_bytes))
    texto = "".join(p.extract_text() for p in reader.pages)
    assert "Eje 1" in texto
    assert "Eje 7" in texto
    assert "Política 7" in texto


def test_valor_que_excede_una_pagina_completa_no_pierde_contenido():
    """Punto 5: un valor único (p. ej. muchos ejes/políticas concatenados)
    más alto que una página completa no debe cortarse ni perderse —
    fpdf2 debe continuar el contenido en páginas siguientes."""
    valor_enorme = " / ".join(f"Eje {i} / Política {i}" for i in range(1, 400))
    indicador = {
        "codigo": "TEST-OVERFLOW",
        "indicador": "Indicador con lista extremadamente larga",
        "ejes_politicas": valor_enorme,
    }
    pdf_bytes = generar_ficha_pdf(indicador)
    assert pdf_bytes[:4] == b"%PDF"

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) > 1
    texto = "".join(p.extract_text() for p in reader.pages)
    assert "Eje 1 " in texto
    assert "Eje 200" in texto
    assert "Eje 399" in texto
