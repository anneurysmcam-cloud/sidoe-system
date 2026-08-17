"""
data/migraciones_historicas/eliminacion_masiva_indicadores.py
================================================================
PROTOCOLO DE ELIMINACIÓN MASIVA DE INDICADORES — para uso de TI de la ONE.

¿Por qué existe este script?
-----------------------------
Desde agosto-2026, un usuario con rol `supervisor` que elimina indicadores
desde la interfaz web ("🗑️ Eliminar Indicador") se autodesactiva al
alcanzar `config.UMBRAL_ELIMINACIONES_AUTOBLOQUEO` (5 por defecto)
eliminaciones seguidas — ver models/crud_indicadores.py::borrar_indicador.
Es una salvaguarda intencional contra eliminación masiva accidental o una
cuenta comprometida, y requiere que un administrador reactive la cuenta
desde "Administrar Usuarios" cada vez que se cruza el umbral.

Si TI necesita eliminar un volumen grande de indicadores de una sola vez
(más de lo que ese límite permite sin interrupciones — p. ej. una
depuración masiva de datos duplicados o descontinuados), hacerlo desde la
interfaz web dispararía ese bloqueo repetidamente. Este script elimina
por fuera de la UI y del rol `supervisor`, así que NO incrementa el
contador de nadie ni desactiva ninguna cuenta — pero sigue dejando el
mismo rastro de auditoría (tabla `auditoria`) que una eliminación normal,
atribuido a la persona real que lo ejecuta.

PROTOCOLO (léalo completo antes de ejecutar)
-----------------------------------------------
1. Confirmar con la jefa de Randy / el supervisor responsable que la
   lista de códigos a eliminar es correcta — este script NO tiene forma
   de "deshacer" salvo restaurar desde el backup del paso 3.
2. Correr PRIMERO sin --confirmar (modo simulación, es el default): lista
   qué se eliminaría y por qué código no se encontró, sin tocar la BD.
3. El script crea un backup rotado automáticamente antes de borrar nada
   en el paso real (usa utils.backup.crear_backup_rotado, el mismo
   mecanismo del panel de administración) — no hace falta un backup
   manual aparte, pero no está de más si el volumen es grande.
4. Correr con --confirmar, pasando el id de un usuario ADMINISTRADOR real
   (para que la auditoría quede atribuida a una persona identificable, no
   a "sistema" o a nadie) — el script verifica que ese id exista y tenga
   rol 'administrador' antes de borrar nada.
5. Revisar el resumen final (cuántos se eliminaron, cuántos códigos no se
   encontraron) y, si algo no cuadra, restaurar desde el backup del paso 3
   (ver utils/backup.py::listar_backups() o el panel de Administrar
   Usuarios, que también expone los backups).

USO
-----
Modo simulación (no toca la BD, es el default):
    python -m data.migraciones_historicas.eliminacion_masiva_indicadores \\
        --usuario-id 3 --codigos COD-001 COD-002 COD-003

Desde un archivo de texto (un código por línea, líneas vacías o que
empiecen con # se ignoran):
    python -m data.migraciones_historicas.eliminacion_masiva_indicadores \\
        --usuario-id 3 --archivo /ruta/a/codigos_a_eliminar.txt

Ejecución real (crea backup, borra, registra auditoría):
    python -m data.migraciones_historicas.eliminacion_masiva_indicadores \\
        --usuario-id 3 --archivo codigos_a_eliminar.txt --confirmar

Es seguro re-ejecutar en modo simulación cuantas veces haga falta; en modo
--confirmar, los códigos ya eliminados en una corrida anterior simplemente
aparecerán como "no encontrado" en la siguiente (no falla ni duplica nada).
"""

import argparse
import logging
import sys

from config import DB_PATH
from data import database as db_mod
from models.crud_indicadores import borrar_indicador

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _leer_codigos_de_archivo(ruta: str) -> list[str]:
    codigos = []
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            codigo = linea.strip()
            if codigo and not codigo.startswith("#"):
                codigos.append(codigo)
    return codigos


def _verificar_administrador(usuario_id: int) -> tuple[bool, str]:
    conn = db_mod.obtener_conexion()
    try:
        fila = conn.execute(
            "SELECT username, rol, activo FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
    finally:
        conn.close()
    if not fila:
        return False, f"No existe ningún usuario con id={usuario_id}."
    username, rol, activo = fila
    if rol != "administrador":
        return False, (
            f"El usuario '{username}' (id={usuario_id}) tiene rol '{rol}', "
            "no 'administrador'. Este script exige un administrador real "
            "para que la auditoría quede atribuida correctamente."
        )
    if not activo:
        return False, f"El usuario '{username}' (id={usuario_id}) está desactivado."
    return True, username


def _resolver_ids(codigos: list[str]) -> tuple[dict[str, int], list[str]]:
    """Devuelve (código -> id) para los encontrados, y la lista de códigos
    que no existen en la base."""
    conn = db_mod.obtener_conexion()
    try:
        encontrados: dict[str, int] = {}
        for codigo in codigos:
            fila = conn.execute(
                "SELECT id FROM indicadores WHERE codigo = ?", (codigo,)
            ).fetchone()
            if fila:
                encontrados[codigo] = fila[0]
    finally:
        conn.close()
    no_encontrados = [c for c in codigos if c not in encontrados]
    return encontrados, no_encontrados


def main() -> int:
    # Este script de TI siempre corre contra una BD de producción ya
    # inicializada, así que esto no crea nada nuevo en la práctica -- se
    # llama de todas formas por consistencia con el resto de los scripts
    # utilitarios ahora que importar data.database ya no ejecuta el
    # bootstrap como efecto secundario (Hallazgo #4 del informe de
    # revisión de código de agosto 2026). Es idempotente y segura de
    # llamar aunque el esquema ya exista.
    db_mod.inicializar_base_datos()

    parser = argparse.ArgumentParser(
        description="Eliminación masiva de indicadores fuera de la UI "
        "(protocolo de TI — no dispara el auto-bloqueo de supervisor).",
    )
    parser.add_argument(
        "--usuario-id", type=int, required=True,
        help="id de un usuario con rol 'administrador' real, para atribuir "
        "la auditoría. Ver la tabla en 'Administrar Usuarios' para el id.",
    )
    grupo_origen = parser.add_mutually_exclusive_group(required=True)
    grupo_origen.add_argument(
        "--codigos", nargs="+", metavar="CODIGO",
        help="Uno o más códigos de indicador a eliminar, separados por espacio.",
    )
    grupo_origen.add_argument(
        "--archivo", metavar="RUTA",
        help="Ruta a un archivo de texto con un código de indicador por línea.",
    )
    parser.add_argument(
        "--confirmar", action="store_true",
        help="Ejecuta la eliminación real (crea backup antes de borrar). "
        "Sin esta bandera, solo simula y no toca la base de datos.",
    )
    args = parser.parse_args()

    ok_admin, detalle_admin = _verificar_administrador(args.usuario_id)
    if not ok_admin:
        logger.error("❌ %s", detalle_admin)
        return 1
    logger.info("Ejecutando como administrador: %s (id=%d)", detalle_admin, args.usuario_id)

    codigos = args.codigos if args.codigos else _leer_codigos_de_archivo(args.archivo)
    codigos = list(dict.fromkeys(codigos))  # de-duplicar preservando orden
    if not codigos:
        logger.error("❌ No se recibió ningún código para eliminar.")
        return 1

    encontrados, no_encontrados = _resolver_ids(codigos)
    logger.info(
        "%d código(s) recibidos: %d encontrado(s), %d no encontrado(s).",
        len(codigos), len(encontrados), len(no_encontrados),
    )
    if no_encontrados:
        logger.warning("No encontrados (se ignoran): %s", ", ".join(no_encontrados))
    if not encontrados:
        logger.error("❌ Ningún código coincide con un indicador existente. Nada que hacer.")
        return 1

    if not args.confirmar:
        logger.info("── MODO SIMULACIÓN (sin --confirmar): no se tocó la base de datos. ──")
        logger.info("Se eliminarían %d indicador(es):", len(encontrados))
        for codigo in encontrados:
            logger.info("  - %s", codigo)
        logger.info(
            "Vuelva a ejecutar con --confirmar (y los mismos argumentos) "
            "para aplicar la eliminación real."
        )
        return 0

    from utils.backup import crear_backup_rotado
    ruta_backup = crear_backup_rotado(DB_PATH)
    logger.info("✅ Backup creado antes de eliminar: %s", ruta_backup)

    exitosos, fallidos = [], []
    for codigo, id_indicador in encontrados.items():
        ok, msg = borrar_indicador(id_indicador, usuario_id=args.usuario_id)
        if ok:
            exitosos.append(codigo)
            logger.info("  ✅ %s eliminado.", codigo)
        else:
            fallidos.append((codigo, msg))
            logger.error("  ❌ %s: %s", codigo, msg)

    logger.info(
        "── Resumen: %d eliminado(s), %d fallido(s), %d no encontrado(s) de %d recibido(s). ──",
        len(exitosos), len(fallidos), len(no_encontrados), len(codigos),
    )
    if fallidos:
        logger.warning(
            "Revise los fallidos arriba; si algo no cuadra, restaure desde "
            "%s antes de repetir.", ruta_backup,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
