"""
tests/test_36_deteccion_cambios_supervisor.py
================================================
TEST DE REGRESIÓN — resumen de "qué cambió" que ve el supervisor en
Aprobar Indicadores (ver models/revision_pendiente.py y
views/aprobar_indicadores.py).

Contexto del bug reportado por Randy:
  Al agregar un Eje/Política de gobierno ADICIONAL a un indicador (el
  selector opcional de views/actualizar_indicador.py, no el par
  principal), el cambio se guardaba correctamente en
  indicador_ejes_politicas (sincronizar_ejes_politicas), pero nunca
  aparecía en el resumen de cambios que ve el supervisor: la lista
  ``cambios`` de modificar_indicador() solo pasaba por
  calcular_diferencias() las columnas propias de ``indicadores`` y
  ``calculo_factibilidad`` — pares_extra se hacía pop() de
  datos_indicador ANTES de esa comparación y sincronizar_ejes_politicas()
  nunca pasa por calcular_diferencias().

  Se encontró el mismo patrón de bug para los campos personalizados de
  Auxiliares (tabla EAV indicador_campos_personalizados,
  guardar_campos_personalizados()): tampoco se comparaban contra su valor
  anterior para el resumen del supervisor.

Este archivo cubre ambos casos, además de un caso base (columna normal de
`indicadores`) para dejar registrado el comportamiento esperado del
mecanismo completo.
"""

import json

from models.crud_auxiliares import crear_categoria, crear_valor, opciones_selectbox
from models.crud_indicadores import guardar_indicador, modificar_indicador

DATOS_FACTIBILIDAD_MINIMA = {
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
    "estructura_datos": "No posee ninguna de las anteriores",
    "variables_calculo": "No",
}


def _crear_indicador_publicado(codigo: str, eje_id, politica_id) -> int:
    """Crea un indicador ya 'publicado' (simula contenido que el público
    ya veía) y devuelve su id. Se fuerza estado_publicacion explícito para
    que la siguiente edición se clasifique como 'actualizado', que es el
    caso donde el supervisor necesita ver el detalle de cambios."""
    datos = {
        "codigo": codigo,
        "indicador": f"Indicador {codigo}",
        "estado_indicador": "Activo",
        "estado_publicacion": "publicado",
        "generador_demanda_id": 1,
        "eje_id": eje_id,
        "politica_gobierno_id": politica_id,
    }
    ok, msg = guardar_indicador(
        datos_indicador=datos,
        datos_fuentes=[],
        datos_factibilidad=DATOS_FACTIBILIDAD_MINIMA,
        usuario_id=1,
    )
    assert ok, f"No se pudo crear el indicador de prueba {codigo}: {msg}"

    import data.database as db_mod
    conn = db_mod.obtener_conexion()
    row = conn.execute(
        "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
    ).fetchone()
    conn.close()
    return row[0]


def _leer_revision(indicador_id: int) -> tuple[str | None, list[dict]]:
    import data.database as db_mod
    conn = db_mod.obtener_conexion()
    row = conn.execute(
        "SELECT revision_tipo, revision_detalle FROM indicadores WHERE id = ?",
        (indicador_id,),
    ).fetchone()
    conn.close()
    tipo, detalle = row
    cambios = json.loads(detalle) if detalle else []
    return tipo, cambios


class TestDeteccionCambioCampoSimple:
    """Caso base: editar una columna normal de `indicadores` sí se
    detectaba correctamente antes de este fix — se deja como referencia
    de comportamiento esperado."""

    def test_editar_nombre_indicador_aparece_en_revision_detalle(self, sidoe_config):
        _, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        eje_id = next(iter(mapa_eje.values()))
        politica_id = next(iter(mapa_politica.values()))

        id_ind = _crear_indicador_publicado("P36-SIMPLE", eje_id, politica_id)

        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-SIMPLE",
                "indicador": "Nombre editado",
                "estado_publicacion": "borrador",
            },
            {},
            usuario_id=1,
        )
        assert ok, msg

        tipo, cambios = _leer_revision(id_ind)
        assert tipo == "actualizado"
        campos = {c["campo"] for c in cambios}
        assert "Nombre del indicador" in campos


class TestClasificacionNuevoVsActualizado:
    """Bug reportado por Randy ('no se valida como edición'): editar un
    indicador YA publicado se estaba clasificando como 'nuevo' en vez de
    'actualizado', porque modificar_indicador() forzaba estado_publicacion
    a 'borrador' en el UPDATE principal ANTES de que
    marcar_pendiente_revision() leyera ese mismo campo de la BD para
    decidir la clasificación — para ese momento ya estaba sobrescrito."""

    def test_editar_indicador_publicado_se_clasifica_como_actualizado(
        self, sidoe_config
    ):
        _, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        eje_id = next(iter(mapa_eje.values()))
        politica_id = next(iter(mapa_politica.values()))

        id_ind = _crear_indicador_publicado("P36-CLASIF", eje_id, politica_id)

        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        estado_antes = conn.execute(
            "SELECT estado_publicacion FROM indicadores WHERE id = ?", (id_ind,)
        ).fetchone()[0]
        conn.close()
        assert estado_antes == "publicado", (
            "Precondición de la prueba: el indicador debe estar publicado "
            "antes de editarlo."
        )

        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-CLASIF",
                "indicador": "Nombre editado tras publicación",
                "estado_publicacion": "borrador",
            },
            {},
            usuario_id=1,
        )
        assert ok, msg

        tipo, _ = _leer_revision(id_ind)
        assert tipo == "actualizado", (
            "Un indicador que ya estaba 'publicado' antes de esta edición "
            f"debe clasificarse como 'actualizado', no '{tipo}'."
        )

    def test_crear_y_editar_antes_de_aprobar_se_mantiene_como_nuevo(
        self, sidoe_config
    ):
        """Caso de borde documentado en revision_pendiente.py: dos
        ediciones seguidas de un indicador que TODAVÍA no ha sido aprobado
        ni una sola vez deben seguir clasificadas como 'nuevo', no
        'actualizado' (nunca llegó a ser público)."""
        _, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        eje_id = next(iter(mapa_eje.values()))
        politica_id = next(iter(mapa_politica.values()))

        datos = {
            "codigo": "P36-DOSVECES",
            "indicador": "Indicador P36-DOSVECES",
            "estado_indicador": "Activo",
            "estado_publicacion": "borrador",
            "generador_demanda_id": 1,
            "eje_id": eje_id,
            "politica_gobierno_id": politica_id,
        }
        ok, msg = guardar_indicador(
            datos_indicador=datos, datos_fuentes=[],
            datos_factibilidad=DATOS_FACTIBILIDAD_MINIMA, usuario_id=1,
        )
        assert ok, msg
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        id_ind = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = 'P36-DOSVECES'"
        ).fetchone()[0]
        conn.close()

        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-DOSVECES", "indicador": "Segunda edición",
                "estado_publicacion": "borrador",
            },
            {}, usuario_id=1,
        )
        assert ok, msg

        tipo, _ = _leer_revision(id_ind)
        assert tipo == "nuevo", (
            "Un indicador que nunca fue aprobado/publicado debe seguir "
            f"clasificado como 'nuevo' tras una segunda edición, no '{tipo}'."
        )


class TestDeteccionEjeAdicional:
    """Bug reportado: agregar un Eje/Política adicional no se veía en el
    resumen de cambios del supervisor."""

    def test_agregar_eje_adicional_aparece_en_revision_detalle(self, sidoe_config):
        textos_eje, mapa_eje = opciones_selectbox("eje")
        textos_politica, mapa_politica = opciones_selectbox("politica_gobierno")
        assert len(textos_eje) >= 2 and len(textos_politica) >= 2, (
            "Se necesitan al menos 2 valores de eje/política sembrados para "
            "esta prueba."
        )
        eje_principal_id = mapa_eje[textos_eje[0]]
        pol_principal_id = mapa_politica[textos_politica[0]]
        eje_extra_id = mapa_eje[textos_eje[1]]
        pol_extra_id = mapa_politica[textos_politica[1]]

        id_ind = _crear_indicador_publicado(
            "P36-EJEXTRA", eje_principal_id, pol_principal_id
        )

        # Edición que solo agrega un eje/política adicional — ningún otro
        # campo de `indicadores` cambia de valor.
        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-EJEXTRA",
                "indicador": "Indicador P36-EJEXTRA",
                "estado_publicacion": "borrador",
                "eje_id": eje_principal_id,
                "politica_gobierno_id": pol_principal_id,
                "_ejes_politicas_extra": [(eje_extra_id, pol_extra_id)],
            },
            {},
            usuario_id=1,
        )
        assert ok, msg

        tipo, cambios = _leer_revision(id_ind)
        assert tipo == "actualizado"
        assert cambios, (
            "Se esperaba al menos un cambio detectado (el eje/política "
            "adicional agregado), pero revision_detalle quedó vacío."
        )
        entradas = {c["campo"]: c for c in cambios}
        assert "Ejes/Políticas de gobierno adicionales" in entradas, (
            f"El cambio de eje/política adicional no aparece en el resumen. "
            f"Campos detectados: {list(entradas.keys())}"
        )
        cambio = entradas["Ejes/Políticas de gobierno adicionales"]
        assert cambio["anterior"] == "—"
        assert textos_eje[1] in cambio["nuevo"]
        assert textos_politica[1] in cambio["nuevo"]

    def test_reordenar_los_mismos_ejes_extra_no_reporta_cambio(self, sidoe_config):
        """Guardar el mismo conjunto de ejes/políticas adicionales, aunque
        en otro orden, no debe generar ruido en el resumen del supervisor."""
        textos_eje, mapa_eje = opciones_selectbox("eje")
        textos_politica, mapa_politica = opciones_selectbox("politica_gobierno")
        assert len(textos_eje) >= 3 and len(textos_politica) >= 3

        eje_principal_id = mapa_eje[textos_eje[0]]
        pol_principal_id = mapa_politica[textos_politica[0]]
        par_a = (mapa_eje[textos_eje[1]], mapa_politica[textos_politica[1]])
        par_b = (mapa_eje[textos_eje[2]], mapa_politica[textos_politica[2]])

        id_ind = _crear_indicador_publicado(
            "P36-EJREORD", eje_principal_id, pol_principal_id
        )
        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-EJREORD",
                "indicador": "Indicador P36-EJREORD",
                "estado_publicacion": "borrador",
                "eje_id": eje_principal_id,
                "politica_gobierno_id": pol_principal_id,
                "_ejes_politicas_extra": [par_a, par_b],
            },
            {},
            usuario_id=1,
        )
        assert ok, msg
        # Se aprueba para simular que ya es contenido publicado y limpiar
        # la revisión pendiente antes de la segunda edición.
        from models.crud_indicadores import aprobar_publicacion_indicador
        aprobar_publicacion_indicador(id_ind, usuario_id=1)

        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-EJREORD",
                "indicador": "Indicador P36-EJREORD",
                "estado_publicacion": "borrador",
                "eje_id": eje_principal_id,
                "politica_gobierno_id": pol_principal_id,
                "_ejes_politicas_extra": [par_b, par_a],
            },
            {},
            usuario_id=1,
        )
        assert ok, msg

        _, cambios = _leer_revision(id_ind)
        campos = {c["campo"] for c in cambios}
        assert "Ejes/Políticas de gobierno adicionales" not in campos, (
            "Reordenar el mismo conjunto de ejes/políticas adicionales no "
            "debería reportarse como un cambio de contenido."
        )


class TestDeteccionCampoPersonalizado:
    """Mismo bug de fondo que los ejes adicionales, pero para los campos
    personalizados de Auxiliares (tabla EAV indicador_campos_personalizados)."""

    def test_editar_campo_personalizado_aparece_en_revision_detalle(self, sidoe_config):
        _, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        eje_id = next(iter(mapa_eje.values()))
        politica_id = next(iter(mapa_politica.values()))

        ok, msg = crear_categoria(
            "p36_cat_custom", "Campo Personalizado P36", aplica_a="indicador",
            usuario_id=1,
        )
        assert ok, msg
        ok, msg, valor_1_id = crear_valor("p36_cat_custom", "Valor Uno", usuario_id=1)
        assert ok, msg
        ok, msg, valor_2_id = crear_valor("p36_cat_custom", "Valor Dos", usuario_id=1)
        assert ok, msg

        _, mapa_custom = opciones_selectbox("p36_cat_custom")
        categoria_id = None
        import data.database as db_mod
        conn = db_mod.obtener_conexion()
        categoria_id = conn.execute(
            "SELECT id FROM auxiliares_categorias WHERE clave = 'p36_cat_custom'"
        ).fetchone()[0]
        conn.close()

        id_ind = _crear_indicador_publicado("P36-CUSTOM", eje_id, politica_id)
        # Valor inicial del campo personalizado (aún en 'publicado', se
        # aprueba después para simular contenido ya visible al público).
        modificar_indicador(
            id_ind,
            {
                "codigo": "P36-CUSTOM", "indicador": "Indicador P36-CUSTOM",
                "estado_publicacion": "borrador",
            },
            {},
            usuario_id=1,
            campos_personalizados_indicador={categoria_id: valor_1_id},
        )
        from models.crud_indicadores import aprobar_publicacion_indicador
        aprobar_publicacion_indicador(id_ind, usuario_id=1)

        # Edición real: solo cambia el campo personalizado, nada más.
        ok, msg = modificar_indicador(
            id_ind,
            {
                "codigo": "P36-CUSTOM", "indicador": "Indicador P36-CUSTOM",
                "estado_publicacion": "borrador",
            },
            {},
            usuario_id=1,
            campos_personalizados_indicador={categoria_id: valor_2_id},
        )
        assert ok, msg

        _, cambios = _leer_revision(id_ind)
        entradas = {c["campo"]: c for c in cambios}
        assert "Campo Personalizado P36" in entradas, (
            f"El cambio de campo personalizado no aparece en el resumen. "
            f"Campos detectados: {list(entradas.keys())}"
        )
        cambio = entradas["Campo Personalizado P36"]
        assert cambio["anterior"] == "Valor Uno"
        assert cambio["nuevo"] == "Valor Dos"
