# Despliegue en producción — SIDOE (ONE)

Guía operativa para poner SIDOE en producción de forma segura. Complementa
la sección "Seguridad" del `README.md`.

## 1. HTTPS/TLS vía reverse proxy

Streamlit no gestiona TLS por sí mismo en producción; se debe colocar detrás
de un reverse proxy (nginx o Apache) que termine la conexión HTTPS y reenvíe
el tráfico al proceso Streamlit local (HTTP interno).

### 1.1 Streamlit en modo headless, solo loopback

En `.streamlit/config.toml` (o por variables de entorno al arrancar el
servicio), asegurar:

```toml
[server]
headless = true
address = "127.0.0.1"   # No exponer el puerto directamente a la red
port = 8501
enableCORS = false        # El proxy gestiona el origen; evita doble capa
enableXsrfProtection = true
```

> Nota: si `enableCORS = false`, Streamlit exige `enableXsrfProtection = true`
> (ya está así por defecto en este proyecto) — es la combinación recomendada
> detrás de un proxy reverso, a diferencia de la config actual de desarrollo
> (`enableCORS = true`) documentada en el README.

### 1.2 Ejemplo de configuración nginx

Reemplazar `sidoe.one.gob.do` por el dominio real y las rutas de certificado
por las emitidas (Let's Encrypt / CA institucional):

El bloqueo anti-fuerza-bruta de `security/hardening.py` opera **por
usuario, a nivel de aplicación** (ver nota de diseño en ese módulo: es
estado en memoria del proceso Streamlit). Es una protección necesaria pero
no suficiente: no limita el volumen de peticiones por IP antes de que
lleguen a Streamlit. `limit_req_zone` agrega esa segunda capa, barata de
mantener, en el borde de la infraestructura:

```nginx
# Fuera de los bloques server{} (nivel http{}, normalmente en nginx.conf
# o en un archivo incluido desde ahí):
limit_req_zone $binary_remote_addr zone=sidoe_login:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=sidoe_general:10m rate=60r/m;

server {
    listen 80;
    server_name sidoe.one.gob.do;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name sidoe.one.gob.do;

    ssl_certificate     /etc/ssl/certs/sidoe.one.gob.do.fullchain.pem;
    ssl_certificate_key /etc/ssl/private/sidoe.one.gob.do.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Cabeceras de seguridad recomendadas
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Health-check estático: NO pasa por Streamlit, así que un monitor
    # externo (ej. Uptime Kuma, un cron con curl, o el balanceador si algún
    # día hay más de una instancia) puede verificar "¿nginx está vivo?" sin
    # depender de que la sesión de Streamlit responda. Requiere crear el
    # archivo `/opt/sidoe/healthcheck/ok.txt` con cualquier contenido (ej.
    # "ok") — no necesita regenerarse, solo debe existir en disco.
    location = /healthz {
        alias /opt/sidoe/healthcheck/ok.txt;
        access_log off;
    }

    location / {
        # Límite general contra scraping/DoS antes de llegar a Streamlit.
        # burst=20 permite ráfagas cortas normales (varios recursos
        # cargando a la vez) sin bloquear al usuario legítimo.
        limit_req zone=sidoe_general burst=20 nodelay;

        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;

        # Streamlit usa WebSocket para el estado interactivo
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 86400;
    }
}
```

> **Nota:** `zone=sidoe_login` queda definida arriba pero no aplicada a una
> `location` específica porque Streamlit no expone una ruta HTTP dedicada
> para `/login` (el login ocurre dentro de la misma sesión WebSocket que
> el resto de la app). Si en el futuro el login se separa a un endpoint
> propio, aplicar esa zona ahí con un `rate` más estricto que el general.

Validar la config y recargar:

```bash
sudo mkdir -p /opt/sidoe/healthcheck && echo ok | sudo tee /opt/sidoe/healthcheck/ok.txt
sudo nginx -t
sudo systemctl reload nginx
```

### 1.3 Arranque del servicio Streamlit (systemd)

Ejemplo de unit file `/etc/systemd/system/sidoe.service`:

```ini
[Unit]
Description=SIDOE - Sistema Integrado de Demanda y Oferta Estadística (ONE)
After=network.target

[Service]
Type=simple
User=sidoe
WorkingDirectory=/opt/sidoe
ExecStart=/opt/sidoe/.venv/bin/streamlit run app.py --server.headless=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sidoe.service
```

### 1.4 Cifrado en reposo de `sidoe.db`

**SIDOE no cifra la base de datos a nivel de aplicación.** `security/hardening.py`
lo documenta explícitamente: implementar cifrado ahí requeriría compilar
SQLite con la extensión SQLCipher, y esa responsabilidad se dejó
deliberadamente fuera del alcance del software, a cargo de infraestructura.

Esto tiene una consecuencia concreta que **debe** resolverse a nivel de
sistema operativo antes de ir a producción con datos reales: sin cifrado
de disco, cualquiera con acceso de lectura al filesystem del servidor
(un administrador de infraestructura no autorizado, un backup mal
asegurado, un disco retirado sin borrado seguro) puede copiar
`sidoe.db` y abrirlo con cualquier lector SQLite estándar — los
permisos `600` que aplica `asegurar_permisos_db()` al arrancar solo
protegen contra otros usuarios del mismo sistema operativo en caliente,
no contra acceso al disco físico o a una copia del archivo.

Checklist mínimo recomendado para TI de ONE:
- Cifrar el volumen/partición donde vive `/opt/sidoe` (LUKS en Linux, o el
  cifrado de disco nativo del proveedor si es una VM en la nube — la
  mayoría ofrece cifrado de volumen gestionado sin cambios de aplicación).
- Confirmar que los backups (`utils/backup.py`) heredan el mismo cifrado
  si se copian fuera del servidor (ej. a un bucket externo) — un backup
  sin cifrar en tránsito o en reposo anula la protección del volumen
  original.
- Si en el futuro se requiere cifrado a nivel de fila/columna (no solo de
  disco), evaluar SQLCipher como reemplazo del `sqlite3` estándar — es un
  cambio de dependencia no trivial, no una configuración.

---

## 2. Logs de aplicación y diagnóstico de incidentes

`app.py` blinda cada vista contra errores no controlados (`_ejecutar_vista`):
si algo falla, el usuario ve solo un mensaje genérico con un código corto
(`ERR-<timestamp>`), nunca el traceback real. El detalle técnico completo
se registra vía `logger.exception` sobre el logging estándar de Python
(`logging.basicConfig`, configurado en las primeras líneas de `app.py`),
que bajo systemd va a stdout/stderr y de ahí a journalctl.

### 2.1 Ubicar el log de un incidente reportado

Cuando alguien reporta un código (ej. `ERR-1738099123`):

```bash
# Ver el traceback completo de ese incidente
sudo journalctl -u sidoe.service | grep "ERR-1738099123" -A 30

# Seguir los logs en vivo mientras se reproduce el problema
sudo journalctl -u sidoe.service -f
```

El mensaje logueado incluye la vista donde ocurrió, el código de incidente
y el usuario (o "público" si no había sesión) — ver `_ejecutar_vista` en
`app.py`.

### 2.2 Retención

`journalctl` respeta la retención por defecto de journald del sistema
(típicamente días/semanas según `/etc/systemd/journald.conf`). Si se
necesita retención más larga para auditoría, redirigir la salida del
servicio a un archivo rotado (ej. vía `StandardOutput=append:/var/log/sidoe/app.log`
en el unit file + `logrotate`) es la vía recomendada — no implementado
por defecto en este documento.

---

## 3. Backup y restauración

### 3.1 Backup automático (ya implementado)

`utils/backup.py` genera backups consistentes vía la API de SQLite
(`conn.backup()`, seguro con WAL activo) y rota los últimos 7. Puede
dispararse desde el panel de administrador o programarse:

**Cron:**
```cron
# Backup diario a las 2:00 AM
0 2 * * * cd /opt/sidoe && /opt/sidoe/.venv/bin/python -m utils.backup >> /var/log/sidoe/backup.log 2>&1
```

**Systemd timer (alternativa a cron):** crear `sidoe-backup.service` (ejecuta
`python -m utils.backup`) y `sidoe-backup.timer` con `OnCalendar=daily`.

Adicionalmente, copiar los backups rotados a un destino fuera del servidor
(almacenamiento institucional / bucket cifrado) según la política de TI de
ONE — la rotación local de 7 copias no sustituye una copia off-site.

### 3.2 Retención de la tabla `auditoria`

`auditoria` solo crece — cada CREAR/ACTUALIZAR/ELIMINAR de indicadores y
usuarios agrega una fila y nada la borra desde la app. `views/ver_auditoria.py`
ya pagina a nivel SQL, así que un volumen alto no rompe la vista, pero sigue
infando el tamaño del archivo `.db` y el tiempo de los `COUNT(*)`/backups
a largo plazo.

`utils/archivar_auditoria.py` exporta a CSV y luego elimina de la tabla
activa las filas más antiguas que el umbral de retención (verifica que el
CSV se escribió completo *antes* de borrar nada — nunca hay pérdida de
información, solo se mueve a un archivo):

```bash
# Archivar todo lo anterior a 1 año (default, confirmado por la jefa de Randy)
python -m utils.archivar_auditoria

# Umbral distinto
python -m utils.archivar_auditoria --dias 400
```

**Cron sugerido (trimestral, no diario — esto es una tarea de mantenimiento,
no de backup):**
```cron
0 3 1 */3 * cd /opt/sidoe && /opt/sidoe/.venv/bin/python -m utils.archivar_auditoria >> /var/log/sidoe/archivado_auditoria.log 2>&1
```

> **Decisión institucional confirmada:** la jefa de Randy en ONE confirmó
> un umbral de retención de 365 días (1 año) para la tabla "en caliente"
> (consultable desde la UI); lo anterior se archiva en CSV. El cron sigue
> sin activarse por defecto en este repositorio — activarlo (agregar la
> línea anterior al crontab del servidor) queda a cargo de TI de ONE al
> desplegar en producción.

### 3.3 Restauración — SIEMPRE manual a nivel de filesystem/TI

**Decisión de diseño confirmada:** la restauración NO se expone en la UI.
Restaurar una base de datos es una operación destructiva que reemplaza el
estado actual del sistema; hacerlo accesible desde la interfaz web crearía
un vector de riesgo (error humano o cuenta comprometida) desproporcionado
frente al beneficio. Debe ejecutarla el equipo de TI directamente en el
servidor, con los permisos de sistema operativo que eso implica.

Procedimiento:

```bash
# 1. Detener el servicio para evitar escrituras durante la restauración
sudo systemctl stop sidoe.service

# 2. Respaldar el estado actual (aunque esté corrupto) antes de sobrescribir
cp /opt/sidoe/sidoe.db /opt/sidoe/sidoe.db.pre_restore_$(date +%Y%m%d_%H%M%S)

# 3. Copiar el backup elegido sobre el archivo activo
cp /opt/sidoe/sidoe.db.bak_20260722_020000 /opt/sidoe/sidoe.db

# 4. Eliminar archivos WAL/SHM huérfanos del estado anterior
rm -f /opt/sidoe/sidoe.db-wal /opt/sidoe/sidoe.db-shm

# 5. Verificar integridad de la BD restaurada
sqlite3 /opt/sidoe/sidoe.db "PRAGMA integrity_check;"

# 6. Corregir permisos (600, propietario del servicio)
chmod 600 /opt/sidoe/sidoe.db
chown sidoe:sidoe /opt/sidoe/sidoe.db

# 7. Reiniciar el servicio y validar con un smoke test
sudo systemctl start sidoe.service
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8501
```

Registrar la restauración (motivo, backup usado, responsable, fecha) en la
bitácora operativa de TI — es independiente de la tabla `auditoria` de la
aplicación, que solo registra acciones de usuarios sobre indicadores.

### 3.4 Eliminación masiva de indicadores — protocolo de TI (agosto-2026)

**Contexto:** desde agosto-2026, un `supervisor` que elimina indicadores
desde la interfaz web se **autodesactiva** al alcanzar
`config.UMBRAL_ELIMINACIONES_AUTOBLOQUEO` (5 por defecto) eliminaciones
seguidas — salvaguarda intencional contra eliminación masiva accidental o
una cuenta comprometida (ver `models/crud_indicadores.py::
borrar_indicador`). Un `administrador` debe reactivarlo manualmente desde
"Administrar Usuarios" cada vez que se cruza el umbral.

Si TI necesita eliminar un **volumen grande** de indicadores de una sola
vez (más de lo que ese límite permite sin interrupciones — p. ej. una
depuración masiva de duplicados o descontinuados), **no lo hagan desde la
interfaz web**: dispararía el bloqueo repetidamente y exigiría
reactivaciones manuales una y otra vez. Usen el script dedicado, que
corre por fuera de la UI y del rol `supervisor` — no incrementa el
contador de nadie ni desactiva ninguna cuenta, pero sigue dejando el
mismo rastro de auditoría que una eliminación normal, atribuido a la
persona real que lo ejecuta:

```bash
cd /opt/sidoe

# 1. SIEMPRE primero en modo simulación (default, no toca la BD): lista
#    qué se eliminaría y qué códigos no se encontraron.
python -m data.migraciones_historicas.eliminacion_masiva_indicadores \
    --usuario-id 3 --archivo /ruta/a/codigos_a_eliminar.txt

# 2. Revisado el resultado, ejecutar la eliminación real. El script crea
#    un backup rotado automáticamente ANTES de borrar nada (mismo
#    mecanismo del panel de administración) — no hace falta un backup
#    manual aparte.
python -m data.migraciones_historicas.eliminacion_masiva_indicadores \
    --usuario-id 3 --archivo /ruta/a/codigos_a_eliminar.txt --confirmar
```

`--usuario-id` debe ser el id de un `administrador` REAL y activo (el
script lo verifica y se niega a correr si no lo es) — así la auditoría
queda atribuida a una persona identificable, no a "sistema" ni a nadie.
`codigos_a_eliminar.txt` es un código de indicador por línea (líneas
vacías o que empiecen con `#` se ignoran). También admite
`--codigos COD-001 COD-002 ...` directamente en la línea de comandos para
listas cortas. El docstring completo del script (protocolo paso a paso,
incluyendo qué hacer si algo sale mal) está en el propio archivo:
`data/migraciones_historicas/eliminacion_masiva_indicadores.py`.

Es seguro re-ejecutar en modo simulación cuantas veces haga falta; en modo
`--confirmar`, los códigos ya eliminados en una corrida anterior
simplemente aparecen como "no encontrado" en la siguiente corrida (no
falla ni duplica nada).

---

## 4. Checklist previo a go-live

- [x] Credenciales de `security/crear_admin.py` generadas de forma
      interactiva o vía variables de entorno (no hardcodeadas) — ✅ corregido.
- [x] Historial de git limpiado de contraseñas de commits anteriores —
      ✅ ejecutado con `git filter-repo` (commit `5fa5861`, 1 ago 2026).
      **Importante:** el reescritor cambió los hashes de los 104 commits;
      cualquier clon existente (local de Randy, o de "los muchachos") quedó
      divergente del remoto — hay que re-clonar desde cero, no hacer `pull`.
- [ ] Reverse proxy nginx/Apache con TLS configurado y validado (`nginx -t`).
- [ ] `.streamlit/config.toml` de producción con `enableCORS = false`,
      `address = "127.0.0.1"`.
- [ ] Backup automático programado (cron/systemd timer) + copia off-site.
- [ ] Procedimiento de restauración probado al menos una vez en un entorno
      de staging antes de depender de él en un incidente real.
- [ ] `limit_req_zone` de nginx (sección 1.2) activo — validado con `nginx -t`.
- [ ] Endpoint `/healthz` respondiendo 200 desde un monitor externo.
- [ ] Volumen de `/opt/sidoe` cifrado a nivel de sistema operativo/proveedor
      (sección 1.4) — SIDOE no cifra la BD a nivel de aplicación.
- [x] Umbral de retención de `auditoria` (sección 3.2) — ✅ confirmado por
      la jefa de Randy en ONE: 365 días. Pendiente solo activar el cron en
      el servidor real al desplegar (no es parte de este repositorio).
- [ ] Cuentas `administrador` con 2FA activado (sección 5) — recomendado
      antes de go-live dado su nivel de privilegio. Disponible tanto en
      modo opt-in como exigido por otro administrador vía "Exigir 2FA";
      no ocurre automáticamente para nadie.

## 5. Notas de la evaluación de julio 2026 (rendimiento/UX)

**`st.rerun()` — auditoría, no refactor masivo.** El código tiene ~22 usos
de `st.rerun()`. Se revisaron y se corrigieron los 2 casos comprobadamente
redundantes (paginación de `views/consultas.py`: el valor de
`session_state` ya se leía más abajo en la misma pasada del script, sin
necesitar un rerun explícito). El resto (`admin_usuarios.py`,
`auxiliares.py`, `actualizar_indicador.py`, `eliminar_indicador.py`,
`app.py`) sigue un patrón distinto: se llaman **después de mutar la BD**
(crear/actualizar/eliminar), para refrescar listas/tablas que ya se
renderizaron **antes** en el mismo script — ahí sí son necesarios, porque
sin el rerun el usuario vería una lista desactualizada hasta su próxima
interacción. No se tocaron sin poder verificar visualmente cada flujo;
sería un refactor de UI de alcance propio, no algo para hacer a ciegas
sobre un sistema en producción.

**2FA (TOTP) — implementado, dos modalidades. Mecanismo sin restricción de
rol en el código, pero hoy solo alcanzable en la práctica desde cuentas
`administrador`.** `security/totp.py` + columnas
`totp_secret`/`totp_habilitado`/`requiere_2fa` en `usuarios` (migraciones
idempotentes). Decisión de la jefa de Randy en ONE (confirmada 4 ago
2026): el mecanismo en sí no distingue rol — pero con la reestructuración
de roles de agosto-2026 (rol `supervisor` nuevo), "Administrar Usuarios"
—única pantalla desde donde se configura o exige 2FA— quedó exclusiva del
menú de `administrador` (ni `editor` ni `supervisor` la tienen en su
menú). Un administrador sí puede exigir 2FA a un usuario `editor` o
`supervisor` desde ese panel (la tabla `usuarios` no filtra por rol al
listar a quién exigírselo), y ese usuario será forzado a configurarlo en
su siguiente login aunque su propio rol no tenga acceso al panel de
autoservicio — pero en la práctica, dado que nadie lo ha exigido fuera de
`administrador`, el 2FA activo hoy es efectivamente solo de cuentas
`administrador`.

- *Autoservicio (opt-in):* cualquier usuario puede activarlo para su
  propia cuenta desde "Administrar Usuarios" → "Verificación en dos pasos
  (2FA)": escanea un QR con su app autenticadora y confirma con el primer
  código generado (así no queda nadie bloqueado por un QR mal escaneado).
  No se activa para nadie por defecto.
- *Obligatorio (exigido por un administrador):* desde el mismo panel, un
  administrador puede marcar "🔒 Exigir 2FA" sobre cualquier usuario. En
  su siguiente login, ese usuario es forzado a configurar el 2FA antes de
  poder acceder a cualquier vista del sistema (ni siquiera las de solo
  lectura). "Quitar exigencia de 2FA" también desactiva el TOTP ya
  configurado del usuario, no solo el flag a futuro — evita que quede
  pidiendo el código sin que el admin lo haya elegido.
- *Códigos de respaldo (recovery codes):* al activarse el 2FA (por
  cualquiera de las dos vías) se generan 10 códigos de un solo uso,
  mostrados una única vez y hasheados en BD, para entrar si se pierde
  acceso a la app autenticadora. El usuario puede regenerarlos desde su
  propio panel.

(El sistema tiene tres roles con cuenta: `editor`, `supervisor` y
`administrador` — reestructuración de agosto-2026, ver README.md
"Estructura de roles". El rol `visualizador` se había eliminado antes de
esa sesión, reemplazado por el acceso público de solo lectura que sigue
vigente hoy.)

