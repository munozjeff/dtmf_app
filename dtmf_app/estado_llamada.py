# -*- coding: utf-8 -*-
"""
CallMonitor — Monitor de estados de llamada via ADB logcat
==========================================================
Lee la salida de `adb logcat` en tiempo real y detecta los cambios de estado
de la llamada activa: CONNECTING → DIALING → ACTIVE → DISCONNECTED

Mejoras v2:
  - Escucha múltiples tags de Telecom (distintas versiones Android/fabricantes)
  - Watchdog timeout: si no hay eventos en CALL_MONITOR_TIMEOUT_S seg. desde
    el inicio → invoca on_state_change("TIMEOUT") para que la campaña pueda
    reaccionar en lugar de quedarse bloqueada indefinidamente.
  - _adb_cmd() como método privado limpio (sin código duplicado).

Uso como módulo:
    from estado_llamada import CallMonitor
    monitor = CallMonitor(device_id="emulator-5554")
    monitor.start(on_state_change=mi_callback, stop_event=threading.Event())

Uso standalone (prueba):
    python estado_llamada.py [device_id]
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Callable

# ── Importar configuración desde core si está disponible ──────
try:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "dtmf_app"))
    from dtmf_app.core.config import TELECOM_LOGCAT_TAGS, CALL_MONITOR_TIMEOUT_S
except Exception:
    TELECOM_LOGCAT_TAGS    = ["Telecom", "TelecomFramework", "CallsManager", "CallStateMachine"]
    CALL_MONITOR_TIMEOUT_S = 90.0

# Regex para capturar state=XXXX de los logs de Telecom
STATE_REGEX = re.compile(r"\bstate=([A-Z_]+)\b")

# Estados relevantes que queremos rastrear
VALID_STATES = {"CONNECTING", "DIALING", "ACTIVE", "DISCONNECTED", "RINGING", "HOLDING"}


class CallMonitor:
    """
    Monitorea el estado de una llamada telefónica en un dispositivo Android
    a través de `adb logcat`, en un hilo separado para no bloquear.

    Args:
        device_id (str | None): Serial del dispositivo ADB (ej: "emulator-5554").
                                Si es None usa el dispositivo por defecto.
        timeout_s (float):      Segundos sin eventos antes de emitir 'TIMEOUT'.
                                0 o negativo → sin timeout.
    """

    def __init__(
        self,
        device_id: str | None = None,
        timeout_s: float = CALL_MONITOR_TIMEOUT_S,
    ):
        self.device_id   = device_id
        self.timeout_s   = timeout_s
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None  = None

    # ── API pública ────────────────────────────────────────────────

    def start(
        self,
        on_state_change: Callable[[str], None],
        stop_event: threading.Event,
        clear_logs: bool = True,
    ) -> None:
        """
        Inicia el monitoreo en un hilo daemon.

        Args:
            on_state_change: callable(state: str) invocado cuando el estado cambia.
                             Estados posibles: CONNECTING, DIALING, ACTIVE,
                             DISCONNECTED, RINGING, HOLDING, TIMEOUT.
            stop_event:      El monitor se detiene cuando está seteado.
            clear_logs:      Si True, limpia el logcat antes de empezar.
        """
        if clear_logs:
            self._clear_logcat()

        self._thread = threading.Thread(
            target=self._monitor_loop,
            args=(on_state_change, stop_event),
            daemon=True,
            name="CallMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        """Termina el proceso adb logcat si sigue corriendo."""
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

    def join(self, timeout: float = 5.0) -> None:
        """Espera a que el hilo de monitoreo termine."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ── Implementación interna ─────────────────────────────────────

    def _adb_cmd(self, *args: str) -> list[str]:
        """Construye el comando adb con -s device_id si aplica."""
        base = ["adb"]
        if self.device_id:
            base += ["-s", self.device_id]
        return base + list(args)

    def _clear_logcat(self) -> None:
        """Limpia el buffer de logcat para no leer estados de llamadas pasadas."""
        try:
            subprocess.run(
                self._adb_cmd("logcat", "-c"),
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _monitor_loop(
        self,
        on_state_change: Callable[[str], None],
        stop_event: threading.Event,
    ) -> None:
        """
        Bucle principal: lee logcat de múltiples tags Telecom y llama
        on_state_change en cada cambio de estado.

        Mejoras vs v1:
          - Filtra con múltiples tags usando -s tag1 -s tag2 … (más robusto)
          - Watchdog: si no llega ningún evento en self.timeout_s segundos
            desde el arranque, emite 'TIMEOUT' y termina.
        """
        # Construir comando con todos los tags de Telecom
        # adb logcat -v time -s Telecom:* -s TelecomFramework:* ...
        tag_filters = []
        for tag in TELECOM_LOGCAT_TAGS:
            tag_filters += ["-s", f"{tag}:*"]

        cmd = self._adb_cmd("logcat", "-v", "time") + tag_filters
        last_state    = None
        last_event_t  = time.monotonic()
        timeout_active = self.timeout_s > 0

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="ignore",
            )

            for line in self._process.stdout:
                if stop_event.is_set():
                    break

                # ── Watchdog: comprobar timeout ─────────────────────
                if timeout_active:
                    elapsed = time.monotonic() - last_event_t
                    if elapsed > self.timeout_s and last_state is None:
                        print(f"[CallMonitor] ⏰ Timeout ({self.timeout_s:.0f}s) sin estado — emitiendo TIMEOUT")
                        try:
                            on_state_change("TIMEOUT")
                        except Exception:
                            pass
                        break

                match = STATE_REGEX.search(line)
                if not match:
                    continue

                state = match.group(1)
                if state not in VALID_STATES:
                    continue
                if state == last_state:
                    continue

                last_state   = state
                last_event_t = time.monotonic()

                try:
                    on_state_change(state)
                except Exception as exc:
                    print(f"[CallMonitor] Error en callback: {exc}")

        except Exception as exc:
            if not stop_event.is_set():
                print(f"[CallMonitor] Error en monitor_loop: {exc}")
        finally:
            self.stop()


# ══════════════════════════════════════════════
#  Modo standalone — prueba directa
# ══════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    device = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"[CallMonitor] Iniciando prueba — dispositivo: {device or 'por defecto'}")
    print("[CallMonitor] Haz una llamada en el dispositivo. CTRL+C para salir.\n")

    ev = threading.Event()
    monitor = CallMonitor(device_id=device, timeout_s=0)  # sin timeout en prueba

    def on_state(state: str):
        icons = {
            "CONNECTING":   "🔄",
            "DIALING":      "📞",
            "ACTIVE":       "✅",
            "DISCONNECTED": "❌",
            "RINGING":      "🔔",
            "HOLDING":      "⏸️",
            "TIMEOUT":      "⏰",
        }
        icon = icons.get(state, "📊")
        print(f"  {icon} Estado detectado: {state}")

    monitor.start(on_state_change=on_state, stop_event=ev)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[CallMonitor] Detenido por el usuario.")
        ev.set()
        monitor.stop()
