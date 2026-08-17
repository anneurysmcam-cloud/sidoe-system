"""
tests/test_40_correccion_fuentes_supervision_mensajes.py
==========================================================
TEST DE REGRESIÓN — dos bugs reportados por Randy sobre
views/actualizar_indicador.py y models/crud_indicadores.actualizar_fuente:

1. Mensajes por sección (utils/ui_mensajes.py): la llamada genérica a
   mostrar_mensaje_pendiente() SIN `seccion` (usada al tope de la vista)
   "robaba" mensajes marcados con seccion="fuentes" antes de que llegaran
   al bloque "📡 Fuentes de información" que sí filtra por esa sección —
   el mensaje de "Fuente actualizada correctamente." aparecía arriba de
   toda la página en vez de junto a la sección de fuentes.

2. actualizar_fuente() no pasaba por revisión del supervisor cuando la
   actualización no producía diferencias de contenido (guardar sin
   cambios, o un borrador previo a la existencia de este mecanismo):
   marcar_pendiente_revision() solo se llamaba dentro de `if cambios:`,
   así que el indicador nunca volvía a 'borrador' ni aparecía en Aprobar
   Indicadores, pese a que la vista de aprobación ya tiene un mensaje de
   fallback listo para ese caso exacto ("No hay detalle de campos
   disponible para esta actualización...").
"""

from utils.ui_mensajes import marcar_mensaje, mostrar_mensaje_pendiente


def _fuente() -> dict:
    return {
        "nombre_fuente": "Fuente integración test",
        "institucion_productora": "ONE Test",
    }


def _indicador(codigo: str) -> dict:
    return {
        "codigo": codigo,
        "indicador": "Indicador de prueba supervisión",
        "estado_indicador": "Activo",
        "generador_demanda_id": 1,
    }


FACT_MIN = {
    "c1_metodologia": "No cumple con los criterios anteriores",
    "c21_existencia_fuente": "No hay fuente",
    "c22_disponibilidad": "No",
    "c23_periodicidad_establecida": "No",
    "c31_posee_desagregacion": "No",
    "num_desagregaciones_requeridas": 0,
    "num_desagregaciones_disponibles": 0,
    "articulacion_fuentes": "No se articula",
    "armonizacion_conceptual": "Sí",
    "subregistro_cobertura": "Sí",
    "cobertura_territorial": "No",
    "estructura_datos": "c) No posee ninguna de las anteriores",
    "variables_calculo": "No",
}


# ---------------------------------------------------------------------------
# 1. Mensajes por sección
# ---------------------------------------------------------------------------

class TestMensajePorSeccion:

    def test_llamada_generica_no_consume_mensaje_de_otra_seccion(self, monkeypatch):
        """La llamada sin `seccion` (equivalente al tope de
        actualizar_indicador.py) NO debe mostrar ni consumir un mensaje
        marcado para la sección 'fuentes'."""
        mostrados = []
        monkeypatch.setattr(
            "streamlit.success", lambda texto: mostrados.append(("success", texto))
        )

        marcar_mensaje("success", "Fuente actualizada correctamente.", seccion="fuentes")
        mostrar_mensaje_pendiente()  # llamada genérica, sin seccion

        assert mostrados == [], "El mensaje de 'fuentes' no debía mostrarse en la llamada genérica"

        mostrar_mensaje_pendiente(seccion="fuentes")  # llamada de la sección correcta
        assert mostrados == [("success", "Fuente actualizada correctamente.")]

    def test_mensaje_global_si_se_muestra_en_llamada_generica(self, monkeypatch):
        """Un mensaje SIN sección (global) sigue mostrándose en la primera
        llamada, sin importar si esa llamada pasa `seccion` o no —
        comportamiento previo que no debe romperse."""
        mostrados = []
        monkeypatch.setattr(
            "streamlit.success", lambda texto: mostrados.append(("success", texto))
        )

        marcar_mensaje("success", "Indicador actualizado correctamente.")
        mostrar_mensaje_pendiente()

        assert mostrados == [("success", "Indicador actualizado correctamente.")]

    def test_mensaje_de_seccion_no_se_muestra_en_otra_seccion(self, monkeypatch):
        """Un mensaje marcado para 'fuentes' tampoco debe aparecer si se
        consulta con una sección distinta."""
        mostrados = []
        monkeypatch.setattr(
            "streamlit.success", lambda texto: mostrados.append(("success", texto))
        )

        marcar_mensaje("success", "Fuente actualizada correctamente.", seccion="fuentes")
        mostrar_mensaje_pendiente(seccion="otra_seccion")

        assert mostrados == []


# ---------------------------------------------------------------------------
# 2. actualizar_fuente siempre pasa por supervisión
# ---------------------------------------------------------------------------

class TestActualizarFuenteSiempreVaASupervision:

    def _crear_indicador_publicado(self, codigo: str) -> int:
        import data.database as db_mod
        from models.crud_indicadores import guardar_indicador

        ok, _ = guardar_indicador(
            datos_indicador=_indicador(codigo),
            datos_fuentes=[_fuente()],
            datos_factibilidad=FACT_MIN,
            usuario_id=1,
        )
        assert ok

        conn = db_mod.obtener_conexion()
        ind_id = conn.execute(
            "SELECT id FROM indicadores WHERE codigo=?", (codigo,)
        ).fetchone()[0]
        # Publicar para que actualizar_fuente lo devuelva a 'borrador' de forma detectable.
        conn.execute(
            "UPDATE indicadores SET estado_publicacion='publicado' WHERE id=?", (ind_id,)
        )
        fuente_id = conn.execute(
            "SELECT id FROM fuentes_indicador WHERE indicador_id=?", (ind_id,)
        ).fetchone()[0]
        conn.commit()
        conn.close()
        return ind_id, fuente_id

    def test_actualizar_fuente_sin_cambios_va_a_borrador_y_revision(self, sidoe_config):
        """Guardar 'Actualizar fuente' con los MISMOS valores (sin cambios
        de contenido) debe igual devolver el indicador a 'borrador' y
        dejarlo marcado como pendiente de revisión — antes del fix, se
        omitía por completo cuando `cambios` quedaba vacío."""
        import data.database as db_mod
        from models.crud_indicadores import actualizar_fuente

        ind_id, fuente_id = self._crear_indicador_publicado("INT-FUENTE-NOCHG-01")

        ok, msg = actualizar_fuente(
            fuente_id=fuente_id,
            datos_fuente=_fuente(),  # idénticos a los ya guardados
            usuario_id=1,
        )
        assert ok is True
        assert "borrador" in msg.lower()

        conn = db_mod.obtener_conexion()
        fila = conn.execute(
            "SELECT estado_publicacion, revision_tipo, revision_detalle "
            "FROM indicadores WHERE id=?",
            (ind_id,),
        ).fetchone()
        conn.close()

        estado_publicacion, revision_tipo, revision_detalle = fila
        assert estado_publicacion == "borrador"
        assert revision_tipo == "actualizado"
        # Sin diferencias de contenido -> detalle vacío; Aprobar Indicadores
        # ya sabe mostrar el mensaje de fallback para este caso.
        assert not revision_detalle

    def test_actualizar_fuente_con_cambios_sigue_registrando_el_detalle(self, sidoe_config):
        """Caso de control: si sí hay cambios de contenido, el detalle
        debe seguir registrándose como antes."""
        import data.database as db_mod
        from models.crud_indicadores import actualizar_fuente

        ind_id, fuente_id = self._crear_indicador_publicado("INT-FUENTE-CHG-01")

        ok, msg = actualizar_fuente(
            fuente_id=fuente_id,
            datos_fuente={
                "nombre_fuente": "Fuente integración test",
                "institucion_productora": "ONE Test — Modificada",
            },
            usuario_id=1,
        )
        assert ok is True
        assert "borrador" in msg.lower()

        conn = db_mod.obtener_conexion()
        fila = conn.execute(
            "SELECT estado_publicacion, revision_tipo, revision_detalle "
            "FROM indicadores WHERE id=?",
            (ind_id,),
        ).fetchone()
        conn.close()

        estado_publicacion, revision_tipo, revision_detalle = fila
        assert estado_publicacion == "borrador"
        assert revision_tipo == "actualizado"
        assert revision_detalle
        assert "ONE Test — Modificada" in revision_detalle
