"""Paquete interno de Reiniciación.

Módulos:
- config: constantes y preferencias
- http_client: peticiones HTTP y utilidades de red
- router_ops: reinicio / fábrica / detección
- browser_login: abrir el panel en el navegador
- gui: interfaz de escritorio
"""

from .gui import App, main

__all__ = ["App", "main"]
