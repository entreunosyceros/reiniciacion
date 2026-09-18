"""Operaciones de reinicio, restauración y detección del router.

Incluye TR-064, rutas HTTP genéricas, login Movistar (MHS) y el modo automático.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Callable

from .config import (
    COMMON_FACTORY_ATTEMPTS,
    COMMON_REBOOT_ATTEMPTS,
    SOAP_ACTION,
    TR064_ACTIONS,
    TR064_DESC_PATHS,
    TR064_PORTS,
)
from .http_client import (
    RouterError,
    HttpResult,
    action_article_noun,
    action_noun,
    build_opener,
    host_from_url,
    http_request,
    looks_like_login_page,
    origin_from_url,
    path_looks_like_action,
    path_looks_like_management_page,
    response_suggests_action_accepted,
    router_became_unreachable,
)

# ---------------------------------------------------------------------------
# TR-064 / UPnP (estándar de muchos routers domésticos)
# ---------------------------------------------------------------------------


def discover_tr064(host: str, username: str, password: str) -> tuple[str, str] | None:
    """Busca el servicio DeviceConfig TR-064 del router y su controlURL."""
    schemes = ("http", "https")
    for port in TR064_PORTS:
        for scheme in schemes:
            if scheme == "https" and port == 49000:
                continue
            if scheme == "http" and port == 49443:
                continue
            for path in TR064_DESC_PATHS:
                url = f"{scheme}://{host}:{port}{path}"
                try:
                    result = http_request(url, "GET", username, password, timeout=5)
                except RouterError:
                    continue
                found = parse_deviceconfig(result.body, f"{scheme}://{host}:{port}")
                if found:
                    return found
    return None


def parse_deviceconfig(xml_bytes: bytes, base: str) -> tuple[str, str] | None:
    """Extrae serviceType y controlURL de DeviceConfig desde el XML TR-064."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for service in root.iter():
        tag = service.tag.rsplit("}", 1)[-1]
        if tag != "service":
            continue
        service_type = ""
        control_url = ""
        for child in service:
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag == "serviceType":
                service_type = (child.text or "").strip()
            elif child_tag == "controlURL":
                control_url = (child.text or "").strip()
        if "DeviceConfig" in service_type and control_url:
            return service_type, urllib.parse.urljoin(base + "/", control_url)
    return None


def run_tr064(url: str, username: str, password: str, action: str, log: Callable[[str], None]) -> str:
    """Envía Reboot o FactoryReset usando TR-064/UPnP."""
    host = host_from_url(url)
    soap_action = TR064_ACTIONS[action]
    log("Buscando la interfaz TR-064 del router…")
    discovered = discover_tr064(host, username, password)
    if discovered:
        service_type, control_url = discovered
        log(f"Servicio encontrado: {service_type}")
        return send_tr064_action(control_url, service_type, soap_action, username, password, action, log)

    log("No se encontró el descriptor. Probando la ruta habitual TR-064…")
    fallback_type = "urn:dslforum-org:service:DeviceConfig:1"
    fallback_url = f"http://{host}:49000/upnp/control/deviceconfig"
    return send_tr064_action(fallback_url, fallback_type, soap_action, username, password, action, log)


def send_tr064_action(
    control_url: str,
    service_type: str,
    soap_action: str,
    username: str,
    password: str,
    action: str,
    log: Callable[[str], None],
) -> str:
    """POST SOAP concreto a la controlURL TR-064 descubierta."""
    body = SOAP_ACTION.format(service_type=service_type, action=soap_action).encode("utf-8")
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service_type}#{soap_action}"',
    }
    log(f"Enviando {action_noun(action)} por TR-064…")
    try:
        result = http_request(control_url, "POST", username, password, data=body, headers=headers)
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        raise
    if result.status >= 200 and result.status < 300:
        if action == "factory":
            return "Orden de restauración de fábrica enviada. El router tardará varios minutos y volverá con la configuración original."
        return "Orden de reinicio enviada por TR-064. El router tardará unos minutos en volver."
    raise RouterError(f"TR-064 respondió con el código HTTP {result.status}.")


# ---------------------------------------------------------------------------
# Acciones HTTP directas (GET/POST a rutas conocidas)
# ---------------------------------------------------------------------------


def http_action(
    url: str,
    username: str,
    password: str,
    method: str,
    action: str,
    log: Callable[[str], None],
) -> str:
    """Envía GET/POST a una URL concreta y valida si la acción se aceptó."""
    log(f"Enviando {method} a {url}…")
    try:
        result = http_request(url, method, username, password)
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        raise
    if response_suggests_action_accepted(result, action):
        return (
            f"El router respondió {result.status}. "
            f"Parece que aceptó {action_article_noun(action)}."
        )
    if looks_like_login_page(result.text) or path_looks_like_management_page(urllib.parse.urlparse(url).path):
        raise RouterError(
            "La URL apunta a la página de administración o de login, no a una orden de "
            f"{action_noun(action)}. Usa el método Automático o una URL específica de reinicio."
        )
    if result.status >= 200 and result.status < 400:
        origin = origin_from_url(url)
        log("La respuesta no confirma la orden. Comprobando si el router se ha caído…")
        if router_became_unreachable(origin, username, password):
            return f"Orden enviada. El router dejó de responder; es probable que haya empezado {action_article_noun(action)}."
        raise RouterError(
            f"El router respondió {result.status}, pero sigue activo y no confirma "
            f"{action_article_noun(action)}. Prueba otra URL o el método Automático."
        )
    raise RouterError(f"El router respondió con el código HTTP {result.status}.")


# ---------------------------------------------------------------------------
# Sesión por sessionKey (Comtrend / firmwares antiguos)
# ---------------------------------------------------------------------------


def extract_session_key(text: str) -> str | None:
    """Intenta sacar sessionKey/session_token del HTML del router."""
    patterns = (
        r"sessionKey\s*=\s*['\"]([^'\"]+)['\"]",
        r"sessionKey\s*=\s*([0-9]+)",
        r'name=["\']sessionKey["\']\s+value=["\']([^"\']+)["\']',
        r"session_token\s*=\s*['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def try_session_key_reboot(
    origin: str,
    username: str,
    password: str,
    action: str,
    log: Callable[[str], None],
) -> str | None:
    """Flujo Comtrend/Movistar antiguo: resetrouter.html + rebootinfo.cgi."""
    if action != "reboot":
        return None
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(username, password, origin, cookie_jar)
    pages = (
        "/resetrouter.html",
        "/rebootinfo.html",
        "/cgi-bin/resetrouter.html",
        "/html/management/maintenance.asp",
    )
    session_key = None
    for page in pages:
        url = urllib.parse.urljoin(origin + "/", page.lstrip("/"))
        log(f"Buscando clave de sesión en {page}…")
        try:
            result = http_request(url, "GET", username, password, timeout=5, opener=opener, cookie_jar=cookie_jar)
        except RouterError as exc:
            if exc.likely_rebooting:
                return str(exc)
            continue
        session_key = extract_session_key(result.text)
        if session_key:
            break
    if not session_key:
        return None

    targets = (
        f"/rebootinfo.cgi?sessionKey={urllib.parse.quote(session_key)}",
        f"/cgi-bin/rebootinfo.cgi?sessionKey={urllib.parse.quote(session_key)}",
    )
    for path in targets:
        url = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        log(f"Enviando reinicio con sessionKey a {path.split('?')[0]}…")
        try:
            result = http_request(url, "GET", username, password, timeout=5, opener=opener, cookie_jar=cookie_jar)
        except RouterError as exc:
            if exc.likely_rebooting:
                return str(exc)
            continue
        if response_suggests_action_accepted(result, action) or router_became_unreachable(origin, username, password):
            return "Orden de reinicio enviada con sessionKey. El router debería reiniciarse en breve."
    return None

# ---------------------------------------------------------------------------
# Movistar / Mitrastar (MHS): login MD5 + COOKIE_SESSION_KEY
# ---------------------------------------------------------------------------


def extract_movistar_sid(text: str) -> str | None:
    """Lee el sid usado para cifrar la contraseña en logIn_mhs."""
    match = re.search(r"sid\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def looks_like_movistar_mhs_login(text: str) -> bool:
    """Detecta la página de login Movistar/Mitrastar (MHS)."""
    lowered = (text or "").lower()
    return "syspasswd" in lowered and ("hex_md5" in lowered or "login_mhs" in lowered or "logIn_mhs".lower() in lowered)

def try_movistar_mhs(
    origin: str,
    username: str,
    password: str,
    action: str,
    log: Callable[[str], None],
) -> str | None:
    """Login MD5 + reinicio/fábrica en routers Movistar con logIn_mhs.cgi."""
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(username, password, origin, cookie_jar)
    login_url = urllib.parse.urljoin(origin + "/", "cgi-bin/logIn_mhs.cgi")
    log("Detectando interfaz Movistar (logIn_mhs)…")
    try:
        login_page = http_request(login_url, "GET", username, password, timeout=5, opener=opener, cookie_jar=cookie_jar)
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        return None
    if not looks_like_movistar_mhs_login(login_page.text):
        return None

    # La web del router cifra la contraseña como MD5("clave:sid") antes de enviarla.
    sid = extract_movistar_sid(login_page.text) or "7602cdc6"
    hashed = hashlib.md5(f"{password}:{sid}".encode("utf-8")).hexdigest()
    payload = urllib.parse.urlencode(
        {
            "sessionKey": "",
            "submitValue": "1",
            "fake_syspasswd": "",
            "syspasswd": hashed,
            "leaveBlur": "0",
        }
    ).encode("utf-8")
    log("Iniciando sesión en el router Movistar…")
    try:
        login_result = http_request(
            login_url,
            "POST",
            username,
            password,
            data=payload,
            timeout=8,
            opener=opener,
            cookie_jar=cookie_jar,
            headers={"Referer": login_url},
        )
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        raise RouterError(f"No se pudo iniciar sesión en Movistar: {exc}") from exc

    # Sin COOKIE_SESSION_KEY seguimos en la página de login → credenciales incorrectas.
    cookie_names = {cookie.name for cookie in cookie_jar}
    still_login = looks_like_movistar_mhs_login(login_result.text) and "COOKIE_SESSION_KEY" not in cookie_names
    if still_login:
        raise RouterError(
            "El router Movistar rechazó el acceso. "
            "En estos routers suele bastar la contraseña de la pegatina; el usuario a menudo no se usa."
        )

    # Token CSRF opcional; si falla, se intenta la acción sin él.
    session_key = ""
    try:
        key_page = http_request(
            urllib.parse.urljoin(origin + "/", "cgi-bin/sessionkey.cgi"),
            "GET",
            username,
            password,
            timeout=5,
            opener=opener,
            cookie_jar=cookie_jar,
            headers={"Referer": urllib.parse.urljoin(origin + "/", "cgi-bin/backupRestoreSetting.cgi")},
        )
        session_key = extract_session_key(key_page.text) or key_page.text.strip()
        if session_key and len(session_key) > 80:
            session_key = ""
    except RouterError:
        session_key = ""

    # restoreFlag=2 → fábrica; restoreFlag=1 → reinicio.
    if action == "factory":
        targets = (
            (
                "POST",
                "/cgi-bin/backupRestoreSetting.cgi",
                {
                    "sessionKey": session_key,
                    "restoreFlag": "2",
                    "submitSave": "restoreToDefault",
                },
            ),
            (
                "GET",
                "/cgi-bin/backupRestoreSetting.cgi",
                {
                    "sessionKey": session_key,
                    "restoreFlag": "2",
                    "submitSave": "restoreToDefault",
                },
            ),
            (
                "POST",
                "/cgi-bin/mhs_management.cgi",
                {
                    "restoreFlag": "2",
                    "submitSave": "restoreToDefault",
                },
            ),
        )
        success_hint = "Orden de restauración de fábrica enviada al router Movistar."
    else:
        targets = (
            (
                "POST",
                "/cgi-bin/mhs_management.cgi",
                {
                    "restoreFlag": "1",
                    "submitSave": "restart",
                },
            ),
            (
                "POST",
                "/cgi-bin/backupRestoreSetting.cgi",
                {
                    "sessionKey": session_key,
                    "submitSave": "restart",
                    "restoreFlag": "1",
                },
            ),
            (
                "GET",
                "/cgi-bin/rebootinfo.cgi",
                {},
            ),
        )
        success_hint = "Orden de reinicio enviada al router Movistar."

    for method, path, fields in targets:
        url = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        data = urllib.parse.urlencode(fields).encode("utf-8") if fields else None
        if method == "GET" and fields:
            url = f"{url}?{urllib.parse.urlencode(fields)}"
            data = None
        log(f"Enviando {action_noun(action)} Movistar con {method} {path}…")
        try:
            result = http_request(
                url,
                method,
                username,
                password,
                data=data,
                timeout=8,
                opener=opener,
                cookie_jar=cookie_jar,
                headers={"Referer": urllib.parse.urljoin(origin + "/", "cgi-bin/backupRestoreSetting.cgi")},
            )
        except RouterError as exc:
            if exc.likely_rebooting:
                return str(exc)
            log(f"{path} no respondió: {exc}")
            continue

        text_lower = result.text.lower()
        if looks_like_movistar_mhs_login(result.text):
            log(f"{path} devolvió de nuevo la página de login.")
            continue
        if (
            response_suggests_action_accepted(result, action)
            or "rebootinfo" in result.url.lower()
            or "restoreinfo" in result.url.lower()
            or "reinici" in text_lower
            or "restart" in text_lower
            or "please wait" in text_lower
        ):
            return success_hint
        if router_became_unreachable(origin, username, password):
            return success_hint + " El router dejó de responder."

    if router_became_unreachable(origin, username, password):
        return success_hint + " El router dejó de responder."
    return None

# ---------------------------------------------------------------------------
# Login por formulario genérico + rutas de reinicio/fábrica
# ---------------------------------------------------------------------------


def try_form_login_then_action(
    origin: str,
    username: str,
    password: str,
    action: str,
    log: Callable[[str], None],
) -> str | None:
    """Prueba logins por formulario genéricos y luego rutas de reinicio."""
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(username, password, origin, cookie_jar)
    login_paths = (
        "/cgi-bin/login.cgi",
        "/cgi-bin/login_advance.cgi",
        "/index/login.cgi",
        "/login.cgi",
        "/te_acceso_router.cgi",
    )
    payloads = (
        urllib.parse.urlencode({"Username": username, "Password": password}).encode(),
        urllib.parse.urlencode({"username": username, "password": password}).encode(),
        urllib.parse.urlencode({"User": username, "Passwd": password}).encode(),
        urllib.parse.urlencode({"loginUsername": username, "loginPassword": password}).encode(),
        urllib.parse.urlencode({"Loginuser": username, "LoginPasswordValue": password, "submitValue": "1"}).encode(),
        urllib.parse.urlencode({"loginPassword": password}).encode(),
    )
    logged_in = False
    for path in login_paths:
        login_url = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        for payload in payloads:
            log(f"Probando acceso por formulario en {path}…")
            try:
                result = http_request(
                    login_url,
                    "POST",
                    username,
                    password,
                    data=payload,
                    timeout=5,
                    opener=opener,
                    cookie_jar=cookie_jar,
                )
            except RouterError as exc:
                if exc.likely_rebooting:
                    return str(exc)
                continue
            if result.status >= 200 and result.status < 400 and not looks_like_login_page(result.text):
                logged_in = True
                break
            if list(cookie_jar):
                logged_in = True
                break
        if logged_in:
            break
    if not logged_in:
        return None

    attempts = COMMON_FACTORY_ATTEMPTS if action == "factory" else COMMON_REBOOT_ATTEMPTS
    for method, path, data in attempts:
        target = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        log(f"Tras el login, probando {method} {path}…")
        try:
            result = http_request(
                target,
                method,
                username,
                password,
                data=data,
                timeout=5,
                opener=opener,
                cookie_jar=cookie_jar,
            )
        except RouterError as exc:
            if exc.likely_rebooting:
                return str(exc)
            continue
        if response_suggests_action_accepted(result, action):
            return f"Orden enviada tras iniciar sesión ({method} {path})."
        if path_looks_like_action(path, action) and result.status >= 200 and result.status < 400:
            if router_became_unreachable(origin, username, password):
                return f"Orden enviada tras iniciar sesión ({method} {path}). El router dejó de responder."
    return None


# ---------------------------------------------------------------------------
# Orquestación: modo automático, prueba y despacho por método
# ---------------------------------------------------------------------------


def consider_http_result(
    result: HttpResult,
    path: str,
    action: str,
    origin: str,
    username: str,
    password: str,
    log: Callable[[str], None],
) -> str | None:
    """Evalúa si una respuesta HTTP cuenta como éxito o hay que seguir probando."""
    if response_suggests_action_accepted(result, action):
        return f"Orden enviada con respuesta clara en {path}."
    if looks_like_login_page(result.text) or path_looks_like_management_page(path):
        log(f"{path} parece una página de administración/login; se ignora.")
        return None
    if not path_looks_like_action(path, action):
        log(f"{path} respondió {result.status}, pero no parece una ruta de {action_noun(action)}.")
        return None
    if result.status >= 200 and result.status < 400:
        log(f"{path} respondió {result.status}. Comprobando si el router se cae…")
        if router_became_unreachable(origin, username, password):
            return f"Orden enviada con {path}. El router dejó de responder."
        log(f"{path} no provocó reinicio; se sigue buscando.")
    return None


def auto_action(url: str, username: str, password: str, action: str, log: Callable[[str], None]) -> str:
    """Orquesta todos los métodos automáticos de reinicio o restauración."""
    errors: list[str] = []
    origin = origin_from_url(url)
    try:
        return run_tr064(url, username, password, action, log)
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        errors.append(str(exc))
        log(f"TR-064 no disponible: {exc}")

    parsed = urllib.parse.urlparse(url)
    if parsed.path not in {"", "/"} and path_looks_like_action(parsed.path, action):
        for method in ("GET", "POST"):
            try:
                return http_action(url, username, password, method, action, log)
            except RouterError as exc:
                if exc.likely_rebooting:
                    return str(exc)
                errors.append(str(exc))
                log(f"{method} a la URL indicada no funcionó: {exc}")
    elif parsed.path not in {"", "/"}:
        log(
            f"La URL ({parsed.path}) parece la página de administración, "
            f"no una orden de {action_noun(action)}. Se probarán rutas específicas…"
        )

    try:
        movistar_result = try_movistar_mhs(origin, username, password, action, log)
        if movistar_result:
            return movistar_result
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        errors.append(str(exc))
        log(f"Interfaz Movistar no disponible: {exc}")

    try:
        session_result = try_session_key_reboot(origin, username, password, action, log)
        if session_result:
            return session_result
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        errors.append(str(exc))
        log(f"Reinicio por sessionKey no disponible: {exc}")

    try:
        form_result = try_form_login_then_action(origin, username, password, action, log)
        if form_result:
            return form_result
    except RouterError as exc:
        if exc.likely_rebooting:
            return str(exc)
        errors.append(str(exc))
        log(f"Acceso por formulario no disponible: {exc}")

    attempts = COMMON_FACTORY_ATTEMPTS if action == "factory" else COMMON_REBOOT_ATTEMPTS
    for method, path, data in attempts:
        target = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        log(f"Probando {method} {path}…")
        try:
            result = http_request(target, method, username, password, data=data, timeout=5)
        except RouterError as exc:
            if exc.likely_rebooting:
                return str(exc)
            errors.append(str(exc))
            continue
        accepted = consider_http_result(result, path, action, origin, username, password, log)
        if accepted:
            return accepted

    detail = errors[-1] if errors else "sin confirmación de reinicio"
    raise RouterError(
        f"No se pudo confirmar {action_article_noun(action)}. "
        "Tu router parece exigir login por formulario o una URL concreta. "
        "Abre la web del router, reinicia manualmente una vez y copia la URL "
        f"exacta de esa acción; luego usa HTTP GET/POST. Último detalle: {detail}"
    )


def probe_router(url: str, username: str, password: str, log: Callable[[str], None]) -> str:
    """Comprueba que el router responde (sin reiniciarlo)."""
    origin = origin_from_url(url)
    log(f"Comprobando {origin}…")
    try:
        result = http_request(url, "GET", username, password, timeout=6)
        return f"Conexión correcta. El router respondió {result.status}."
    except RouterError as exc:
        if "incorrectos" in str(exc):
            raise
        log(f"La URL principal no respondió: {exc}")

    host = host_from_url(url)
    if discover_tr064(host, username, password):
        return "Conexión correcta. Se detectó la interfaz TR-064 del router."
    raise RouterError("No se pudo contactar con el router. Revisa la URL y que estés en su red.")


def run_action_with_method(
    url: str,
    username: str,
    password: str,
    method: str,
    action: str,
    log: Callable[[str], None],
) -> str:
    """Despacha la acción según el método elegido en la UI."""
    if method == "tr064":
        return run_tr064(url, username, password, action, log)
    if method == "get":
        return http_action(url, username, password, "GET", action, log)
    if method == "post":
        return http_action(url, username, password, "POST", action, log)
    return auto_action(url, username, password, action, log)

