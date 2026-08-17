# Instalación del Sistema SIDOE — ONE

## Método recomendado: pip install -e .

Es el único paso necesario después de clonar el repositorio.
Registra la raíz del proyecto en el entorno virtual de forma permanente,
eliminando la necesidad de `sys.path.append()` en cualquier archivo.

```powershell
# 1. Crear y activar el entorno virtual (Windows)
python -m venv .venv
.venv\Scripts\activate

# 2. Instalar dependencias + registrar el paquete SIDOE
pip install -e .

# 3. Ejecutar la aplicación
streamlit run app.py
```

```bash
# 1. Crear y activar el entorno virtual (Linux/Mac)
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependencias + registrar el paquete SIDOE
pip install -e .

# 3. Ejecutar la aplicación
streamlit run app.py
```

## Instalación reproducible en producción

`pip install -e .` resuelve dependencias contra `pyproject.toml`, que
deliberadamente no fija versiones exactas (igual que `requirements.txt`).
Esto es correcto para desarrollo, pero en un servidor de producción puede
instalar una versión de alguna dependencia distinta a la que se validó en
CI. Para instalar exactamente las mismas versiones que pasaron la suite de
pruebas y el linter, use el archivo de lock en lugar del paso 2 anterior:

```bash
pip install -r requirements.lock
pip install -e . --no-deps   # registra el paquete SIDOE sin reinstalar dependencias
```

Ver el encabezado de `requirements.lock` para instrucciones de
regeneración (deliberada, no automática).

## ¿Por qué pip install -e . en lugar de sys.path?

`pip install -e .` (modo editable) le dice a Python que la carpeta del
proyecto ES un paquete instalado. Esto significa que `from config import ...`
o `from data.database import ...` funciona desde cualquier directorio,
cualquier script, y cualquier terminal — sin trucos de path.

El flag `-e` (editable) hace que los cambios al código se reflejen
inmediatamente sin reinstalar.

## Método alternativo: archivo .pth (sin pip)

Si no es posible ejecutar pip en el servidor, copie `sidoe.pth` al
directorio `site-packages` del entorno virtual, reemplazando
`RUTA_ABSOLUTA_DEL_PROYECTO` con la ruta real del proyecto:

```powershell
# Ejemplo Windows (ajuste la ruta de Python según su instalación)
# Editar sidoe.pth y reemplazar RUTA_ABSOLUTA_DEL_PROYECTO por:
# C:\Users\Usuario\Desktop\Estadistica Mencion Socioeconomia\sidoe_system

copy sidoe.pth .venv\Lib\site-packages\sidoe.pth
```

```bash
# Ejemplo Linux
# Editar sidoe.pth y reemplazar RUTA_ABSOLUTA_DEL_PROYECTO por:
# /opt/sidoe_system

cp sidoe.pth .venv/lib/python3.11/site-packages/sidoe.pth
```

## Scripts de migración (ejecutar una sola vez)

```powershell
# Migración inicial del Excel oficial
python -m data.migraciones_historicas.ETL_migracion
```

Los siguientes ya fueron aplicados en producción y solo se documentan como
referencia histórica (viven en `data/migraciones_historicas/`, son
idempotentes por si hiciera falta volver a correrlos sobre una BD nueva):

```powershell
# Corrección de FK de auditoría (si aplica)
python -m data.migraciones_historicas.fix_auditoria_fk

# Ajustes de esquema v2 (si aplica)
python -m data.migraciones_historicas.migracion_v2_ajustes
```

> **Nota:** Los scripts se ejecutan con `python -m data.NOMBRE` (con punto,
> no con slash), lo que garantiza que Python resuelva los imports
> correctamente desde la raíz del proyecto.
