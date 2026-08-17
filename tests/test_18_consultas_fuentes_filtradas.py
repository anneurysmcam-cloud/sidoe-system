"""
tests/test_18_consultas_fuentes_filtradas.py
=============================================
TEST DE REGRESIÓN — Punto 2 (Consultas): expander de fuentes por indicador.

Contexto del bug reportado:
  El expander "Ver fuentes de un indicador específico" en views/consultas.py
  solo permitía ver las fuentes de UN indicador a la vez (vía selectbox), lo
  que daba la impresión de que, ante un filtro con múltiples indicadores
  coincidentes, solo se mostraban las fuentes del primero de la lista.

  El export a Excel (hoja "Fuentes") ya incluía correctamente las fuentes de
  TODOS los indicadores filtrados (usa .isin(codigos_filtrados)); el gap
  estaba únicamente en la vista de pantalla.

Fix: se agregó un modo de vista ("Un indicador específico" /
  "Todos los indicadores filtrados") controlado por un st.radio dentro del
  expander. Este test verifica que, en modo "todos", aparezcan las fuentes
  de los N indicadores que matchean el filtro (no solo del primero) — el
  mismo patrón de bug de "solo se procesa/muestra el primer elemento de una
  relación 1:N" ya visto antes en el ETL y en sincronizar_indicadores_referenciados().

Usa streamlit.testing.v1.AppTest sobre app.py (entrypoint real de la app,
igual que test_17_app_modo_publico.py) navegando a "Generar Consulta", que
es la opción por defecto del menú público sin sesión.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")

from pathlib import Path

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


def _indicador(codigo: str, nombre: str) -> dict:
    return {
        "codigo": codigo,
        "indicador": nombre,
        "estado_indicador": "Activo",
        "generador_demanda_id": 1,
    }


def _fuente(nombre: str) -> dict:
    return {
        "nombre_fuente": nombre,
        "institucion_productora": "ONE Test",
    }


def _crear_dos_indicadores_con_dos_fuentes_cada_uno():
    """Crea IND-CONS-01 e IND-CONS-02, cada uno con 2 fuentes."""
    from models.crud_indicadores import guardar_indicador

    for codigo, nombre in [
        ("IND-CONS-01", "Indicador de consultas uno"),
        ("IND-CONS-02", "Indicador de consultas dos"),
    ]:
        ok, msg = guardar_indicador(
            datos_indicador=_indicador(codigo, nombre),
            datos_fuentes=[
                _fuente(f"Fuente A de {codigo}"),
                _fuente(f"Fuente B de {codigo}"),
            ],
            datos_factibilidad=FACT_MAX,
            usuario_id=1,
        )
        assert ok is True, f"Falló crear {codigo}: {msg}"


def _radio_modo_vista(at):
    """El expander de Consultas agrega su propio st.radio ('Modo de vista'),
    distinto del st.radio del menú lateral. Lo identificamos por label."""
    for r in at.radio:
        if r.label == "Modo de vista":
            return r
    raise AssertionError("No se encontró el radio 'Modo de vista' en la vista de Consultas.")


class TestExpanderFuentesFiltradas:

    def test_modo_individual_muestra_solo_fuentes_del_indicador_seleccionado(
        self, sidoe_config
    ):
        _crear_dos_indicadores_con_dos_fuentes_cada_uno()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()
        assert not at.exception

        # Modo por defecto: "Un indicador específico", selectbox toma el
        # primer código en orden alfabético (IND-CONS-01).
        tabla = at.dataframe[-1]
        assert len(tabla.value) == 2  # solo las 2 fuentes de IND-CONS-01

    def test_modo_todos_muestra_fuentes_de_todos_los_indicadores_filtrados(
        self, sidoe_config
    ):
        _crear_dos_indicadores_con_dos_fuentes_cada_uno()

        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()
        assert not at.exception

        _radio_modo_vista(at).set_value("Todos los indicadores filtrados").run()
        assert not at.exception

        tabla = at.dataframe[-1]
        # 2 indicadores x 2 fuentes = 4 filas, no solo las 2 del primero.
        assert len(tabla.value) == 4
        assert set(tabla.value["indicador_codigo"]) == {"IND-CONS-01", "IND-CONS-02"}

    def test_modo_todos_sin_indicadores_no_lanza_excepcion(self, sidoe_config):
        # BD sin indicadores activos: la vista debe manejarlo sin excepción.
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.session_state["landing_dismissed"] = True
        at.run()
        assert not at.exception
