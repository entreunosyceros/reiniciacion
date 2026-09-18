"""Cliente HTTP compartido y utilidades de URL/red.

Aquí están las piezas de bajo nivel que usan el resto de módulos:
normalizar URLs, hablar con el router por HTTP y detectar cortes de red.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import (
    ACTION_PATH_MARKERS,
    MANAGEMENT_PATH_MARKERS,
    NET_CHECK_TARGETS,
    NET_CHECK_TIMEOUT,
    TIMEOUT,
    USER_AGENT,
)


def normalize_url(raw: str) -> str:
    """Convierte lo que escribe el usuario en una URL http(s) válida."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Escribe la URL del router.")
    # Permite escribir solo 192.168.1.1 sin el esquema.
    if "://" not in text:
        text = "http://" + text
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("La URL debe empezar por http:// o https://")
    if not parsed.netloc:
        raise ValueError("La URL del router no es válida.")
    return text


def host_from_url(url: str) -> str:
    """Devuelve el hostname o IP de una URL."""
    return urllib.parse.urlparse(url).hostname or ""


def origin_from_url(url: str) -> str:
    """Devuelve esquema + host (+ puerto), sin la ruta."""
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def looks_like_local_host(host: str) -> bool:
    """Indica si el host parece de red local (para avisar antes de actuar)."""
    if not host:
        return False
    lowered = host.lower().rstrip(".")
    known = {
        "localhost",
        "router",
        "router.local",
        "fritz.box",
        "livebox",
        "livebox.home",
        "movistar",
        "vodafone",
        "home",
        "gateway",
        "box",
    }
    if lowered in known or lowered.endswith(".local") or lowered.endswith(".home"):
        return True
    # Si es una IP literal, comprobar si es privada.
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    # Si es un nombre, resolver y mirar las IPs obtenidas.
    try:
        info = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for item in info:
        ip_text = item[4][0]
        try:
            ip = ipaddress.ip_address(ip_text.split("%")[0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
    return False


def ssl_context() -> ssl.SSLContext:
    """Contexto SSL permisivo: muchos routers usan certificados autofirmados."""
    context = ssl._create_unverified_context()
    context.check_hostname = False
    return context


def internet_is_up(timeout: float = NET_CHECK_TIMEOUT) -> bool:
    """Comprueba si hay salida a internet con conexiones TCP cortas."""
    for host, port in NET_CHECK_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def action_noun(action: str) -> str:
    """Nombre corto de la acción para mensajes al usuario."""
    return "reinicio" if action == "reboot" else "restauración de fábrica"


def action_article_noun(action: str) -> str:
    """Misma acción con artículo, para frases naturales en español."""
    return "el reinicio" if action == "reboot" else "la restauración de fábrica"


def path_looks_like_action(path: str, action: str) -> bool:
    """True si la ruta parece un endpoint de reinicio o de fábrica."""
    lower = (path or "").lower()
    markers = list(ACTION_PATH_MARKERS)
    if action == "reboot":
        markers = [m for m in markers if m not in {"factory", "restore", "default"}] + [
            "reboot",
            "restart",
            "rebootinfo",
        ]
    return any(marker in lower for marker in markers)


def path_looks_like_management_page(path: str) -> bool:
    """True si la ruta parece login/home del admin (no una orden de reinicio)."""
    lower = (path or "").lower()
    if not lower or lower == "/":
        return True
    if path_looks_like_action(lower, "reboot") or path_looks_like_action(lower, "factory"):
        return False
    return any(marker.lower() in lower for marker in MANAGEMENT_PATH_MARKERS)


def looks_like_login_page(text: str) -> bool:
    """Heurística sobre el HTML: ¿parece una pantalla de login?"""
    lowered = (text or "").lower()
    markers = (
        "login",
        "password",
        "contraseña",
        "usuario",
        "username",
        "login_mhs",
        "te_acceso_router",
        "frm_logintoken",
    )
    hits = sum(1 for marker in markers if marker in lowered)
    return hits >= 2 or ("location.href" in lowered and "login" in lowered)


def response_suggests_action_accepted(result: HttpResult, action: str) -> bool:
    """Decide si una respuesta HTTP confirma de verdad el reinicio/fábrica."""
    text = result.text.lower()
    # Un 200 en la página de login no cuenta como éxito.
    if looks_like_login_page(text):
        return False
    words = (
        "rebooting",
        "reboot",
        "restarting",
        "reinici",
        "please wait",
        "espere",
        "wait",
        "device is rebooting",
        "el dispositivo se está reiniciando",
    )
    if action == "factory":
        words = words + (
            "factory",
            "fábrica",
            "fabrica",
            "restore",
            "default settings",
            "valores de fábrica",
        )
    if any(word in text for word in words):
        return True
    return result.status in {202, 204}


class RouterError(Exception):
    """Error al hablar con el router.

    likely_rebooting=True indica que el corte de red es coherente con un
    reinicio ya en marcha (suele ser una buena señal, no un fallo).
    """

    def __init__(self, message: str, likely_rebooting: bool = False) -> None:
        super().__init__(message)
        self.likely_rebooting = likely_rebooting


class HttpResult:
    """Respuesta HTTP simplificada (código, cuerpo y URL final)."""

    def __init__(self, status: int, body: bytes, url: str) -> None:
        self.status = status
        self.body = body
        self.url = url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def is_connection_drop(exc: BaseException) -> bool:
    """True si la excepción encaja con un cierre brusco de la conexión."""
    if isinstance(
        exc,
        (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            TimeoutError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    ):
        return True
    if isinstance(exc, socket.timeout):
        return True
    # Errores típicos de red en Linux tras enviar un reinicio.
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {104, 107, 110, 111}:
        return True
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        return is_connection_drop(reason)
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "remote end closed connection",
            "connection reset",
            "broken pipe",
            "timed out",
        )
    )


def build_opener(
    username: str,
    password: str,
    origin: str,
    cookie_jar: http.cookiejar.CookieJar | None = None,
) -> urllib.request.OpenerDirector:
    """Crea un opener con Basic/Digest auth y, si hace falta, cookies de sesión."""
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, origin, username, password)
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=ssl_context()),
        urllib.request.HTTPBasicAuthHandler(password_mgr),
        urllib.request.HTTPDigestAuthHandler(password_mgr),
    ]
    if cookie_jar is not None:
        handlers.insert(0, urllib.request.HTTPCookieProcessor(cookie_jar))
    return urllib.request.build_opener(*handlers)


def http_request(
    url: str,
    method: str,
    username: str,
    password: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = TIMEOUT,
    cookie_jar: http.cookiejar.CookieJar | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> HttpResult:
    """Realiza una petición HTTP/HTTPS al router y unifica el manejo de errores."""
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    active_opener = opener or build_opener(username, password, origin, cookie_jar)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        request_headers.update(headers)
    if data is not None and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with active_opener.open(request, timeout=timeout) as response:
            body = response.read(64_000)
            return HttpResult(getattr(response, "status", 200), body, response.geturl())
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(64_000)
        except Exception:
            pass
        if exc.code in {200, 202, 204}:
            return HttpResult(exc.code, body, url)
        if exc.code in {401, 403}:
            raise RouterError("Usuario o contraseña incorrectos, o el router rechazó el acceso.") from exc
        raise RouterError(f"El router respondió con el código HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        # Tras un reinicio el router suele cortar sin responder bien.
        if is_connection_drop(exc):
            raise RouterError(
                "La conexión se cortó después de enviar la orden. Es habitual si el router ya está aplicándola.",
                likely_rebooting=True,
            ) from exc
        reason = exc.reason
        raise RouterError(f"No se pudo conectar con el router: {reason}") from exc
    except (TimeoutError, socket.timeout, http.client.RemoteDisconnected, http.client.IncompleteRead, OSError) as exc:
        if isinstance(exc, OSError) and not is_connection_drop(exc):
            raise RouterError(f"No se pudo conectar con el router: {exc}") from exc
        raise RouterError(
            "La conexión se cortó después de enviar la orden. Es habitual si el router ya está reiniciando.",
            likely_rebooting=True,
        ) from exc


def router_became_unreachable(origin: str, username: str, password: str) -> bool:
    """Espera un momento y comprueba si el router ya no responde (posible reinicio)."""
    time.sleep(2)
    try:
        http_request(origin + "/", "GET", username, password, timeout=3)
        return False
    except RouterError as exc:
        return True if exc.likely_rebooting else "No se pudo conectar" in str(exc)
