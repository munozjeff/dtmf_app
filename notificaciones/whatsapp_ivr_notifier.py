# -*- coding: utf-8 -*-
"""
WhatsApp IVR Notifier
======================
Módulo auto-contenido que gestiona una instancia de Chrome con perfil
persistente para enviar notificaciones de WhatsApp al finalizar cada
llamada del sistema IVR.

Características:
  - Perfil Chrome persistente (no pide QR después del primer escaneo)
  - Cola de notificaciones en segundo plano (no bloquea el IVR)
  - Auto-inicio del navegador si está cerrado cuando se necesita enviar
  - Soporte de grupo principal + número de respaldo
"""

import os
import re
import time
import json
import threading
from queue import Queue, Empty
from datetime import datetime

# ── Selenium ──────────────────────────────────────────────────────────
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, NoSuchElementException, WebDriverException
    )
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    _SELENIUM_OK = True
except ImportError:
    _SELENIUM_OK = False
    print("[WANotif] ⚠ selenium no instalado — notificaciones desactivadas")

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _WDM_OK = True
except ImportError:
    _WDM_OK = False

# ──────────────────────────────────────────────────────────────────────
WHATSAPP_URL = "https://web.whatsapp.com"

# Ruta por defecto del perfil Chrome (junto a la raíz del proyecto)
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROFILE_DIR = os.path.join(_HERE, "whatsapp_chrome_profile")


# ══════════════════════════════════════════════════════════════════════
#  Mensajes de notificación por estado de llamada
# ══════════════════════════════════════════════════════════════════════

STATUS_EMOJI = {
    "ANSWERED_TONE":    "✅",
    "ANSWERED_NO_TONE": "⚠️",
    "NO_ANSWER":        "📵",
    "DISCONNECTED":     "📵",
    "ADB_ERROR":        "❌",
    "STOPPED":          "⏹️",
    "UNKNOWN":          "❓",
}

STATUS_LABEL = {
    "ANSWERED_TONE":    "CONTESTÓ — Tono detectado",
    "ANSWERED_NO_TONE": "CONTESTÓ — Sin tono",
    "NO_ANSWER":        "NO CONTESTÓ",
    "DISCONNECTED":     "DESCONECTADO",
    "ADB_ERROR":        "ERROR ADB",
    "STOPPED":          "CAMPAÑA DETENIDA",
    "UNKNOWN":          "DESCONOCIDO",
}


def build_notification_message(number: str, status: str,
                                digit: str | None = None,
                                option_desc: str | None = None) -> str:
    """Construye el mensaje de notificación para una llamada."""
    emoji = STATUS_EMOJI.get(status, "❓")
    label = STATUS_LABEL.get(status, status)

    lines = [
        f"📞 IVR — Resultado de llamada",
        f"──────────────────────────",
        f"Número:  {number}",
    ]

    if digit and option_desc:
        lines.append(f"Estado:  {emoji} {label}: {digit} ({option_desc})")
    elif digit:
        lines.append(f"Estado:  {emoji} {label}: {digit}")
    else:
        lines.append(f"Estado:  {emoji} {label}")

    lines.append(f"Hora:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  Clase principal
# ══════════════════════════════════════════════════════════════════════

class WhatsAppIVRNotifier:
    """
    Notificador WhatsApp para el sistema IVR.

    Estados del navegador:
      - "closed"   → no hay instancia activa
      - "opening"  → Chrome arrancando / esperando QR
      - "ready"    → WhatsApp Web cargado y listo para enviar
      - "error"    → ocurrió un error; requiere reabrir
    """

    def __init__(self, profile_path: str | None = None):
        self.profile_path = profile_path or DEFAULT_PROFILE_DIR
        os.makedirs(self.profile_path, exist_ok=True)

        self.driver = None
        self._lock  = threading.Lock()

        # Estado observable
        self._status     = "closed"
        self._status_msg = "Navegador cerrado"

        # Cola de notificaciones
        self._queue        = Queue()
        self._worker_stop  = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._start_worker()

    # ── Worker de cola ────────────────────────────────────────────────

    def _start_worker(self):
        self._worker_stop.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="WANotifWorker"
        )
        self._worker_thread.start()

    def _worker_loop(self):
        while not self._worker_stop.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except Empty:
                continue
            try:
                self._do_send(
                    task["contacto"],
                    task["mensaje"],
                    task.get("backup"),
                )
            except Exception as exc:
                print(f"[WANotif] ❌ Error en worker: {exc}")
            finally:
                self._queue.task_done()

    # ── Opciones de Chrome ────────────────────────────────────────────

    def _build_options(self) -> "ChromeOptions":
        opts = ChromeOptions()
        opts.add_argument(f"--user-data-dir={self.profile_path}")
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        # Suprimir logs innecesarios
        opts.add_experimental_option("prefs", {
            "profile.default_content_setting_values.notifications": 2
        })
        return opts

    # ── Abrir / cerrar navegador ──────────────────────────────────────

    def open_browser(self) -> tuple[bool, str]:
        """
        Abre Chrome con el perfil persistente apuntando a WhatsApp Web.
        Si ya hay una instancia activa, la reutiliza.
        Retorna (ok, mensaje).
        """
        if not _SELENIUM_OK:
            return False, "selenium no instalado — ejecuta: pip install selenium webdriver-manager"

        with self._lock:
            # ¿Ya está abierto y respondiendo?
            if self.driver:
                try:
                    _ = self.driver.current_url  # lanza si ya cerró
                    self._status     = "ready" if self._status == "ready" else self._status
                    self._status_msg = "Navegador ya estaba abierto"
                    return True, "Navegador ya está activo"
                except Exception:
                    self.driver = None

            self._status     = "opening"
            self._status_msg = "Iniciando Chrome…"

        # Fuera del lock para no bloquear durante la creación
        try:
            opts = self._build_options()
            if _WDM_OK:
                service = ChromeService(ChromeDriverManager().install())
                driver  = webdriver.Chrome(service=service, options=opts)
            else:
                driver = webdriver.Chrome(options=opts)

            driver.get(WHATSAPP_URL)

            with self._lock:
                self.driver      = driver
                self._status     = "opening"
                self._status_msg = "WhatsApp abierto — escanea el QR si es necesario"

            # Hilo para detectar cuando WA ya está listo
            threading.Thread(
                target=self._wait_for_ready, daemon=True, name="WAReadyWatcher"
            ).start()

            return True, "Chrome abierto. Escanea el QR si es la primera vez."

        except Exception as exc:
            with self._lock:
                self._status     = "error"
                self._status_msg = str(exc)
            return False, f"Error abriendo Chrome: {exc}"

    def _wait_for_ready(self, timeout: int = 120):
        """Espera hasta que la lista de chats de WhatsApp sea visible."""
        try:
            driver = self.driver
            if not driver:
                return
            wait = WebDriverWait(driver, timeout)
            # Selector robusto: panel lateral (siempre presente cuando hay sesión)
            wait.until(EC.presence_of_element_located((
                By.CSS_SELECTOR,
                '#pane-side, div[aria-label="Lista de chats"], div[data-testid="chat-list"]'
            )))
            with self._lock:
                self._status     = "ready"
                self._status_msg = "WhatsApp listo para notificar"
            print("[WANotif] ✅ WhatsApp Web listo")
        except Exception as exc:
            with self._lock:
                self._status     = "error"
                self._status_msg = f"Timeout esperando WhatsApp: {exc}"
            print(f"[WANotif] ❌ Error esperando WhatsApp: {exc}")

    def close_browser(self) -> tuple[bool, str]:
        """Cierra el navegador Chrome."""
        with self._lock:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
            self._status     = "closed"
            self._status_msg = "Navegador cerrado"
        return True, "Navegador cerrado"

    # ── Estado ────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._lock:
            return {
                "status":     self._status,
                "message":    self._status_msg,
                "queue_size": self._queue.qsize(),
                "available":  _SELENIUM_OK,
            }

    # ── Garantizar que el browser esté listo ─────────────────────────

    def ensure_ready(self, auto_open_timeout: int = 90) -> bool:
        """
        Verifica que el browser esté en estado 'ready'.
        Si está cerrado o en error, lo abre automáticamente y espera.
        """
        with self._lock:
            status = self._status

        if status == "ready":
            # Verificar que el driver sigue vivo
            try:
                with self._lock:
                    driver = self.driver
                if driver:
                    _ = driver.current_url
                    return True
            except Exception:
                with self._lock:
                    self.driver  = None
                    self._status = "closed"

        if status in ("closed", "error"):
            print("[WANotif] 🔄 Browser no activo — iniciando automáticamente…")
            ok, msg = self.open_browser()
            if not ok:
                print(f"[WANotif] ❌ No se pudo abrir Chrome: {msg}")
                return False
            status = "opening"

        if status in ("opening",):
            # Esperar hasta que esté listo
            deadline = time.time() + auto_open_timeout
            while time.time() < deadline:
                time.sleep(1.5)
                with self._lock:
                    s = self._status
                if s == "ready":
                    return True
                if s == "error":
                    return False

        return False

    # ── Encolar notificación ──────────────────────────────────────────

    def enqueue_notification(self, contacto: str, mensaje: str,
                             backup: str | None = None):
        """Agrega la notificación a la cola (no bloquea)."""
        self._queue.put({
            "contacto": contacto,
            "mensaje":  mensaje,
            "backup":   backup,
        })
        print(f"[WANotif] 📬 Notificación encolada → '{contacto}' (cola: {self._queue.qsize()})")

    # ── Envío efectivo ────────────────────────────────────────────────

    def _do_send(self, contacto: str, mensaje: str, backup: str | None):
        """Garantiza browser listo y envía. Si falla, intenta el respaldo."""
        if not self.ensure_ready():
            print(f"[WANotif] ❌ WhatsApp no disponible — notificación perdida: {contacto}")
            return

        ok = self._send_to_contact(contacto, mensaje)

        if not ok and backup and backup.strip() and backup != contacto:
            print(f"[WANotif] 🔄 Reintentando con respaldo: '{backup}'")
            ok = self._send_to_contact(backup, mensaje)

        if ok:
            print(f"[WANotif] ✅ Notificación enviada correctamente")
        else:
            print(f"[WANotif] ❌ Falló el envío de la notificación")

    @staticmethod
    def _clean(texto: str) -> str:
        """Elimina emojis que pueden causar problemas en Selenium."""
        return re.sub(r'[\U00010000-\U0010ffff]', '', texto).strip()

    def _send_to_contact(self, contacto: str, mensaje: str) -> bool:
        """
        Abre el chat con 'contacto' en WhatsApp Web y envía el mensaje.
        Estrategia:
          1. Vía rápida: búsqueda en barra lateral → ENTER
          2. Vía URL: solo para números (send?phone=XXX)
        """
        try:
            with self._lock:
                driver = self.driver
            if not driver:
                return False

            mensaje_limpio = self._clean(mensaje)
            chat_abierto   = False

            # ── Estrategia 1: barra de búsqueda lateral ───────────────
            try:
                fast_wait    = WebDriverWait(driver, 6)
                search_input = fast_wait.until(EC.presence_of_element_located((
                    By.XPATH,
                    '//p[contains(@class,"copyable-text") and contains(@class,"x15bjb6t")]'
                    ' | //div[@contenteditable="true"][@data-tab="3"]'
                    ' | //input[@data-tab="3"]'
                )))
                search_input.click()
                time.sleep(0.2)
                search_input.send_keys(Keys.CONTROL + "a")
                search_input.send_keys(Keys.DELETE)
                search_input.send_keys(contacto)
                time.sleep(1.0)
                search_input.send_keys(Keys.ENTER)
                time.sleep(1.5)

                # Verificar que el chat abrió (footer visible)
                try:
                    WebDriverWait(driver, 5).until(EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "div[contenteditable='true'][data-tab='10'], "
                        "div[contenteditable='true'][data-tab='6'], "
                        "footer div[contenteditable]"
                    )))
                    chat_abierto = True
                    print(f"[WANotif] ✓ Chat abierto (vía rápida): '{contacto}'")
                except Exception:
                    chat_abierto = False
                    print(f"[WANotif] ⚠ Vía rápida: chat no abrió en 5s")

            except Exception as exc:
                print(f"[WANotif] ⚠ Barra de búsqueda no disponible: {exc}")

            # ── Estrategia 2: URL directa (solo números) ──────────────
            if not chat_abierto:
                is_number = bool(re.match(r'^\+?[\d\s\-]{7,}$', contacto))
                if is_number:
                    phone = re.sub(r'[^\d]', '', contacto)
                    print(f"[WANotif] 🔗 Abriendo por URL: wa.me/{phone}")
                    driver.get(f"https://web.whatsapp.com/send?phone={phone}")
                    try:
                        WebDriverWait(driver, 20).until(EC.presence_of_element_located((
                            By.CSS_SELECTOR,
                            "div[contenteditable='true'][data-tab='10']"
                        )))
                        chat_abierto = True
                        print(f"[WANotif] ✓ Chat abierto (URL): {phone}")
                    except Exception:
                        print(f"[WANotif] ❌ No se pudo abrir chat por URL con {phone}")

            if not chat_abierto:
                print(f"[WANotif] ❌ No se pudo abrir chat con '{contacto}'")
                return False

            # ── Enviar mensaje ────────────────────────────────────────
            time.sleep(0.5)
            try:
                msg_box = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    "div[contenteditable='true'][data-tab='10'], "
                    "div[contenteditable='true'][data-tab='6']"
                )))
                msg_box.click()
                time.sleep(0.3)

                # Enviar línea por línea usando SHIFT+ENTER
                lines = mensaje_limpio.split('\n')
                for i, line in enumerate(lines):
                    if i > 0:
                        msg_box.send_keys(Keys.SHIFT + Keys.ENTER)
                    msg_box.send_keys(line)

                time.sleep(0.4)
                msg_box.send_keys(Keys.ENTER)   # enviar
                time.sleep(1.5)
                print(f"[WANotif] ✓ Mensaje enviado a '{contacto}'")

                # Volver a la lista de chats con Escape (evita recargar la página)
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    time.sleep(0.5)
                except Exception:
                    pass

                return True

            except Exception as exc:
                print(f"[WANotif] ❌ Error escribiendo/enviando mensaje: {exc}")
                return False

        except Exception as exc:
            import traceback
            print(f"[WANotif] ❌ Error en _send_to_contact: {exc}")
            traceback.print_exc()
            return False

    # ── Ciclo de vida ─────────────────────────────────────────────────

    def shutdown(self):
        """Detiene el worker y cierra el navegador."""
        self._worker_stop.set()
        self.close_browser()
