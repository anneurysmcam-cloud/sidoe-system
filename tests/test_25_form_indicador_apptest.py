"""
tests/test_25_form_indicador_apptest.py
========================================
Tests E2E con streamlit.testing.v1.AppTest para views/crear_indicador.py y
views/actualizar_indicador.py — las dos vistas de formulario más grandes y
las que se refactorizaron para deduplicar helpers/vocabulario compartidos
en views/_form_indicador_shared.py y config.py (julio-2026).

Motivación (por qué AppTest y no solo cobertura de línea):
  El bug de sincronizar_indicadores_referenciados() (julio-2026) no fue
  detectado por cobertura de línea al 100% porque los tests de regresión
  ejercitaban solo el camino "feliz" (títulos coincidentes). El riesgo
  equivalente aquí es la refactorización de _sb_edit/_sb_edit_opcional en
  un único selectbox_auxiliar(id_actual=...): un test unitario de la
  función helper no garantiza que, dentro del render real del formulario,
  el ID correcto llegue hasta el selectbox correcto. Estos tests corren
  app.py de punta a punta (igual que test_17/test_18) para cubrir ese
  camino de integración.

Alcance deliberadamente acotado (no exhaustivo campo por campo):
  - Guardar un indicador nuevo con el formulario completo.
  - Validación de campos obligatorios vacíos.
  - Validación de consistencia "sin fuente".
  - Preselección correcta de un valor NO trivial (no el índice 0) al
    editar — la clase de regresión más valiosa para esta vista.
  - Editar y guardar cambios.
  - Agregar una fuente nueva.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

# Import a nivel de módulo (no solo dentro de las funciones de test) para
# garantizar que sidoe.db exista antes de que conftest.py intente
# copiarlo (efecto lateral del bootstrap en data/database.py). Sin esto,
# el archivo solo funciona si se corre junto a otro módulo que ya haga
# este import durante la fase de collection de pytest — ver
# test_24_migracion_normalizacion_auxiliares_texto_libre.py, que hoy es el
# único que lo hace y de quien dependía implícitamente el resto de la suite.
import data.database  # noqa: F401
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

FACT_MAX = {
    "c1_metodologia": "Indicador con metodología nacional o internacional definida",
    "c21_existencia_fuente": "Completamente",
    "c22_disponibilidad": "Sí",
    "c23_periodicidad_establecida": "Sí",
    "c31_posee_desagregacion": "Sí",
    "num_desagregaciones_requeridas": 1,
    "num_desagregaciones_disponibles": 1,
    "articulacion_fuentes": "No requiere de articulación",
    "armonizacion_conceptual": "No",
    "subregistro_cobertura": "No",
    "cobertura_territorial": "Sí",
    "estructura_datos": (
        "a) La fuente de información utiliza en el procesamiento "
        "una base de datos estructurada"
    ),
    "variables_calculo": "Sí",
}


def _login_editor(at: AppTest) -> None:
    at.session_state["usuario"] = {
        "id": 1, "username": "p25_editor_apptest", "rol": "editor",
    }


def _navegar_a(at: AppTest, opcion: str) -> None:
    at.sidebar.radio[0].set_value(opcion).run()
    assert not at.exception


def _selectbox_por_label(at: AppTest, label: str, ocurrencia: int = 0):
    coincidencias = [sb for sb in at.selectbox if sb.label == label]
    assert coincidencias, f"No se encontró ningún selectbox con label={label!r}"
    return coincidencias[ocurrencia]


def _text_input_por_label(at: AppTest, label: str, ocurrencia: int = 0):
    coincidencias = [ti for ti in at.text_input if ti.label == label]
    assert coincidencias, f"No se encontró ningún text_input con label={label!r}"
    return coincidencias[ocurrencia]


def _sembrar_catalogos_vacios() -> None:
    """Siembra un valor en las 3 categorías de Auxiliares que arrancan
    vacías por diseño (área_misional_one, institución_productora,
    nombre_fuente — ver config.py: se pueblan vía el backfill de ETL sobre
    datos reales, no con valores iniciales). Sin esto, sus selectbox no
    tienen ninguna opción y cualquier envío de formulario que dependa de
    ellas queda bloqueado por la validación de campos obligatorios."""
    from models.crud_auxiliares import crear_valor

    for clave, valor in [
        ("area_misional_one", "Estadísticas Sociales"),
        ("institucion_productora", "ONE"),
        ("nombre_fuente", "Encuesta semilla p25"),
    ]:
        ok, msg, _id = crear_valor(clave, valor, usuario_id=1)
        assert ok or "ya existe" in msg, f"Falló sembrar {clave}: {msg}"


def _crear_indicador_seed(codigo: str, eje_id: int, nombre: str = "Indicador semilla p25") -> None:
    from models.crud_indicadores import guardar_indicador

    ok, msg = guardar_indicador(
        datos_indicador={
            "codigo": codigo,
            "indicador": nombre,
            "estado_indicador": "Activo",
            "generador_demanda_id": 1,
            "eje_id": eje_id,
        },
        datos_fuentes=[{
            "nombre_fuente": f"Fuente semilla de {codigo}",
            "institucion_productora": "ONE Test",
        }],
        datos_factibilidad=FACT_MAX,
        usuario_id=1,
    )
    assert ok is True, f"Falló crear el indicador semilla: {msg}"


class TestCrearIndicadorAppTest:

    def test_formulario_completo_guarda_indicador_correctamente(self, sidoe_config):
        _sembrar_catalogos_vacios()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        assert not at.exception

        _navegar_a(at, "Crear Nuevo Indicador")

        _text_input_por_label(at, "Código Único del Indicador").set_value("P25-CREAR-01")
        _text_input_por_label(at, "Nombre Oficial del Indicador").set_value("Indicador creado por AppTest")
        _text_input_por_label(at, "Ente responsable de metodología").set_value("ONE")
        _text_input_por_label(at, "Numerador").set_value("Casos")
        _text_input_por_label(at, "Denominador").set_value("Total")
        _text_input_por_label(at, "Unidad de medida").set_value("Porcentaje")
        _text_input_por_label(at, "Hipervínculo del último cálculo").set_value("http://example.org")
        _text_input_por_label(at, "Año del último dato disponible").set_value("2026")

        # El resto de los campos 🧩 respaldados por Auxiliares quedan en su
        # valor por defecto (índice 0), que es válido (no None) — eso es
        # justamente lo que garantiza selectbox_auxiliar() sin opcional=True.
        #
        # Excepciones: 2 valores por defecto (índice 0, orden alfabético de
        # Auxiliares) son inconsistentes con OTRO campo bajo las
        # validaciones de views/_validaciones_consistencia.py (formulario de
        # consistencia, agosto-2026) y deben fijarse explícitamente para que
        # el "camino feliz" de este test siga siendo un envío realmente
        # válido:
        #   - 🧩 Requerimiento de clasificación por defecto es "No" (orden
        #     alfabético: No, No identificada, Si), que exige "Uso de
        #     Clasificaciones" = "No requerida" (su default es "Sí").
        #   - "C3.1 Posee desagregación requerida" por defecto es "Sí" (voca-
        #     bulario fijo, no alfabético), que exige "Desagregaciones
        #     requeridas por el indicador" > 0 (su default es 0) — se deja
        #     en "No" en vez de tocar el number_input, ya que "No" sí es
        #     consistente con el 0 por defecto.
        _selectbox_por_label(at, "Uso de Clasificaciones").set_value("No requerida")
        _selectbox_por_label(at, "C3.1 Posee desagregación requerida").set_value("No")

        boton_guardar = next(b for b in at.button if b.label == "💾 Guardar Nuevo Indicador")
        boton_guardar.click().run()
        assert not at.exception
        assert at.success

        from data.database import obtener_conexion
        conn = obtener_conexion()
        fila = conn.execute(
            "SELECT codigo, indicador FROM indicadores WHERE codigo = ?", ("P25-CREAR-01",)
        ).fetchone()
        conn.close()
        assert fila is not None
        assert fila[1] == "Indicador creado por AppTest"

    def test_campos_obligatorios_vacios_muestra_error_y_no_guarda(self, sidoe_config):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Crear Nuevo Indicador")

        boton_guardar = next(b for b in at.button if b.label == "💾 Guardar Nuevo Indicador")
        boton_guardar.click().run()
        assert not at.exception
        assert at.error
        assert "obligatorios" in at.error[0].value.lower()

        from data.database import obtener_conexion
        conn = obtener_conexion()
        total = conn.execute("SELECT COUNT(*) FROM indicadores").fetchone()[0]
        conn.close()
        assert total == 0

    def test_inconsistencia_sin_fuente_muestra_error(self, sidoe_config):
        _sembrar_catalogos_vacios()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Crear Nuevo Indicador")

        _text_input_por_label(at, "Código Único del Indicador").set_value("P25-CREAR-02")
        _text_input_por_label(at, "Nombre Oficial del Indicador").set_value("Indicador sin fuente")
        _text_input_por_label(at, "Ente responsable de metodología").set_value("ONE")
        _text_input_por_label(at, "Numerador").set_value("Casos")
        _text_input_por_label(at, "Denominador").set_value("Total")
        _text_input_por_label(at, "Unidad de medida").set_value("Porcentaje")
        _text_input_por_label(at, "Hipervínculo del último cálculo").set_value("http://example.org")
        _text_input_por_label(at, "Año del último dato disponible").set_value("2026")

        # "Existencia de fuente" = "No hay fuente", pero C2.1 se deja en su
        # valor por defecto ("Completamente"), que NO es el negativo
        # esperado -> debe activar _errores_consistencia_sin_fuente.
        _selectbox_por_label(at, "🧩 Existencia de fuente").set_value("No hay fuente")

        boton_guardar = next(b for b in at.button if b.label == "💾 Guardar Nuevo Indicador")
        boton_guardar.click().run()
        assert not at.exception
        assert at.error
        assert "no hay fuente" in at.error[0].value.lower()

        from data.database import obtener_conexion
        conn = obtener_conexion()
        fila = conn.execute(
            "SELECT 1 FROM indicadores WHERE codigo = ?", ("P25-CREAR-02",)
        ).fetchone()
        conn.close()
        assert fila is None


class TestActualizarIndicadorAppTest:

    def test_preselecciona_el_valor_actual_no_trivial_del_indicador(self, sidoe_config):
        """Regresión dirigida: el ID guardado debe traducirse al TEXTO
        correcto en el selectbox al editar, incluso cuando ese valor no es
        la primera opción de la lista (índice 0) — que es precisamente el
        caso donde un bug de preselección pasaría desapercibido."""
        from models.crud_auxiliares import opciones_selectbox

        textos_eje, mapa_eje = opciones_selectbox("eje")
        # "Eje 3: Productivo" es la 3ra opción (índice 2) de los 5 valores
        # oficiales sembrados en config.py; si la preselección fuera trivial
        # (siempre índice 0) esta prueba lo detecta.
        assert "Eje 3: Productivo" in textos_eje, (
            f"Seed inesperado de Auxiliares: {textos_eje}"
        )
        eje_id_no_trivial = mapa_eje["Eje 3: Productivo"]

        _crear_indicador_seed("P25-ACT-01", eje_id=eje_id_no_trivial)

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Actualizar Indicador")

        opciones_sel = at.selectbox[0]  # "Seleccione el indicador a modificar:"
        assert opciones_sel.label == "Seleccione el indicador a modificar:"
        coincidencia = next(o for o in opciones_sel.options if o.startswith("P25-ACT-01"))
        opciones_sel.set_value(coincidencia).run()
        assert not at.exception

        sb_eje = _selectbox_por_label(at, "🧩 Eje")
        assert sb_eje.value == "Eje 3: Productivo"

    def test_editar_nombre_y_guardar_persiste_el_cambio(self, sidoe_config):
        from models.crud_auxiliares import opciones_selectbox

        _, mapa_eje = opciones_selectbox("eje")
        eje_id = next(iter(mapa_eje.values()))
        _crear_indicador_seed("P25-ACT-02", eje_id=eje_id, nombre="Nombre original")

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Actualizar Indicador")

        opciones_sel = at.selectbox[0]
        coincidencia = next(o for o in opciones_sel.options if o.startswith("P25-ACT-02"))
        opciones_sel.set_value(coincidencia).run()
        assert not at.exception

        _text_input_por_label(at, "Nombre del Indicador").set_value("Nombre editado por AppTest")

        # Igual que en test_formulario_completo_guarda_indicador_correctamente:
        # el indicador semilla (_crear_indicador_seed) no fija
        # requerimiento_clasificacion_id, así que el formulario lo precarga
        # en su default "No" — inconsistente con "Uso de Clasificaciones",
        # que FACT_MAX sí fija en "Sí". Se ajusta aquí para que el envío sea
        # consistente bajo views/_validaciones_consistencia.py. (ficha_tecnica
        # y C1 no requieren ajuste: el default de ficha_tecnica es "Definido"
        # -orden alfabético de Auxiliares-, ya consistente con el C1 que
        # FACT_MAX fija en la opción de metodología definida.)
        _selectbox_por_label(at, "Uso de Clasificaciones").set_value("No requerida")

        boton_aplicar = next(b for b in at.button if b.label == "⚡ Aplicar Cambios y Recalcular Factibilidad")
        boton_aplicar.click().run()
        assert not at.exception
        assert at.success

        from models.crud_indicadores import obtener_indicador_por_id
        from data.database import obtener_conexion
        conn = obtener_conexion()
        fila = conn.execute("SELECT id FROM indicadores WHERE codigo = ?", ("P25-ACT-02",)).fetchone()
        conn.close()
        assert obtener_indicador_por_id(fila[0])["indicador"]["indicador"] == "Nombre editado por AppTest"

    def test_cambiar_de_indicador_no_deja_estado_de_ejes_adicionales_pegado(
        self, sidoe_config
    ):
        """Regresión dirigida (bug reportado por Randy): 'Eje adicional 1'/
        'Política adicional 1' usaban una key de widget ESTÁTICA (sin el id
        del indicador). Streamlit conserva el valor de session_state de esa
        key entre reruns e ignora el id_actual recalculado — así que, en la
        misma sesión, pasar de un indicador SIN eje adicional a uno CON eje
        adicional guardado (p. ej. vía "✏️ Editar antes de aprobar" después
        de haber visto otro indicador) mostraba el campo vacío/con el valor
        del indicador anterior en vez del real: el cambio "se veía borrado"
        aunque seguía intacto en la base de datos."""
        from models.crud_auxiliares import opciones_selectbox
        from models.crud_indicadores import modificar_indicador

        textos_eje, mapa_eje = opciones_selectbox("eje")
        _, mapa_politica = opciones_selectbox("politica_gobierno")
        assert len(textos_eje) >= 2
        eje_principal_id = mapa_eje[textos_eje[0]]
        eje_extra_id = mapa_eje[textos_eje[1]]
        pol_extra_id = next(iter(mapa_politica.values()))

        # A: sin eje adicional.
        _crear_indicador_seed("P25-ACT-04A", eje_id=eje_principal_id, nombre="Indicador A sin extra")
        # B: con un eje/política adicional ya guardado.
        _crear_indicador_seed("P25-ACT-04B", eje_id=eje_principal_id, nombre="Indicador B con extra")
        from data.database import obtener_conexion
        conn = obtener_conexion()
        id_b = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = ?", ("P25-ACT-04B",)
        ).fetchone()[0]
        conn.close()
        ok, msg = modificar_indicador(
            id_b,
            {
                "codigo": "P25-ACT-04B", "indicador": "Indicador B con extra",
                "estado_publicacion": "borrador",
                "_ejes_politicas_extra": [(eje_extra_id, pol_extra_id)],
            },
            {}, usuario_id=1,
        )
        assert ok, msg

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Actualizar Indicador")

        opciones_sel = at.selectbox[0]
        # 1) Se selecciona primero A (sin eje adicional) — deja su huella
        #    en session_state antes de que la reproducción del bug importe.
        coincidencia_a = next(o for o in opciones_sel.options if o.startswith("P25-ACT-04A"))
        opciones_sel.set_value(coincidencia_a).run()
        assert not at.exception
        sb_extra_a = _selectbox_por_label(at, "Eje adicional 1")
        assert sb_extra_a.value == "— Ninguno —"

        # 2) Se cambia a B (SÍ tiene eje adicional guardado), en la MISMA
        #    sesión/instancia de AppTest — este es el escenario del bug.
        opciones_sel = at.selectbox[0]
        coincidencia_b = next(o for o in opciones_sel.options if o.startswith("P25-ACT-04B"))
        opciones_sel.set_value(coincidencia_b).run()
        assert not at.exception

        sb_extra_b = _selectbox_por_label(at, "Eje adicional 1")
        assert sb_extra_b.value == textos_eje[1], (
            "El campo 'Eje adicional 1' muestra un valor obsoleto del "
            "indicador anterior en vez del eje adicional real de este "
            f"indicador (mostró {sb_extra_b.value!r})."
        )

    def test_agregar_fuente_nueva_incrementa_el_conteo_de_fuentes(self, sidoe_config):
        """Este bloque estaba pegado, sin `def` propio, al final del test
        anterior (test_cambiar_de_indicador_no_deja_estado_de_ejes_...) —
        corría como parte de ese test en vez de como caso independiente.
        Separado a petición de Randy; sin cambios de lógica."""
        _sembrar_catalogos_vacios()
        from models.crud_auxiliares import opciones_selectbox

        _, mapa_eje = opciones_selectbox("eje")
        eje_id = next(iter(mapa_eje.values()))
        _crear_indicador_seed("P25-ACT-03", eje_id=eje_id)

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Actualizar Indicador")

        opciones_sel = at.selectbox[0]
        coincidencia = next(o for o in opciones_sel.options if o.startswith("P25-ACT-03"))
        opciones_sel.set_value(coincidencia).run()
        assert not at.exception

        from models.crud_indicadores import obtener_indicador_por_id
        from data.database import obtener_conexion
        conn = obtener_conexion()
        id_mod = conn.execute("SELECT id FROM indicadores WHERE codigo = ?", ("P25-ACT-03",)).fetchone()[0]
        conn.close()
        total_antes = len(obtener_indicador_por_id(id_mod)["fuentes"])

        # El resto de campos 🧩 de la sección "Agregar" quedan en su valor
        # por defecto (índice 0) — nombre_fuente no es opcional=True, así
        # que ese valor por defecto ya es válido (no None).
        boton_agregar = next(b for b in at.button if b.label == "➕ Agregar fuente")
        boton_agregar.click().run()
        assert not at.exception
        assert at.success

        total_despues = len(obtener_indicador_por_id(id_mod)["fuentes"])
        assert total_despues == total_antes + 1

    def test_agregar_solo_eje_extra_sin_politica_persiste_y_aparece_en_revision(
        self, sidoe_config
    ):
        """Bug reportado por Randy: agregar un eje/política adicional solo
        aparecía como cambio pendiente de aprobar si se llenaban AMBOS
        lados del par (eje Y política). Si solo se llenaba uno de los dos
        (aquí: solo el eje adicional, dejando la política adicional en
        '— Ninguno —'), el par se descartaba en silencio antes de
        guardarse — el filtro de la vista exigía `eje and politica` pese a
        que la tabla permite un lado nulo y sincronizar_ejes_politicas()
        ya lo soporta."""
        from models.crud_auxiliares import opciones_selectbox

        textos_eje, mapa_eje = opciones_selectbox("eje")
        assert len(textos_eje) >= 2
        eje_principal_id = mapa_eje[textos_eje[0]]
        eje_extra_id = mapa_eje[textos_eje[1]]

        _crear_indicador_seed(
            "P25-ACT-05", eje_id=eje_principal_id, nombre="Indicador solo eje extra"
        )
        from data.database import obtener_conexion
        conn = obtener_conexion()
        id_ind = conn.execute(
            "SELECT id FROM indicadores WHERE codigo = ?", ("P25-ACT-05",)
        ).fetchone()[0]
        conn.execute(
            "UPDATE indicadores SET estado_publicacion = 'publicado' WHERE id = ?",
            (id_ind,),
        )
        conn.commit()
        conn.close()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        _login_editor(at)
        at.run()
        _navegar_a(at, "Actualizar Indicador")

        opciones_sel = at.selectbox[0]
        coincidencia = next(o for o in opciones_sel.options if o.startswith("P25-ACT-05"))
        opciones_sel.set_value(coincidencia).run()
        assert not at.exception

        # Igual que en test_editar_nombre_y_guardar_persiste_el_cambio: el
        # seed no fija requerimiento_clasificacion_id (default "No"), así
        # que "Uso de Clasificaciones" se ajusta para no chocar con
        # views/_validaciones_consistencia.py.
        _selectbox_por_label(at, "Uso de Clasificaciones").set_value("No requerida")

        # Solo se llena el eje adicional 1 — la política adicional 1 se
        # deja en su default "— Ninguno —" (sin seleccionar).
        _selectbox_por_label(at, "Eje adicional 1").set_value(textos_eje[1])

        boton_aplicar = next(
            b for b in at.button if b.label == "⚡ Aplicar Cambios y Recalcular Factibilidad"
        )
        boton_aplicar.click().run()
        assert not at.exception
        assert at.success

        from models.crud_indicadores import obtener_ejes_politicas_extra
        pares = obtener_ejes_politicas_extra(id_ind)
        assert pares == [(eje_extra_id, None)], (
            f"El par parcial (solo eje, sin política) no se guardó: {pares}"
        )

        conn = obtener_conexion()
        tipo, detalle = conn.execute(
            "SELECT revision_tipo, revision_detalle FROM indicadores WHERE id = ?",
            (id_ind,),
        ).fetchone()
        conn.close()
        import json
        cambios = json.loads(detalle) if detalle else []
        assert tipo == "actualizado"
        campos = {c["campo"] for c in cambios}
        assert "Ejes/Políticas de gobierno adicionales" in campos, (
            f"El eje adicional agregado (sin política) no aparece en el "
            f"resumen de cambios para el supervisor. Campos: {campos}"
        )
