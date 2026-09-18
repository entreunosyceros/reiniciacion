"""Constantes, rutas y persistencia de configuración.

Este módulo centraliza valores que usa el resto del programa:
rutas de archivos, tiempos de espera, rutas HTTP conocidas y colores de la UI.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Identidad y rutas del proyecto
# ---------------------------------------------------------------------------

APP_NAME = "Reiniciación"
# Clase X11/Wayland (sin tilde): debe coincidir con StartupWMClass del .desktop
# para que Ubuntu muestre el icono en el dock/barra.
WM_CLASS = "Reiniciacion"

# Carpeta raíz del proyecto (un nivel por encima de este paquete).
APP_DIR = Path(__file__).resolve().parent.parent
ICON_FILE = APP_DIR / "img" / "logo.png"

# Preferencias del usuario (URL, usuario, contraseña, etc.).
CONFIG_DIR = Path.home() / ".config" / "reiniciacion"
CONFIG_FILE = CONFIG_DIR / "config.json"
# Ruta antigua por si el usuario viene de una versión previa.
LEGACY_CONFIG_FILE = Path.home() / ".config" / "reiniciar-router" / "config.json"

DEFAULT_URL = "http://192.168.1.1"
DEFAULT_USER = "admin"

# ---------------------------------------------------------------------------
# Tiempos y comprobación de internet
# ---------------------------------------------------------------------------

TIMEOUT = 8  # segundos para peticiones HTTP al router
NET_CHECK_INTERVAL_MS = 5000  # sondeo normal del indicador de internet
NET_CHECK_FAST_MS = 2000  # sondeo más frecuente tras un reinicio
NET_CHECK_TIMEOUT = 2.0

# Destinos públicos para comprobar si hay salida a internet (TCP).
NET_CHECK_TARGETS = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("208.67.222.222", 443),
)

# ---------------------------------------------------------------------------
# TR-064 / UPnP (habitual en routers de operador)
# ---------------------------------------------------------------------------

TR064_PORTS = (49000, 49443)  # HTTP y HTTPS
TR064_DESC_PATHS = ("/tr64desc.xml", "/tr64desc", "/igddesc.xml")
USER_AGENT = "Reiniciacion/1.0"

# Palabra que hay que escribir para confirmar el borrado a fábrica.
FACTORY_CONFIRM_WORD = "FABRICA"

# Intentos HTTP genéricos cuando TR-064 no está disponible.
# Cada entrada es: (método, ruta, cuerpo opcional).
COMMON_REBOOT_ATTEMPTS = (
    ("GET", "/reboot", None),
    ("GET", "/reboot.cgi", None),
    ("POST", "/reboot.cgi", b"Reboot=Reboot"),
    ("GET", "/cgi-bin/reboot.cgi", None),
    ("POST", "/cgi-bin/reboot.cgi", b"Reboot=Reboot"),
    ("POST", "/cgi-bin/reboot.cgi", b"action=reboot"),
    ("GET", "/cgi-bin/restart.cgi", None),
    ("POST", "/cgi-bin/restart.cgi", b""),
    ("GET", "/rebootinfo.cgi", None),
    ("POST", "/apply.cgi", b"action_mode=reboot&action_script=&action_wait=5"),
    ("POST", "/apply.cgi", b"submit_button=Reboot&action=Reboot&change_action="),
    ("GET", "/cgi-bin/reboot", None),
    ("POST", "/html/management/reboot.cgi?RequestFile=/html/management/maintenance.asp", b""),
    ("POST", "/service.cgi", b"EVENT=REBOOT"),
)

COMMON_FACTORY_ATTEMPTS = (
    ("GET", "/factorydefault", None),
    ("GET", "/factoryreset", None),
    ("POST", "/restore.cgi", b"RestoreFactory=RestoreFactory"),
    ("POST", "/reset.cgi", b"factory=1"),
    ("POST", "/cgi-bin/restore.cgi", b"factory=1"),
    ("POST", "/apply.cgi", b"action_mode=Restore&action_script=&action_wait=15"),
    ("POST", "/apply.cgi", b"submit_button=Restore&action=Restore&change_action="),
    ("GET", "/cgi-bin/restore", None),
)

# Fragmentos de ruta que suelen indicar una acción real (no la home del admin).
ACTION_PATH_MARKERS = (
    "reboot",
    "restart",
    "rebootinfo",
    "resetrouter",
    "factory",
    "restore",
    "default",
    "devrestart",
    "resetboard",
)

# Fragmentos que suelen ser páginas de login o administración (no reinician).
MANAGEMENT_PATH_MARKERS = (
    "indexmain",
    "login",
    "logIn",
    "te_acceso",
    "home",
    "dashboard",
    "status",
    "main.html",
    "index.html",
    "index.asp",
)

# Plantilla SOAP para acciones TR-064 (Reboot / FactoryReset).
SOAP_ACTION = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:{action} xmlns:u="{service_type}"></u:{action}>
  </s:Body>
</s:Envelope>
"""

# ---------------------------------------------------------------------------
# Paleta de la interfaz
# ---------------------------------------------------------------------------

BG = "#12151c"
SURFACE = "#1b2030"
SURFACE_2 = "#242a3b"
BORDER = "#343b50"
TEXT = "#f4f6fb"
MUTED = "#97a0b5"
ACCENT = "#6ea8ff"
DANGER = "#ef6b6b"
FACTORY = "#be123c"
SUCCESS = "#4fd19a"
WARNING = "#e7c35a"
INPUT_BG = "#10131a"

# Nombres de acción SOAP según el botón pulsado en la UI.
TR064_ACTIONS = {
    "reboot": "Reboot",
    "factory": "FactoryReset",
}


def local_config() -> dict:
    """Lee la configuración guardada en este equipo (si existe)."""
    for path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def save_config(data: dict) -> None:
    """Guarda la configuración en ~/.config/reiniciacion/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
