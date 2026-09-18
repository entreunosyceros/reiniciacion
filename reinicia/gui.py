"""Interfaz gráfica de Reiniciación.

Construye la ventana Tkinter, el indicador de internet y conecta
los botones con las operaciones del router y el navegador.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from .browser_login import prepare_browser_login
from .config import (
    ACCENT,
    APP_NAME,
    BG,
    BORDER,
    CONFIG_FILE,
    DANGER,
    DEFAULT_URL,
    DEFAULT_USER,
    FACTORY,
    FACTORY_CONFIRM_WORD,
    ICON_FILE,
    INPUT_BG,
    MUTED,
    NET_CHECK_FAST_MS,
    NET_CHECK_INTERVAL_MS,
    SUCCESS,
    SURFACE,
    SURFACE_2,
    TEXT,
    WARNING,
    local_config,
    save_config,
)
from .http_client import (
    RouterError,
    host_from_url,
    internet_is_up,
    looks_like_local_host,
    normalize_url,
)
from .router_ops import probe_router, run_action_with_method


class App(tk.Tk):
    """Ventana principal de escritorio."""

    # ------------------------------------------------------------------
    # Inicialización y estilo
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        """Crea la ventana, carga preferencias y arranca el indicador de internet."""
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.minsize(500, 800)
        self.geometry("530x860")
        self._set_window_icon()
        self.busy = False
        self._net_online: bool | None = None
        self._net_waiting_recovery = False
        self._net_check_after_id: str | None = None
        self._net_check_running = False
        saved = local_config()

        self.url_var = tk.StringVar(value=saved.get("url") or DEFAULT_URL)
        self.user_var = tk.StringVar(value=saved.get("username") or DEFAULT_USER)
        self.password_var = tk.StringVar(value=saved.get("password") or "")
        self.method_var = tk.StringVar(value=saved.get("method") or "auto")
        self.remember_var = tk.BooleanVar(value=bool(saved.get("remember", True)))
        self.show_password_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Introduce la URL y la contraseña de tu router.")
        self.net_status_var = tk.StringVar(value="Comprobando conexión a internet…")

        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Return>", lambda _event: self.confirm_reboot())
        self._schedule_net_check(200)

    def _load_photo(self, max_side: int) -> tk.PhotoImage | None:
        """Carga y reduce el logo PNG para iconos o la ventana de ayuda."""
        if not ICON_FILE.is_file():
            return None
        try:
            icon = tk.PhotoImage(file=str(ICON_FILE))
        except tk.TclError:
            return None
        side = max(icon.width(), icon.height())
        factor = max(1, (side + max_side - 1) // max_side)
        if factor > 1:
            icon = icon.subsample(factor, factor)
        return icon

    def _set_window_icon(self) -> None:
        """Asigna el icono de la ventana/barra de tareas."""
        icon = self._load_photo(64)
        if icon is None:
            return
        self.iconphoto(True, icon)
        self._window_icon = icon

    def _build_style(self) -> None:
        """Aplica el tema oscuro a los widgets ttk."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Sans", 18, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Sans", 10))
        style.configure("Label.TLabel", background=SURFACE, foreground=MUTED, font=("Sans", 9))
        style.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Sans", 9), wraplength=440)
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT, font=("Sans", 9))
        style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", TEXT)])
        style.configure(
            "Accent.TButton",
            background=DANGER,
            foreground="#fff",
            font=("Sans", 10, "bold"),
            padding=10,
        )
        style.map("Accent.TButton", background=[("active", "#d85a5a"), ("disabled", "#6b3a3a")])
        style.configure(
            "Factory.TButton",
            background=FACTORY,
            foreground="#fff",
            font=("Sans", 10, "bold"),
            padding=10,
        )
        style.map("Factory.TButton", background=[("active", "#9f1239"), ("disabled", "#6b3a3a")])
        style.configure(
            "Ghost.TButton",
            background=SURFACE_2,
            foreground=TEXT,
            font=("Sans", 10),
            padding=9,
        )
        style.map("Ghost.TButton", background=[("active", BORDER), ("disabled", SURFACE_2)])

    def _build_ui(self) -> None:
        """Construye todos los controles de la interfaz."""
        outer = ttk.Frame(self, style="TFrame")
        outer.pack(fill="both", expand=True, padx=24, pady=22)

        ttk.Label(outer, text="Reiniciación", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Reinicia tu router o restáuralo a valores de fábrica desde el escritorio.",
            style="Subtitle.TLabel",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(4, 10))

        net_bar = tk.Frame(outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        net_bar.pack(fill="x", pady=(0, 14))
        net_inner = tk.Frame(net_bar, bg=SURFACE)
        net_inner.pack(fill="x", padx=12, pady=10)
        self.net_dot = tk.Label(net_inner, text="●", bg=SURFACE, fg=WARNING, font=("Sans", 12))
        self.net_dot.pack(side="left")
        self.net_status_label = tk.Label(
            net_inner,
            textvariable=self.net_status_var,
            bg=SURFACE,
            fg=TEXT,
            font=("Sans", 10),
            anchor="w",
        )
        self.net_status_label.pack(side="left", fill="x", expand=True, padx=(8, 0))

        card = tk.Frame(outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="x", padx=18, pady=18)

        self._labeled_entry(inner, "URL del router", self.url_var)
        self._labeled_entry(inner, "Usuario", self.user_var)
        self._labeled_entry(inner, "Contraseña", self.password_var, show="•")

        show_row = tk.Frame(inner, bg=SURFACE)
        show_row.pack(fill="x", pady=(2, 12))
        tk.Checkbutton(
            show_row,
            text="Mostrar contraseña",
            variable=self.show_password_var,
            command=self.toggle_password,
            bg=SURFACE,
            fg=MUTED,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=SURFACE_2,
            highlightthickness=0,
            font=("Sans", 9),
        ).pack(anchor="w")

        tk.Label(inner, text="Método", bg=SURFACE, fg=MUTED, font=("Sans", 9)).pack(anchor="w")
        self.method_labels = {
            "auto": "Automático (recomendado)",
            "tr064": "TR-064 / UPnP",
            "get": "HTTP GET a la URL",
            "post": "HTTP POST a la URL",
        }
        self.method_display = tk.StringVar(
            value=self.method_labels.get(self.method_var.get(), self.method_labels["auto"])
        )
        method_menu = tk.OptionMenu(
            inner,
            self.method_display,
            *self.method_labels.values(),
            command=self._on_method_selected,
        )
        method_menu.configure(
            bg=INPUT_BG,
            fg=TEXT,
            activebackground=SURFACE_2,
            activeforeground=TEXT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            relief="flat",
            font=("Sans", 10),
            anchor="w",
        )
        method_menu["menu"].configure(
            bg=SURFACE_2,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="#0b1220",
            font=("Sans", 10),
        )
        method_menu.pack(fill="x", pady=(4, 12), ipady=2)
        self.method_help = tk.Label(inner, bg=SURFACE, fg=MUTED, font=("Sans", 8), wraplength=420, justify="left")
        self.method_help.pack(anchor="w", pady=(0, 12))

        tk.Checkbutton(
            inner,
            text="Recordar URL, usuario y contraseña en este equipo",
            variable=self.remember_var,
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=SURFACE_2,
            highlightthickness=0,
            font=("Sans", 9),
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="Se guardan en local y sin cifrar, solo en este ordenador.",
            bg=SURFACE,
            fg=MUTED,
            font=("Sans", 8),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        util_row = tk.Frame(outer, bg=BG)
        util_row.pack(fill="x", pady=(16, 8))
        self.test_button = ttk.Button(util_row, text="Probar conexión", style="Ghost.TButton", command=self.test_connection)
        self.test_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.open_button = ttk.Button(util_row, text="Abrir en el navegador", style="Ghost.TButton", command=self.open_in_browser)
        self.open_button.pack(side="left", fill="x", expand=True)

        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x")
        self.reboot_button = ttk.Button(actions, text="Reiniciar", style="Accent.TButton", command=self.confirm_reboot)
        self.reboot_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.factory_button = ttk.Button(
            actions,
            text="Valores de fábrica",
            style="Factory.TButton",
            command=self.confirm_factory,
        )
        self.factory_button.pack(side="left", fill="x", expand=True)

        status_row = tk.Frame(outer, bg=BG)
        status_row.pack(fill="x", pady=(10, 8))
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=("Sans", 9),
            wraplength=360,
            justify="left",
            anchor="w",
        )
        self.status_label.pack(side="left", fill="x", expand=True, anchor="w")
        ttk.Button(status_row, text="Ayuda", style="Ghost.TButton", command=self.show_help).pack(side="right")

        log_frame = tk.Frame(outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.log_text = tk.Text(
            log_frame,
            height=7,
            bg=INPUT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Sans", 9),
            padx=10,
            pady=10,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

        self._refresh_method_label()

    # ------------------------------------------------------------------
    # Indicador de conexión a internet
    # ------------------------------------------------------------------

    def _schedule_net_check(self, delay_ms: int | None = None) -> None:
        """Programa la siguiente comprobación de internet."""
        if self._net_check_after_id is not None:
            try:
                self.after_cancel(self._net_check_after_id)
            except Exception:
                pass
            self._net_check_after_id = None
        wait = delay_ms
        if wait is None:
            wait = NET_CHECK_FAST_MS if self._net_waiting_recovery else NET_CHECK_INTERVAL_MS
        self._net_check_after_id = self.after(wait, self._start_net_check)

    def _start_net_check(self) -> None:
        """Lanza en segundo plano la sonda de conectividad."""
        self._net_check_after_id = None
        if self._net_check_running:
            self._schedule_net_check()
            return
        self._net_check_running = True
        threading.Thread(target=self._net_check_worker, daemon=True).start()

    def _net_check_worker(self) -> None:
        """Hilo que comprueba si hay salida a internet."""
        try:
            online = internet_is_up()
        except Exception:
            online = False
        self.after(0, lambda: self._apply_net_status(online))

    def _apply_net_status(self, online: bool) -> None:
        """Actualiza el indicador verde/rojo según el resultado."""
        self._net_check_running = False
        previous = self._net_online
        self._net_online = online
        if online:
            self.net_dot.configure(fg=SUCCESS)
            self.net_status_var.set("Conexión a internet: activa")
            if self._net_waiting_recovery and previous is False:
                self._net_waiting_recovery = False
                self.log("Conexión a internet recuperada.")
                self.set_status("Conexión a internet recuperada.", SUCCESS)
            elif previous is False:
                self.log("Conexión a internet recuperada.")
        else:
            self.net_dot.configure(fg=DANGER)
            self.net_status_var.set("Conexión a internet: desconectada")
            if previous is True:
                self.log("Sin conexión a internet.")
        self._schedule_net_check()

    def mark_network_down_after_action(self) -> None:
        """Marca la red como caída y acelera el sondeo tras un reinicio."""
        self._net_waiting_recovery = True
        self._net_online = False
        self.net_dot.configure(fg=DANGER)
        self.net_status_var.set("Conexión a internet: desconectada")
        self._schedule_net_check(800)

    # ------------------------------------------------------------------
    # Controles del formulario
    # ------------------------------------------------------------------

    def _on_method_selected(self, choice: str | None = None) -> None:
        """Sincroniza el desplegable de método con el valor interno."""
        selected = choice or self.method_display.get()
        reverse = {value: key for key, value in self.method_labels.items()}
        self.method_var.set(reverse.get(selected, "auto"))
        self._refresh_method_label()

    def _labeled_entry(self, parent: tk.Frame, label: str, variable: tk.StringVar, show: str | None = None) -> tk.Entry:
        """Crea una etiqueta + campo de texto con el estilo de la app."""
        tk.Label(parent, text=label, bg=SURFACE, fg=MUTED, font=("Sans", 9)).pack(anchor="w")
        entry = tk.Entry(
            parent,
            textvariable=variable,
            show=show or "",
            bg=INPUT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Sans", 11),
        )
        entry.pack(fill="x", pady=(4, 12), ipady=7)
        if show:
            self.password_entry = entry
        return entry

    def _refresh_method_label(self) -> None:
        """Actualiza el texto de ayuda bajo el método elegido."""
        texts = {
            "auto": "Detecta routers Movistar/MHS, TR-064 y rutas HTTP reales. No uses la página de administración como URL de orden.",
            "tr064": "Usa la API TR-064 de muchos routers de operador (puerto 49000).",
            "get": "Hace GET a la URL exacta que escribiste, con usuario y contraseña.",
            "post": "Hace POST a la URL exacta que escribiste, con usuario y contraseña.",
        }
        self.method_help.configure(text=texts.get(self.method_var.get(), texts["auto"]))

    def toggle_password(self) -> None:
        """Muestra u oculta la contraseña en el formulario."""
        self.password_entry.configure(show="" if self.show_password_var.get() else "•")

    # ------------------------------------------------------------------
    # Estado de la UI (log, estado, botones ocupados)
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        """Añade una línea al registro de la ventana (thread-safe)."""
        def append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.after(0, append)

    def set_status(self, message: str, color: str = MUTED) -> None:
        """Actualiza el mensaje de estado inferior (thread-safe)."""
        def apply() -> None:
            self.status_var.set(message)
            self.status_label.configure(fg=color)

        if threading.current_thread() is threading.main_thread():
            apply()
        else:
            self.after(0, apply)

    def set_busy(self, busy: bool) -> None:
        """Deshabilita botones mientras hay una operación en curso."""
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.test_button.configure(state=state)
        self.open_button.configure(state=state)
        self.reboot_button.configure(state=state)
        self.factory_button.configure(state=state)

    def collected_form(self) -> tuple[str, str, str, str]:
        """Lee y valida URL, usuario, contraseña y método del formulario."""
        url = normalize_url(self.url_var.get())
        username = self.user_var.get().strip()
        password = self.password_var.get()
        method = self.method_var.get()
        return url, username, password, method

    def _local_warning(self, url: str) -> str:
        """Aviso extra si la URL no parece de una red local."""
        if looks_like_local_host(host_from_url(url)):
            return ""
        return (
            "\n\nLa dirección no parece de una red local. "
            "Úsalo solo con tu propio router."
        )

    # ------------------------------------------------------------------
    # Acciones del usuario (botones)
    # ------------------------------------------------------------------

    def confirm_reboot(self) -> None:
        """Pide confirmación y lanza el reinicio."""
        if self.busy:
            return
        try:
            url, _username, _password, _method = self.collected_form()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        extra = self._local_warning(url)
        if not messagebox.askyesno(
            APP_NAME,
            "Se va a enviar la orden de reinicio a:\n"
            f"{url}\n\n"
            "Internet y el Wi‑Fi se cortarán unos minutos."
            f"{extra}\n\n¿Reiniciar ahora?",
            icon="warning",
        ):
            return
        self.run_job("Enviando reinicio…", lambda: self._action_job("reboot"))

    def confirm_factory(self) -> None:
        """Doble confirmación (incl. palabra FABRICA) y lanza restauración."""
        if self.busy:
            return
        try:
            url, _username, _password, _method = self.collected_form()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        extra = self._local_warning(url)
        if not messagebox.askyesno(
            APP_NAME,
            "Esto restaurará el router a valores de fábrica:\n"
            f"{url}\n\n"
            "Se perderán el nombre y la contraseña del Wi‑Fi, "
            "los ajustes de internet y el resto de la configuración. "
            "Después habrá que volver a configurar el aparato."
            f"{extra}\n\n¿Continuar?",
            icon="warning",
        ):
            return
        typed = simpledialog.askstring(
            APP_NAME,
            f"Escribe {FACTORY_CONFIRM_WORD} para confirmar la restauración de fábrica:",
            parent=self,
        )
        if (typed or "").strip().upper() != FACTORY_CONFIRM_WORD:
            self.set_status("Restauración de fábrica cancelada.")
            return
        self.run_job("Enviando restauración de fábrica…", lambda: self._action_job("factory"))

    def test_connection(self) -> None:
        """Comprueba acceso al router sin reiniciarlo."""
        if self.busy:
            return
        try:
            self.collected_form()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        self.run_job("Comprobando conexión…", self._probe_job)

    def open_in_browser(self) -> None:
        """Abre el panel del router en el navegador con login si es posible."""
        if self.busy:
            return
        try:
            self.collected_form()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        self.run_job("Abriendo el router en el navegador…", self._open_browser_job)

    def _open_browser_job(self) -> None:
        """Trabajo en segundo plano para preparar el acceso al navegador."""
        try:
            url, username, password, _method = self.collected_form()
            opened = prepare_browser_login(url, username, password, self.log)
            message = f"Navegador abierto con acceso automático: {opened}"
            self.log(message)
            self.set_status("Router abierto en el navegador con los datos de acceso.", SUCCESS)
            self.after(0, lambda m=message: messagebox.showinfo(APP_NAME, "Se ha abierto el navegador e iniciado el acceso al router."))
        except (ValueError, RouterError) as exc:
            error_text = str(exc)
            self.log(error_text)
            self.set_status(error_text, DANGER)
            self.after(0, lambda m=error_text: messagebox.showerror(APP_NAME, m))
        except Exception as exc:
            error_text = f"No se pudo abrir el navegador: {exc}"
            self.log(error_text)
            self.set_status(error_text, DANGER)
            self.after(0, lambda m=error_text: messagebox.showerror(APP_NAME, m))
        finally:
            self.after(0, lambda: self.set_busy(False))
            self.after(0, self.persist_if_needed)

    # ------------------------------------------------------------------
    # Trabajos en segundo plano (no bloquean la ventana)
    # ------------------------------------------------------------------

    def run_job(self, status: str, job: Callable[[], None]) -> None:
        """Ejecuta una tarea larga en un hilo y marca la UI como ocupada."""
        self.set_busy(True)
        self.set_status(status, WARNING)
        self.log(status)
        threading.Thread(target=job, daemon=True).start()

    def _probe_job(self) -> None:
        """Hilo de «Probar conexión»."""
        try:
            url, username, password, _method = self.collected_form()
            message = probe_router(url, username, password, self.log)
            self.log(message)
            self.set_status(message, SUCCESS)
            self.after(0, lambda m=message: messagebox.showinfo(APP_NAME, m))
        except (ValueError, RouterError) as exc:
            error_text = str(exc)
            self.log(error_text)
            self.set_status(error_text, DANGER)
            self.after(0, lambda m=error_text: messagebox.showerror(APP_NAME, m))
        finally:
            self.after(0, lambda: self.set_busy(False))
            self.after(0, self.persist_if_needed)

    def _action_job(self, action: str) -> None:
        """Hilo de reinicio o restauración de fábrica."""
        try:
            url, username, password, method = self.collected_form()
            message = run_action_with_method(url, username, password, method, action, self.log)
            self.log(message)
            self.set_status(message, SUCCESS)
            self.after(0, self.mark_network_down_after_action)
            self.after(0, lambda m=message: messagebox.showinfo(APP_NAME, m))
        except (ValueError, RouterError) as exc:
            error_text = str(exc)
            success_like = isinstance(exc, RouterError) and exc.likely_rebooting
            color = SUCCESS if success_like else DANGER
            self.log(error_text)
            self.set_status(error_text, color)
            if success_like:
                self.after(0, self.mark_network_down_after_action)
                self.after(0, lambda m=error_text: messagebox.showinfo(APP_NAME, m))
            else:
                self.after(0, lambda m=error_text: messagebox.showerror(APP_NAME, m))
        except Exception as exc:
            error_text = f"Error inesperado: {exc}"
            self.log(error_text)
            self.set_status(error_text, DANGER)
            self.after(0, lambda m=error_text: messagebox.showerror(APP_NAME, m))
        finally:
            self.after(0, lambda: self.set_busy(False))
            self.after(0, self.persist_if_needed)

    # ------------------------------------------------------------------
    # Preferencias, ayuda y cierre
    # ------------------------------------------------------------------

    def persist_if_needed(self) -> None:
        """Guarda o borra la configuración local según la casilla Recordar."""
        if not self.remember_var.get():
            if CONFIG_FILE.exists():
                try:
                    CONFIG_FILE.unlink()
                except OSError:
                    pass
            return
        data = {
            "url": self.url_var.get().strip(),
            "username": self.user_var.get().strip(),
            "password": self.password_var.get(),
            "method": self.method_var.get(),
            "remember": True,
        }
        save_config(data)

    def show_help(self) -> None:
        """Muestra la ventana de ayuda con el logo."""
        if getattr(self, "_help_window", None) is not None and self._help_window.winfo_exists():
            self._help_window.lift()
            self._help_window.focus_force()
            return

        help_text = (
            "1. Conéctate a la red de tu router.\n"
            "2. Escribe su URL, por ejemplo http://192.168.1.1\n"
            "3. Introduce usuario y contraseña de administración.\n"
            "4. Pulsa Probar conexión.\n"
            "5. Si quieres, abre la configuración con Abrir en el navegador.\n"
            "6. Elige Reiniciar o Valores de fábrica.\n\n"
            "Reiniciar solo apaga y enciende el router.\n"
            "Valores de fábrica borra toda la configuración; "
            f"para confirmar hay que escribir {FACTORY_CONFIRM_WORD}.\n\n"
            "El método automático usa TR-064 y rutas HTTP comunes. "
            "Si conoces la URL exacta, pégala arriba y elige HTTP GET o POST.\n\n"
            "Los datos se guardan solo en este equipo, en:\n"
            f"{CONFIG_FILE}"
        )

        dialog = tk.Toplevel(self)
        dialog.title("Ayuda")
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.resizable(False, False)
        self._help_window = dialog

        frame = tk.Frame(dialog, bg=BG, padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        logo = self._load_photo(160)
        if logo is not None:
            dialog._help_logo = logo
            tk.Label(frame, image=logo, bg=BG).pack(pady=(0, 14))

        tk.Label(
            frame,
            text=APP_NAME,
            bg=BG,
            fg=TEXT,
            font=("Sans", 16, "bold"),
        ).pack(anchor="center")

        tk.Label(
            frame,
            text=help_text,
            bg=BG,
            fg=MUTED,
            font=("Sans", 10),
            justify="left",
            wraplength=420,
            anchor="w",
        ).pack(fill="x", pady=(12, 18))

        ttk.Button(frame, text="Cerrar", style="Ghost.TButton", command=dialog.destroy).pack()

        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.focus_force()

    def on_close(self) -> None:
        """Detiene el sondeo de red, guarda preferencias y cierra."""
        if self._net_check_after_id is not None:
            try:
                self.after_cancel(self._net_check_after_id)
            except Exception:
                pass
            self._net_check_after_id = None
        self.persist_if_needed()
        self.destroy()


def main() -> None:
    """Punto de entrada de la interfaz gráfica."""
    app = App()
    app.mainloop()
