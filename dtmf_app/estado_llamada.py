# -*- coding: utf-8 -*-
"""
CallMonitor — Monitor de estados de llamada via ADB logcat
==========================================================
Lee la salida de `adb logcat -s Telecom` en tiempo real y detecta
los cambios de estado de la llamada activa:
    CONNECTING → DIALING → ACTIVE → DISCONNECTED

Uso como módulo:
    from estado_llamada import CallMonitor
    monitor = CallMonitor(device_id="emulator-5554")
    monitor.start(on_state_change=mi_callback, stop_event=threading.Event())

Uso standalone (prueba):
    python estado_llamada.py
"""

import re
import subprocess
import threading
import time

# Regex para capturar state=XXXX de los logs de Telecom
STATE_REGEX = re.compile(r"state=([A-Z_]+)")

# Estados relevantes que queremos rastrear
VALID_STATES = {"CONNECTING", "DIALING", "ACTIVE", "DISCONNECTED", "RINGING", "HOLDING"}


class CallMonitor:
    """
    Monitorea el estado de una llamada telefónica en un dispositivo Android
    a través de `adb logcat`, en un hilo separado para no bloquear.

    Args:
        device_id (str | None): Serial del dispositivo ADB (ej: "emulator-5554").
                                Si es None usa el dispositivo por defecto.
    """

    def __init__(self, device_id: str | None = None):
        self.device_id = device_id
        self._process: subprocess.Popen | None = None
        self._thread:  threading.Thread | None = None

    # ── API pública ────────────────────────────────────────────────

    def start(
        self,
        on_state_change,
        stop_event: threading.Event,
        clear_logs: bool = True,
    ):
        """
        Inicia el monitoreo en un hilo daemon.

        Args:
            on_state_change: callable(state: str) invocado cuando el estado cambia.
            stop_event: threading.Event; el monitor se detiene cuando está seteado.
            clear_logs: si True limpia el logcat antes de empezar.
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

    def stop(self):
        """Termina el proceso adb logcat si sigue corriendo."""
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

    def join(self, timeout: float = 5.0):
        """Espera a que el hilo de monitoreo termine."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ── Implementación interna ─────────────────────────────────────

    def _adb_cmd(self, *args) -> list[str]:
        """Construye el comando adb con -s device_id si aplica."""
        base = ["adb"]
        if self.device_id:
            base += ["-s", self.device_id]
        return base + list(args)

    def _clear_logcat(self):
        """Limpia el buffer de logcat para no leer estados de llamadas pasadas."""
        try:
            subprocess.run(
                self._adb_cmd("logcat", "-c"),
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _monitor_loop(self, on_state_change, stop_event: threading.Event):
        """Bucle principal: lee logcat y llama on_state_change en cada cambio."""
        cmd = self._adb_cmd("logcat", "-v", "time", "-s", "Telecom")
        last_state = None

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

                match = STATE_REGEX.search(line)
                if not match:
                    continue

                state = match.group(1)
                if state not in VALID_STATES:
                    continue
                if state == last_state:
                    continue

                last_state = state
                try:
                    on_state_change(state)
                except Exception as exc:
                    print(f"[CallMonitor] Error en callback: {exc}")

        except Exception as exc:
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
    monitor = CallMonitor(device_id=device)

    def on_state(state):
        icons = {
            "CONNECTING":    "🔄",
            "DIALING":       "📞",
            "ACTIVE":        "✅",
            "DISCONNECTED":  "❌",
            "RINGING":       "🔔",
            "HOLDING":       "⏸️",
        }
        icon = icons.get(state, "📊")
        print(f"{icon} Estado detectado: {state}")

    monitor.start(on_state_change=on_state, stop_event=ev)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[CallMonitor] Detenido por el usuario.")
        ev.set()
        monitor.stop()
