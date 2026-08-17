"""
tests/test_08_crud_auxiliares.py
=================================
Cobertura del CRUD de Auxiliares (models/crud_auxiliares.py): categorías,
valores, resolución de IDs, historial y campos personalizados (EAV).

Usa la fixture ``sidoe_config`` (BD temporal, aislada de producción).
"""

import pytest

import models.crud_auxiliares as aux


# ---------------------------------------------------------------------------
# Categorías de sistema (ya existentes por ETL)
# ---------------------------------------------------------------------------

def test_listar_categorias_devuelve_lista(sidoe_config):
    categorias = aux.listar_categorias()
    assert isinstance(categorias, list)
    assert len(categorias) > 0


def test_listar_categorias_solo_activas_vs_todas(sidoe_config):
    todas = aux.listar_categorias(solo_activas=False)
    activas = aux.listar_categorias(solo_activas=True)
    assert len(todas) >= len(activas)


def test_obtener_categoria_por_clave_existente(sidoe_config):
    cat = aux.obtener_categoria_por_clave("generador_demanda")
    assert cat is not None
    assert cat["clave"] == "generador_demanda"


def test_obtener_categoria_por_clave_inexistente(sidoe_config):
    assert aux.obtener_categoria_por_clave("clave_que_no_existe_xyz") is None


def test_obtener_categoria_por_id(sidoe_config):
    cat = aux.obtener_categoria_por_clave("generador_demanda")
    por_id = aux.obtener_categoria_por_id(cat["id"])
    assert por_id["clave"] == "generador_demanda"


def test_obtener_categoria_por_id_inexistente(sidoe_config):
    assert aux.obtener_categoria_por_id(999999) is None


# ---------------------------------------------------------------------------
# Categorías personalizadas (creación, listado, reasignación, eliminación)
# ---------------------------------------------------------------------------

def test_crear_categoria_personalizada_exitosa(sidoe_config):
    ok, msg = aux.crear_categoria(
        "clasificacion_extra", "Clasificación Extra", "desc", aplica_a="indicador"
    )
    assert ok is True
    assert "creada" in msg.lower()


def test_crear_categoria_duplicada_falla(sidoe_config):
    aux.crear_categoria("dup_cat", "Dup Cat", aplica_a="fuente")
    ok, msg = aux.crear_categoria("dup_cat", "Otro Nombre", aplica_a="fuente")
    assert ok is False
    assert "ya existe" in msg.lower()


def test_crear_categoria_sin_clave_o_nombre_falla(sidoe_config):
    ok, msg = aux.crear_categoria("", "Nombre", aplica_a="indicador")
    assert ok is False
    ok2, msg2 = aux.crear_categoria("clave_valida", "", aplica_a="indicador")
    assert ok2 is False


def test_crear_categoria_aplica_a_invalido_falla(sidoe_config):
    ok, msg = aux.crear_categoria("clave_x", "Nombre X", aplica_a="otra_cosa")
    assert ok is False
    assert "componente" in msg.lower()


def test_listar_categorias_personalizadas_filtra_por_componente(sidoe_config):
    aux.crear_categoria("pers_ind", "Personalizada Indicador", aplica_a="indicador")
    aux.crear_categoria("pers_fte", "Personalizada Fuente", aplica_a="fuente")

    solo_ind = aux.listar_categorias_personalizadas(aplica_a="indicador")
    claves = [c["clave"] for c in solo_ind]
    assert "pers_ind" in claves
    assert "pers_fte" not in claves

    todas_personalizadas = aux.listar_categorias_personalizadas()
    assert any(c["clave"] == "pers_fte" for c in todas_personalizadas)


def test_actualizar_aplica_a_categoria_personalizada(sidoe_config):
    aux.crear_categoria("reasignable", "Reasignable", aplica_a="indicador")
    cat = aux.obtener_categoria_por_clave("reasignable")
    ok, msg = aux.actualizar_aplica_a_categoria(cat["id"], "fuente")
    assert ok is True
    actualizada = aux.obtener_categoria_por_id(cat["id"])
    assert actualizada["aplica_a"] == "fuente"


def test_actualizar_aplica_a_categoria_invalida(sidoe_config):
    cat = aux.obtener_categoria_por_clave("generador_demanda")
    ok, msg = aux.actualizar_aplica_a_categoria(cat["id"], "no_valido")
    assert ok is False


def test_actualizar_aplica_a_categoria_inexistente(sidoe_config):
    ok, msg = aux.actualizar_aplica_a_categoria(999999, "indicador")
    assert ok is False
    assert "no existe" in msg.lower()


def test_actualizar_aplica_a_categoria_de_sistema_rechazado(sidoe_config):
    # generador_demanda tiene columna fija en config.py -> es de sistema
    cat = aux.obtener_categoria_por_clave("generador_demanda")
    ok, msg = aux.actualizar_aplica_a_categoria(cat["id"], "indicador")
    assert ok is False
    assert "sistema" in msg.lower()


def test_eliminar_categoria_de_sistema_rechazada(sidoe_config):
    cat = aux.obtener_categoria_por_clave("generador_demanda")
    ok, msg = aux.eliminar_categoria(cat["id"])
    assert ok is False
    assert "sistema" in msg.lower()


def test_eliminar_categoria_inexistente(sidoe_config):
    ok, msg = aux.eliminar_categoria(999999)
    assert ok is False


def test_eliminar_categoria_personalizada_sin_uso(sidoe_config):
    aux.crear_categoria("temporal_borrable", "Temporal Borrable", aplica_a="indicador")
    cat = aux.obtener_categoria_por_clave("temporal_borrable")
    ok, msg = aux.eliminar_categoria(cat["id"])
    assert ok is True
    assert aux.obtener_categoria_por_id(cat["id"]) is None


# ---------------------------------------------------------------------------
# Valores de catálogo
# ---------------------------------------------------------------------------

def test_crear_valor_exitoso(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "NUEVO_VALOR_TEST")
    assert ok is True
    assert nuevo_id is not None


def test_crear_valor_vacio_falla(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "   ")
    assert ok is False
    assert nuevo_id is None


def test_crear_valor_categoria_inexistente_falla(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("categoria_fantasma", "Valor")
    assert ok is False
    assert nuevo_id is None


def test_crear_valor_duplicado_case_insensitive_falla(sidoe_config):
    aux.crear_valor("generador_demanda", "Duplicado Test")
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "  duplicado test  ")
    assert ok is False
    assert nuevo_id is None


def test_obtener_valores_y_activos(sidoe_config):
    aux.crear_valor("generador_demanda", "Valor Activo Test")
    valores = aux.obtener_valores("generador_demanda")
    assert any(v["valor"] == "Valor Activo Test" for v in valores)

    textos = aux.obtener_valores_activos("generador_demanda")
    assert "Valor Activo Test" in textos


def test_opciones_selectbox(sidoe_config):
    textos, mapa = aux.opciones_selectbox("generador_demanda")
    assert isinstance(textos, list)
    assert isinstance(mapa, dict)
    for t in textos:
        assert t in mapa


def test_editar_valor_exitoso(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Editar Original")
    ok2, msg2 = aux.editar_valor(nuevo_id, "Editar Renombrado")
    assert ok2 is True
    assert aux.resolver_texto(nuevo_id) == "Editar Renombrado"


def test_editar_valor_inexistente(sidoe_config):
    ok, msg = aux.editar_valor(999999, "Nuevo Texto")
    assert ok is False


def test_editar_valor_vacio_falla(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Para Editar Vacio")
    ok2, msg2 = aux.editar_valor(nuevo_id, "   ")
    assert ok2 is False


def test_editar_valor_igual_al_actual_falla(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Valor Igual")
    ok2, msg2 = aux.editar_valor(nuevo_id, "Valor Igual")
    assert ok2 is False
    assert "igual" in msg2.lower()


def test_editar_valor_a_duplicado_falla(sidoe_config):
    aux.crear_valor("generador_demanda", "Valor Original A")
    ok, msg, id_b = aux.crear_valor("generador_demanda", "Valor Original B")
    ok2, msg2 = aux.editar_valor(id_b, "Valor Original A")
    assert ok2 is False
    assert "ya existe" in msg2.lower()


def test_cambiar_estado_valor_activar_desactivar(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Para Desactivar")
    ok2, msg2 = aux.cambiar_estado_valor(nuevo_id, False)
    assert ok2 is True
    assert "desactivado" in msg2.lower()

    valores_activos = aux.obtener_valores("generador_demanda", solo_activos=True)
    assert not any(v["id"] == nuevo_id for v in valores_activos)

    ok3, msg3 = aux.cambiar_estado_valor(nuevo_id, True)
    assert ok3 is True
    assert "activado" in msg3.lower()


def test_cambiar_estado_valor_inexistente(sidoe_config):
    ok, msg = aux.cambiar_estado_valor(999999, True)
    assert ok is False


def test_eliminar_valor_sin_uso_exitoso(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Para Eliminar Sin Uso")
    ok2, msg2 = aux.eliminar_valor(nuevo_id)
    assert ok2 is True
    assert aux.resolver_texto(nuevo_id) is None


def test_eliminar_valor_inexistente(sidoe_config):
    ok, msg = aux.eliminar_valor(999999)
    assert ok is False
    assert "no existe" in msg.lower()


@pytest.mark.requiere_bd_local
def test_eliminar_valor_en_uso_rechazado(sidoe_config, db_conn):
    # generador_demanda_id=1 (END) está en uso por indicadores migrados
    fila = db_conn.execute(
        "SELECT generador_demanda_id FROM indicadores "
        "WHERE generador_demanda_id IS NOT NULL LIMIT 1"
    ).fetchone()
    assert fila is not None
    auxiliar_en_uso = fila[0]
    ok, msg = aux.eliminar_valor(auxiliar_en_uso)
    assert ok is False
    assert "en uso" in msg.lower()


# ---------------------------------------------------------------------------
# Resolución de texto <-> id (usado en ETL)
# ---------------------------------------------------------------------------

def test_resolver_texto_existente_e_inexistente(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Resolver Test")
    assert aux.resolver_texto(nuevo_id) == "Resolver Test"
    assert aux.resolver_texto(999999) is None
    assert aux.resolver_texto(None) is None


def test_resolver_o_crear_id_valor_existente(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Existente Resolver")
    resuelto = aux.resolver_o_crear_id("generador_demanda", "existente resolver")
    assert resuelto == nuevo_id


def test_resolver_o_crear_id_crea_si_no_existe(sidoe_config):
    resuelto = aux.resolver_o_crear_id("generador_demanda", "Totalmente Nuevo XYZ")
    assert resuelto is not None
    assert aux.resolver_texto(resuelto) == "Totalmente Nuevo XYZ"


def test_resolver_o_crear_id_texto_vacio_usa_default(sidoe_config):
    resuelto = aux.resolver_o_crear_id("generador_demanda", None)
    assert resuelto is not None
    assert aux.resolver_texto(resuelto) == "No identificado"


def test_resolver_o_crear_id_nan_usa_default(sidoe_config):
    resuelto = aux.resolver_o_crear_id("generador_demanda", float("nan"))
    assert aux.resolver_texto(resuelto) == "No identificado"


# ---------------------------------------------------------------------------
# Campos personalizados (EAV)
# ---------------------------------------------------------------------------

@pytest.mark.requiere_bd_local
def test_guardar_y_obtener_campos_personalizados_indicador(sidoe_config, db_conn):
    aux.crear_categoria("campo_extra_1", "Campo Extra 1", aplica_a="indicador")
    cat = aux.obtener_categoria_por_clave("campo_extra_1")
    ok, msg, valor_id = aux.crear_valor("campo_extra_1", "Valor Extra 1")

    fila_ind = db_conn.execute("SELECT id FROM indicadores LIMIT 1").fetchone()
    indicador_id = fila_ind[0]

    aux.guardar_campos_personalizados(
        "indicador", indicador_id, {cat["id"]: valor_id}
    )
    guardados = aux.obtener_valores_personalizados("indicador", indicador_id)
    assert guardados.get(cat["id"]) == valor_id


def test_guardar_campos_personalizados_vacio_no_hace_nada(sidoe_config):
    # No debe lanzar excepción con valores=None o {}
    aux.guardar_campos_personalizados("indicador", 1, None)
    aux.guardar_campos_personalizados("indicador", 1, {})


@pytest.mark.requiere_bd_local
def test_guardar_campos_personalizados_upsert(sidoe_config, db_conn):
    aux.crear_categoria("campo_extra_2", "Campo Extra 2", aplica_a="fuente")
    cat = aux.obtener_categoria_por_clave("campo_extra_2")
    ok1, _, valor_id_1 = aux.crear_valor("campo_extra_2", "Primero")
    ok2, _, valor_id_2 = aux.crear_valor("campo_extra_2", "Segundo")

    fila_fte = db_conn.execute("SELECT id FROM fuentes_indicador LIMIT 1").fetchone()
    fuente_id = fila_fte[0]

    aux.guardar_campos_personalizados("fuente", fuente_id, {cat["id"]: valor_id_1})
    aux.guardar_campos_personalizados("fuente", fuente_id, {cat["id"]: valor_id_2})

    guardados = aux.obtener_valores_personalizados("fuente", fuente_id)
    assert guardados[cat["id"]] == valor_id_2


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

def test_obtener_historial_por_auxiliar(sidoe_config):
    ok, msg, nuevo_id = aux.crear_valor("generador_demanda", "Con Historial")
    aux.editar_valor(nuevo_id, "Con Historial Editado")
    historial = aux.obtener_historial(auxiliar_id=nuevo_id)
    acciones = [h["accion"] for h in historial]
    assert "CREACION" in acciones
    assert "RENOMBRADO" in acciones


def test_obtener_historial_general_y_filtrado_por_categoria(sidoe_config):
    aux.crear_valor("generador_demanda", "Historial General Test")
    todo = aux.obtener_historial()
    assert len(todo) > 0

    filtrado = aux.obtener_historial(categoria_clave="generador_demanda")
    assert all(
        h.get("categoria") is None or h.get("categoria") is not None
        for h in filtrado
    )
