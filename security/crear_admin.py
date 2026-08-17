"""
security/crear_admin.py
=======================
Script de utilidad para crear el usuario administrador inicial.
Ejecutar una sola vez en un entorno nuevo:

    python -m security.crear_admin

El usuario y la contraseña NO están hardcodeados. Se solicitan de forma
interactiva (la contraseña con entrada oculta vía getpass), o pueden
suministrarse mediante variables de entorno para despliegues automatizados:

    SIDOE_ADMIN_USERNAME=admin_jefa SIDOE_ADMIN_PASSWORD='...' \
        python -m security.crear_admin
"""

import getpass
import logging
import os
import sqlite3
import sys

from data.database import inicializar_base_datos
from security.auth import registrar_usuario

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _obtener_credenciales() -> tuple[str, str]:
    """Obtiene username y password desde variables de entorno o, si no
    están definidas, de forma interactiva por consola."""
    username = os.environ.get("SIDOE_ADMIN_USERNAME")
    password = os.environ.get("SIDOE_ADMIN_PASSWORD")

    if username and password:
        logger.info("Usando credenciales provistas por variables de entorno.")
        return username, password

    if not username:
        username = input("Nombre de usuario para el administrador inicial: ").strip()

    if not password:
        password = getpass.getpass("Contraseña (no se mostrará en pantalla): ")
        password_confirm = getpass.getpass("Confirma la contraseña: ")
        if password != password_confirm:
            logger.error("❌ Las contraseñas no coinciden. Abortando.")
            sys.exit(1)

    return username, password


def main() -> None:
    """Crea el usuario administrador inicial en la base de datos, con
    credenciales suministradas por el operador (interactivas o vía
    variables de entorno). No usar contraseñas de ejemplo en producción."""
    # Este script se ejecuta típicamente en un entorno nuevo, ANTES de que
    # nadie haya arrancado app.py todavía -- inicializar_base_datos() debe
    # llamarse explícitamente aquí para garantizar que la tabla `usuarios`
    # (y el resto del esquema) exista antes de registrar_usuario() (Hallazgo
    # #4 del informe de revisión de código de agosto 2026: importar
    # data.database/security.auth ya no crea el esquema como efecto
    # secundario). Es segura de llamar aunque el esquema ya exista
    # (idempotente).
    inicializar_base_datos()

    username, password = _obtener_credenciales()

    if not username or not password:
        logger.error("❌ Usuario y contraseña son obligatorios. Abortando.")
        sys.exit(1)

    try:
        registrar_usuario(username, password, rol="administrador")
    except (ValueError, sqlite3.IntegrityError) as exc:
        logger.error("❌ No se pudo crear el usuario administrador: %s", exc)
        sys.exit(1)

    logger.info("✅ Usuario administrador creado: %s", username)
    logger.info(
        "Recuerda no compartir esta contraseña ni dejarla en historiales de "
        "shell/CI. Si se usaron variables de entorno, elimínalas de la sesión."
    )


if __name__ == "__main__":
    main()
