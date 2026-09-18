"""Apertura del panel del router en el navegador con sesión.

En Movistar se inicia sesión en Python y se expone un proxy local
para evitar el error 403 al hacer login desde localhost.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import http.cookiejar
import http.server
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable

from .config import USER_AGENT
from .http_client import (
    RouterError,
    build_opener,
    http_request,
    origin_from_url,
    ssl_context,
)
from .router_ops import extract_movistar_sid, looks_like_movistar_mhs_login

# ---------------------------------------------------------------------------
# Utilidades de URL, cookies y reescritura de contenido
# ---------------------------------------------------------------------------


def basic_auth_url(url: str, username: str, password: str) -> str:
    """Construye una URL con usuario:contraseña embebidos (Basic Auth)."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return url
    userinfo = f"{urllib.parse.quote(username, safe='')}:{urllib.parse.quote(password, safe='')}"
    netloc = f"{userinfo}@{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunparse(
        (parsed.scheme or "http", netloc, parsed.path or "/", parsed.params, parsed.query, parsed.fragment)
    )


def cookie_header_for(url: str, cookie_jar: http.cookiejar.CookieJar) -> str:
    """Genera la cabecera Cookie a partir del jar de sesión."""
    request = urllib.request.Request(url)
    cookie_jar.add_cookie_header(request)
    return request.get_header("Cookie") or ""


def rewrite_router_payload(data: bytes, router_origin: str, proxy_origin: str, content_type: str) -> bytes:
    """Reescribe enlaces del HTML/CSS/JS del router hacia el proxy local."""
    if not data:
        return data
    lowered = (content_type or "").lower()
    if not any(token in lowered for token in ("text/html", "text/css", "javascript", "json", "xml")):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError:
            return data
    router_parsed = urllib.parse.urlparse(router_origin)
    host = router_parsed.hostname or ""
    replacements = (
        (router_origin, proxy_origin),
        (router_origin.replace("http://", "https://"), proxy_origin),
        (f"//{host}", f"//{urllib.parse.urlparse(proxy_origin).netloc}"),
        (f"http://{host}", proxy_origin),
        (f"https://{host}", proxy_origin),
    )
    for old, new in replacements:
        if old:
            text = text.replace(old, new)
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Proxy local: el navegador habla con 127.0.0.1; Python reenvía al router
# ---------------------------------------------------------------------------


def start_router_session_proxy(
    router_origin: str,
    cookie_jar: http.cookiejar.CookieJar,
    start_path: str,
    log: Callable[[str], None] | None = None,
    ttl_seconds: int = 900,
) -> str:
    """Proxy local autenticado: el navegador usa 127.0.0.1 y las cookies van al router."""
    router_origin = router_origin.rstrip("/")
    state = {
        "jar": cookie_jar,
        "origin": router_origin,
        "proxy_origin": "",
    }

    class RouterProxyHandler(http.server.BaseHTTPRequestHandler):
        def _proxy(self) -> None:
            try:
                self._proxy_request()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # El navegador cerró la pestaña o canceló la descarga.
                return

        def _proxy_request(self) -> None:
            target_url = state["origin"] + (self.path if self.path.startswith("/") else "/" + self.path)
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length > 0 else None
            router_host = urllib.parse.urlparse(state["origin"]).netloc
            headers = {
                "User-Agent": self.headers.get("User-Agent") or USER_AGENT,
                "Accept": self.headers.get("Accept") or "*/*",
                "Referer": state["origin"] + "/",
                "Host": router_host,
            }
            content_type = self.headers.get("Content-Type")
            if content_type:
                headers["Content-Type"] = content_type
            cookie = cookie_header_for(target_url, state["jar"])
            if cookie:
                headers["Cookie"] = cookie

            request = urllib.request.Request(target_url, data=body, headers=headers, method=self.command)
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(state["jar"]),
                urllib.request.HTTPSHandler(context=ssl_context()),
            )
            try:
                with opener.open(request, timeout=20) as response:
                    raw = response.read()
                    status = getattr(response, "status", 200)
                    resp_headers = dict(response.headers.items())
                    final_url = response.geturl()
            except urllib.error.HTTPError as exc:
                raw = exc.read() if hasattr(exc, "read") else b""
                status = exc.code
                resp_headers = dict(exc.headers.items()) if exc.headers else {}
                final_url = target_url
            except Exception as exc:
                message = f"No se pudo contactar con el router: {exc}".encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return

            content_type = resp_headers.get("Content-Type", "application/octet-stream")
            location = resp_headers.get("Location")
            if location:
                absolute = urllib.parse.urljoin(final_url, location)
                if absolute.startswith(state["origin"]):
                    location = state["proxy_origin"] + absolute[len(state["origin"]) :]
                elif absolute.startswith("/"):
                    location = state["proxy_origin"] + absolute

            payload = rewrite_router_payload(raw, state["origin"], state["proxy_origin"], content_type)

            self.send_response(status)
            skip = {
                "transfer-encoding",
                "content-length",
                "content-encoding",
                "connection",
                "set-cookie",
                "location",
            }
            for key, value in resp_headers.items():
                if key.lower() in skip:
                    continue
                self.send_header(key, value)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._proxy()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy()

        def do_HEAD(self) -> None:  # noqa: N802
            self._proxy()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    class QuietProxyServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address) -> None:  # noqa: ANN001
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            super().handle_error(request, client_address)

    server = QuietProxyServer(("127.0.0.1", 0), RouterProxyHandler)
    port = server.server_address[1]
    proxy_origin = f"http://127.0.0.1:{port}"
    state["proxy_origin"] = proxy_origin
    if not start_path.startswith("/"):
        start_path = "/" + start_path
    local_url = proxy_origin + start_path

    def run_server() -> None:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(ttl_seconds)
        server.shutdown()
        server.server_close()

    threading.Thread(target=run_server, daemon=True).start()
    time.sleep(0.25)
    if log:
        log(f"Proxy de sesión del router en {proxy_origin} (activo ~{ttl_seconds // 60} min)")
    if not webbrowser.open(local_url, new=2):
        raise RouterError(f"No se pudo abrir el navegador. Prueba: {local_url}")
    return local_url


# ---------------------------------------------------------------------------
# Login Movistar y formularios autoenviados para otros routers
# ---------------------------------------------------------------------------


def login_movistar_cookie_jar(
    origin: str,
    username: str,
    password: str,
    log: Callable[[str], None],
) -> http.cookiejar.CookieJar:
    """Inicia sesión Movistar y devuelve el jar con COOKIE_SESSION_KEY."""
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(username, password, origin, cookie_jar)
    login_url = urllib.parse.urljoin(origin + "/", "cgi-bin/logIn_mhs.cgi")
    login_page = http_request(login_url, "GET", username, password, timeout=5, opener=opener, cookie_jar=cookie_jar)
    if not looks_like_movistar_mhs_login(login_page.text):
        raise RouterError("No se detectó la página de login Movistar.")
    sid = extract_movistar_sid(login_page.text) or "7602cdc6"
    hashed = hashlib.md5(f"{password}:{sid}".encode("utf-8")).hexdigest()
    payload = urllib.parse.urlencode(
        {
            "sessionKey": "",
            "submitValue": "1",
            "fake_syspasswd": "",
            "syspasswd": hashed,
            "leaveBlur": "0",
            "Submit": "Comprobar",
        }
    ).encode("utf-8")
    log("Iniciando sesión Movistar para el navegador…")
    result = http_request(
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
    cookie_names = {cookie.name for cookie in cookie_jar}
    if "COOKIE_SESSION_KEY" not in cookie_names and looks_like_movistar_mhs_login(result.text):
        raise RouterError(
            "No se pudo iniciar sesión en el router. Revisa la contraseña "
            "(en Movistar suele ser la de la pegatina)."
        )
    return cookie_jar


def build_auto_submit_html(action_url: str, fields: dict[str, str], title: str = "Iniciando sesión…") -> str:
    """HTML que envía solo un formulario POST (otros routers)."""
    inputs = []
    for name, value in fields.items():
        inputs.append(
            '<input type="hidden" name="'
            + html_lib.escape(name, quote=True)
            + '" value="'
            + html_lib.escape(value, quote=True)
            + '">'
        )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{html_lib.escape(title)}</title>
  <style>
    body {{ font-family: sans-serif; background: #12151c; color: #f4f6fb; display: flex;
           align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .box {{ text-align: center; }}
  </style>
</head>
<body onload="document.getElementById('autoLogin').submit()">
  <div class="box">
    <p>{html_lib.escape(title)}</p>
    <p style="color:#97a0b5;font-size:0.9rem">Si no redirige, pulsa el botón.</p>
    <form id="autoLogin" method="post" action="{html_lib.escape(action_url, quote=True)}">
      {''.join(inputs)}
      <button type="submit">Entrar en el router</button>
    </form>
  </div>
</body>
</html>
"""


def serve_html_and_open(page_html: str, log: Callable[[str], None] | None = None) -> str:
    """Sirve una página local en 127.0.0.1 y la abre en el navegador."""
    payload = page_html.encode("utf-8")

    class AutoLoginHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AutoLoginHandler)
    port = server.server_address[1]
    local_url = f"http://127.0.0.1:{port}/"

    def run_server() -> None:
        server.timeout = 1
        deadline = time.time() + 20
        while time.time() < deadline:
            server.handle_request()
        server.server_close()

    threading.Thread(target=run_server, daemon=True).start()
    time.sleep(0.15)
    opened = webbrowser.open(local_url, new=2)
    if log:
        log(f"Página de acceso automático abierta en {local_url}")
    if not opened:
        raise RouterError(f"No se pudo abrir el navegador. Prueba manualmente: {local_url}")
    return local_url


# ---------------------------------------------------------------------------
# Entrada pública: elige proxy Movistar, Basic Auth o formulario HTML
# ---------------------------------------------------------------------------


def prepare_browser_login(url: str, username: str, password: str, log: Callable[[str], None]) -> str:
    """Prepara el acceso al router en el navegador, con login automático si es posible."""
    origin = origin_from_url(url)
    movistar_login = urllib.parse.urljoin(origin + "/", "cgi-bin/logIn_mhs.cgi")
    try:
        page = http_request(movistar_login, "GET", username, password, timeout=5)
    except RouterError as exc:
        page = None
        log(f"No se pudo leer logIn_mhs: {exc}")
    else:
        if looks_like_movistar_mhs_login(page.text):
            # Evita el POST desde localhost (el router responde 403).
            # Inicia sesión en Python y abre un proxy local con la sesión.
            cookie_jar = login_movistar_cookie_jar(origin, username, password, log)
            start_path = "/cgi-bin/mhs.cgi"
            parsed_path = urllib.parse.urlparse(url).path
            if parsed_path and parsed_path not in {"/", "/cgi-bin/logIn_mhs.cgi"}:
                if "indexmain" in parsed_path or parsed_path.endswith(".cgi"):
                    start_path = parsed_path if parsed_path.startswith("/") else "/" + parsed_path
            log("Abriendo la configuración del router con sesión iniciada…")
            return start_router_session_proxy(origin, cookie_jar, start_path, log=log)

    # Otros routers con login por formulario clásico (usuario/contraseña en claro).
    form_candidates = (
        ("/cgi-bin/login_advance.cgi", {"Loginuser": username, "LoginPasswordValue": password, "submitValue": "1"}),
        ("/te_acceso_router.cgi", {"loginUsername": username, "loginPassword": password}),
        ("/cgi-bin/login.cgi", {"Loginuser": username, "LoginPasswordValue": password, "submitValue": "1"}),
    )
    for path, fields in form_candidates:
        login_url = urllib.parse.urljoin(origin + "/", path.lstrip("/"))
        try:
            probe = http_request(login_url, "GET", username, password, timeout=4)
        except RouterError:
            continue
        body = probe.text.lower()
        if "password" not in body and "contraseña" not in body and "login" not in body:
            continue
        fields = dict(fields)
        if "hex_md5" in body or "loginsidvalue" in body:
            sid_match = re.search(r"sid\s*=\s*['\"]([^'\"]+)['\"]", probe.text)
            sid = sid_match.group(1) if sid_match else ""
            if sid and "LoginPasswordValue" in fields:
                fields["LoginPasswordValue"] = hashlib.md5(f"{password}:{sid}".encode("utf-8")).hexdigest()
                fields["LoginSidValue"] = sid
        log(f"Preparando acceso automático por formulario ({path})…")
        html_page = build_auto_submit_html(login_url, fields, title="Entrando en el router…")
        return serve_html_and_open(html_page, log)

    auth_url = basic_auth_url(url if urllib.parse.urlparse(url).path not in {"", "/"} else origin + "/", username, password)
    log("Abriendo el router con autenticación básica en el navegador…")
    if webbrowser.open(auth_url, new=2):
        return auth_url

    log("Abriendo la URL del router sin login automático…")
    if webbrowser.open(url, new=2):
        return url
    raise RouterError("No se pudo abrir el navegador.")

