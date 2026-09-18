#!/usr/bin/env python3
"""Reiniciación: reinicia o restaura el router desde el escritorio.

Este archivo solo arranca la aplicación. La lógica está repartida en el
paquete ``reinicia`` (config, HTTP, operaciones del router, navegador y GUI).
"""

from reinicia.gui import main

if __name__ == "__main__":
    main()
