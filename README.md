# Reiniciación

<p align="center">
<img width="604" height="575" alt="logo" src="https://github.com/user-attachments/assets/c55cb377-1399-4f75-95af-d304c3c4881e" />
</p>

Aplicación de escritorio para **reiniciar** tu router o **restaurarlo a valores de fábrica** sin abrir el navegador.

## Requisitos

- Python 3.10 o superior
- `python3-tk` (Tkinter)
- Estar conectado a la red del router (normalmente Wi‑Fi o cable)

No necesita dependencias de pip: solo la biblioteca estándar de Python.
El paquete `.deb` declara esas dependencias y `apt` las instala si faltan.

## Instalación (.deb)

Para generar el paquete hace falta `python3-pil` (solo al construir, no al usar la app):

```bash
sudo apt install python3-pil
./build-deb.sh
sudo apt install ./dist/reiniciacion_1.0.1_all.deb
```

También vale:

```bash
sudo dpkg -i ./dist/reiniciacion_1.0.1_all.deb
```

Tras instalarlo:
<p align="center">
<img width="470" height="234" alt="lanzador-reiniciacion" src="https://github.com/user-attachments/assets/e21e84ed-f93c-4704-9bf3-28729a5a50e6" />
</p>

- comando: `reiniciacion`
- entrada en el menú de aplicaciones: **Reiniciación**

Para desinstalar:

```bash
sudo apt remove reiniciacion
```

### Ejecutar sin instalar

Desde la carpeta del proyecto:

```bash
python3 reiniciacion.py
```

## Uso rápido

<p align="center">
<img width="589" height="988" alt="interfaz-reiniciacion" src="https://github.com/user-attachments/assets/c03a7262-521f-4070-b0a0-e16ad94f9127" />
</p>

1. Conéctate a la red de tu router.
2. Escribe la **URL** (por ejemplo `http://192.168.1.1`).
3. Introduce **usuario** y **contraseña** de administración.
4. Pulsa **Probar conexión**.
5. Elige **Reiniciar** o **Valores de fábrica**.

## Opciones de la interfaz

### URL del router

Dirección de la página de administración. Ejemplos:

- `http://192.168.1.1`
- `http://192.168.0.1`
- `http://fritz.box`
- `https://192.168.1.1`

Si no escribes `http://` o `https://`, se añade `http://` automáticamente.

### Usuario

Usuario de administración del router. Por defecto: `admin`.

En algunos routers (sobre todo TR-064 / FRITZ!Box) hace falta un usuario con permiso de aplicación o de administración remota.

### Contraseña

Contraseña de administración del router.

- **Mostrar contraseña**: muestra el texto en claro mientras escribes.

### Método

Define cómo se envía la orden al router:

| Método | Qué hace |
| --- | --- |
| **Automático (recomendado)** | Intenta TR-064 y, si falla, rutas HTTP habituales de reinicio o de fábrica. |
| **TR-064 / UPnP** | Usa la API TR-064 (puertos 49000 / 49443), habitual en routers de operador. |
| **HTTP GET a la URL** | Hace un `GET` a la URL exacta que escribiste. |
| **HTTP POST a la URL** | Hace un `POST` a la URL exacta que escribiste. |

Si conoces la URL concreta de reinicio o de fábrica de tu modelo, pégala en el campo URL y elige **HTTP GET** o **HTTP POST**.

Para el modo automático conviene poner solo la base, por ejemplo `http://192.168.1.1`, no la página de administración (`/cgi-bin/indexmain.cgi`), porque esa página no reinicia el router.

### Router Movistar (MHS)

Si tu router muestra la página `logIn_mhs.cgi` (Movistar/Mitrastar), el modo automático:

1. Entra con la contraseña cifrada como hace la web (`MD5(contraseña:sid)`).
2. Envía el reinicio a `mhs_management.cgi` / `backupRestoreSetting.cgi`.
3. Para valores de fábrica usa `restoreToDefault`.

En estos routers normalmente basta la **contraseña de la pegatina**; el campo usuario puede ignorarse.

### Recordar datos

La casilla **Recordar URL, usuario y contraseña en este equipo** viene activada por defecto.

Si está marcada, al cerrar la aplicación (o tras probar conexión / enviar una orden) se guardan:

- URL del router
- usuario
- contraseña
- método elegido

Así no hace falta volver a escribirlos en la siguiente sesión.

Los datos se guardan en local y **sin cifrar** en:

```text
~/.config/reiniciacion/config.json
```

Si desmarcas la casilla, se borra ese archivo y no se recordará nada.

Si existía una configuración antigua de versiones previas, también se puede leer desde:

```text
~/.config/reiniciar-router/config.json
```

## Botones de acción

### Probar conexión

Comprueba si el router responde con la URL, usuario y contraseña indicados. No reinicia ni borra nada.

### Abrir en el navegador

Abre la configuración del router e intenta iniciar sesión con los datos del programa.

- En routers **Movistar (logIn_mhs)** inicia sesión en segundo plano y abre un **proxy local** (`127.0.0.1`) con esa sesión, para evitar el error 403 al enviar el login desde otra dirección.
- El proxy permanece activo unos 15 minutos mientras navegas la configuración.
- En otros modelos prueba formularios habituales o autenticación HTTP Basic.

### Reiniciar

Envía una orden de **reinicio normal**.

- Internet y Wi‑Fi se cortarán unos minutos.
- La configuración del router **no** se borra.
- Pide confirmación antes de enviar la orden.

### Valores de fábrica

Envía una orden de **restauración a valores de fábrica**.

- Se pierden Wi‑Fi, ajustes de internet, usuarios y el resto de la configuración.
- Habrá que volver a configurar el router después.
- Pide confirmación y, además, que escribas `FABRICA` para evitar borrados accidentales.

## Cómo funciona el modo automático

### Reinicio

1. Busca la interfaz **TR-064** y envía la acción `Reboot`.
2. Si no está disponible y la URL tiene una ruta concreta, prueba `GET` y `POST` sobre esa URL.
3. Si sigue fallando, prueba rutas HTTP comunes, por ejemplo:
   - `/reboot`
   - `/reboot.cgi`
   - `/apply.cgi` (varios formatos de formulario)
   - `/cgi-bin/reboot`

### Valores de fábrica

1. Busca **TR-064** y envía la acción `FactoryReset`.
2. Si no está disponible, prueba la URL indicada o rutas HTTP comunes, por ejemplo:
   - `/factorydefault`
   - `/factoryreset`
   - `/restore.cgi`
   - `/reset.cgi`
   - `/apply.cgi` (formatos de restore)
   - `/cgi-bin/restore`

El panel inferior muestra el progreso y los errores de cada intento.

## Indicador de internet

En la parte superior de la ventana verás el estado de la conexión a internet:

- **activa**: hay salida a internet
- **desconectada**: no hay salida a internet

Tras reiniciar o restaurar el router, el indicador pasa a desconectado y comprueba la red con más frecuencia hasta que vuelve a estar activa.

La app usa autenticación **Basic** y **Digest** frente a la interfaz del router.

TR-064 se intenta en:

- puerto `49000` (HTTP)
- puerto `49443` (HTTPS)

## Ayuda integrada

El botón **Ayuda** resume el uso básico, la diferencia entre reinicio y fábrica, y la ruta del archivo de configuración.

## Limitaciones

- No todos los routers aceptan reinicio o restauración por red. Depende del fabricante y del firmware.
- Algunos modelos solo permiten estas acciones desde su propia web, con tokens de sesión, o con SSH/Telnet.
- Si el modo automático no funciona, prueba a pegar la URL exacta de tu router y usar **HTTP GET** o **HTTP POST**.
- Úsalo solo con **tu propio router** y dentro de tu red local.

## Archivos del proyecto

| Archivo | Descripción |
| --- | --- |
| `reiniciacion.py` | Punto de entrada de la aplicación |
| `reinicia/` | Lógica (config, HTTP, router, navegador, GUI) |
| `img/logo.png` | Icono de la aplicación |
| `build-deb.sh` | Genera el paquete `.deb` en `dist/` |
| `packaging/` | Lanzador, `.desktop`, icono y metadatos Debian |
| `dist/` | Paquetes `.deb` generados |
| `README.md` | Esta documentación |
