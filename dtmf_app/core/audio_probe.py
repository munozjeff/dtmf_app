"""
dtmf_app/core/audio_probe.py
──────────────────────────────────────────────────────────────────────
Auto-detección de canal de audio asociado a un dispositivo ADB.

Algoritmo:
  1. Push del tono de calibración (calib_tone.wav) al dispositivo vía ADB
  2. Reproducción del tono en el altavoz del teléfono vía Intent Android
  3. Escucha simultánea en TODOS los canales de entrada disponibles
     que NO estén ya ocupados por otras sesiones activas
  4. Análisis Goertzel @ 3750 Hz en cada canal
  5. El primer canal que supere el umbral de energía → canal detectado
  6. Devuelve (input_idx, output_idx) — la salida se infiere del mismo
     dispositivo USB (usualmente input_idx == output_idx - 1 o mismo)

Frecuencia de calibración: 3750 Hz
  - Fuera del rango DTMF (697–1633 Hz)
  - Fuera del rango de ring tone (300–500 Hz)
  - Audible y bien reproducible por el altavoz del teléfono
"""

from __future__ import annotations
import os
import subprocess
import threading
import time
import numpy as np
from typing import Callable

try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

# Ruta al tono de calibración (relativa al paquete)
_SOUNDS_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sounds")
CALIB_TONE   = os.path.join(_SOUNDS_DIR, "calib_tone.wav")
CALIB_FREQ   = 3750     # Hz
CALIB_DEVICE = "/sdcard/Download/calib_tone.wav"   # ruta en Android


def _goertzel_energy(frame: np.ndarray, target_hz: float, sr: int) -> float:
    """
    Calcula la energía Goertzel en `target_hz` para el frame dado.
    Normalizada por N² → comparable entre frames de distinto tamaño.
    """
    N     = len(frame)
    if N == 0:
        return 0.0
    k     = int(0.5 + N * target_hz / sr)
    omega = 2.0 * np.pi * k / N
    coeff = 2.0 * np.cos(omega)
    s1 = s2 = 0.0
    for x in frame:
        s  = float(x) + coeff * s1 - s2
        s2 = s1
        s1 = s
    return (s2**2 + s1**2 - coeff * s1 * s2) / (N * N)


class ChannelListener(threading.Thread):
    """
    Escucha en un canal de entrada específico buscando el tono de calibración.
    Llama a `found_cb(device_idx)` la primera vez que detecta el tono.
    """

    BLOCK_MS    = 50     # ms por bloque de análisis
    ENERGY_THR  = 0.001  # umbral Goertzel mínimo para "detectado"
    RATIO_THR   = 5.0    # ratio energía_calib / energía_total (selectividad)

    def __init__(self,
                 device_idx: int,
                 found_cb: Callable[[int], None],
                 stop_ev: threading.Event):
        super().__init__(daemon=True, name=f"ChListener-{device_idx}")
        self.device_idx = device_idx
        self.found_cb   = found_cb
        self.stop_ev    = stop_ev
        self.detected   = False
        self.peak_energy = 0.0

    def run(self):
        if not _SD_OK:
            return
        try:
            dev_info = sd.query_devices(self.device_idx, "input")
            sr       = int(dev_info["default_samplerate"])
            block    = int(sr * self.BLOCK_MS / 1000)

            with sd.InputStream(
                device     = self.device_idx,
                channels   = 1,
                samplerate = sr,
                blocksize  = block,
                dtype      = "float32",
            ) as stream:
                while not self.stop_ev.is_set():
                    data, _ = stream.read(block)
                    if self.stop_ev.is_set():
                        break

                    frame = data[:, 0] if data.ndim > 1 else data.ravel()
                    frame = frame.astype(np.float32)

                    energy_total = float(np.mean(frame**2))
                    if energy_total < 1e-8:
                        continue   # silencio — no analizar

                    e_calib = _goertzel_energy(frame, CALIB_FREQ, sr)
                    self.peak_energy = max(self.peak_energy, e_calib)

                    ratio = e_calib / (energy_total + 1e-12)

                    if e_calib >= self.ENERGY_THR and ratio >= self.RATIO_THR:
                        if not self.detected:
                            self.detected = True
                            print(f"[Probe] ✅ Canal {self.device_idx}: "
                                  f"Goertzel={e_calib:.4f} ratio={ratio:.1f}")
                            self.found_cb(self.device_idx)
                        return

        except Exception as exc:
            print(f"[Probe] Canal {self.device_idx} error: {exc}")


class AudioChannelProber:
    """
    Orquesta la auto-detección de canal de audio para un dispositivo ADB.

    Uso:
        prober = AudioChannelProber(
            device_id       = "emulator-5554",
            occupied_inputs = {0, 2},   # canales ya en uso por otras sesiones
            on_found        = lambda in_idx, out_idx: ...,
            on_error        = lambda msg: ...,
        )
        prober.start()
        # Esperar: on_found o on_error se llamará desde un hilo daemon
    """

    PUSH_TIMEOUT = 10    # s — timeout para adb push
    PLAY_TIMEOUT = 8     # s — timeout para adb shell am start
    LISTEN_SECS  = 6     # s — duración de escucha tras reproducir

    def __init__(self,
                 device_id: str,
                 occupied_inputs: "set[int] | None" = None,
                 on_found:  "Callable[[int, int | None], None] | None" = None,
                 on_error:  "Callable[[str], None] | None" = None,
                 on_status: "Callable[[str], None] | None" = None):
        self.device_id       = device_id
        self.occupied_inputs = occupied_inputs or set()
        self.on_found        = on_found  or (lambda in_idx, out_idx: None)
        self.on_error        = on_error  or (lambda msg: None)
        self.on_status       = on_status or (lambda msg: None)
        self._thread: "threading.Thread | None" = None
        self._result_in: "int | None"  = None
        self._result_out: "int | None" = None
        self._done = threading.Event()

    # ── API pública ───────────────────────────────────────────────

    def start(self):
        """Inicia la detección en un hilo daemon."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"Probe-{self.device_id}"
        )
        self._thread.start()

    def wait(self, timeout: float = 30.0) -> "tuple[int | None, int | None]":
        """Bloquea hasta que la detección termina. Devuelve (in_idx, out_idx)."""
        self._done.wait(timeout=timeout)
        return self._result_in, self._result_out

    # ── Lógica interna ────────────────────────────────────────────

    def _adb(self, *args: str, timeout: int = 10):
        cmd = ["adb", "-s", self.device_id] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _run(self):
        if not _SD_OK:
            self.on_error("sounddevice no disponible — instala con: pip install sounddevice")
            self._done.set()
            return

        if not os.path.isfile(CALIB_TONE):
            self.on_error(f"Tono de calibración no encontrado: {CALIB_TONE}")
            self._done.set()
            return

        self.on_status("🔍 Iniciando auto-detección de canal…")

        # ── 1. Push del tono al dispositivo ──────────────────────
        self.on_status("📤 Enviando tono al dispositivo…")
        try:
            r = self._adb("push", CALIB_TONE, CALIB_DEVICE, timeout=self.PUSH_TIMEOUT)
            if r.returncode != 0:
                self.on_error(f"adb push falló: {r.stderr.strip()}")
                self._done.set()
                return
        except Exception as exc:
            self.on_error(f"adb push error: {exc}")
            self._done.set()
            return

        # ── 2. Obtener canales de entrada disponibles ─────────────
        try:
            all_devs = sd.query_devices()
        except Exception as exc:
            self.on_error(f"Error listando dispositivos de audio: {exc}")
            self._done.set()
            return

        candidate_inputs: list[int] = []
        for idx, dev in enumerate(all_devs):
            if dev.get("max_input_channels", 0) > 0 and idx not in self.occupied_inputs:
                candidate_inputs.append(idx)

        if not candidate_inputs:
            self.on_error("No hay canales de entrada disponibles (todos ocupados)")
            self._done.set()
            return

        self.on_status(f"🎤 Escuchando en {len(candidate_inputs)} canal(es): {candidate_inputs}")

        # ── 3. Iniciar listeners en todos los canales libres ───────
        stop_ev   = threading.Event()
        found_ev  = threading.Event()
        result    = {"in_idx": None}

        def found_cb(in_idx: int):
            if not found_ev.is_set():
                result["in_idx"] = in_idx
                found_ev.set()
                stop_ev.set()

        listeners: list[ChannelListener] = []
        for idx in candidate_inputs:
            lst = ChannelListener(idx, found_cb, stop_ev)
            lst.start()
            listeners.append(lst)

        # ── 4. Reproducir tono en el dispositivo ──────────────────
        # Pequeña pausa para que los streams se abran antes de reproducir
        time.sleep(0.6)

        self.on_status(f"📢 Reproduciendo tono {CALIB_FREQ} Hz en {self.device_id}…")
        try:
            self._adb(
                "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", f"file://{CALIB_DEVICE}",
                "-t", "audio/wav",
                timeout=self.PLAY_TIMEOUT
            )
        except Exception as exc:
            stop_ev.set()
            self.on_error(f"Error reproduciendo tono: {exc}")
            self._done.set()
            return

        # ── 5. Esperar detección o timeout ─────────────────────────
        found_ev.wait(timeout=self.LISTEN_SECS)
        stop_ev.set()   # detener listeners restantes

        for lst in listeners:
            lst.join(timeout=1.0)

        # ── 6. Determinar canal de salida ──────────────────────────
        in_idx = result["in_idx"]

        if in_idx is None:
            self.on_status("⚠️ No se detectó el tono — intenta subir el volumen del teléfono")
            self.on_error("Canal de audio no detectado automáticamente")
            self._done.set()
            return

        # Buscar el dispositivo de salida del mismo adaptador USB:
        # en la mayoría de interfaces USB, input y output son dispositivos adyacentes
        out_idx = self._find_matching_output(in_idx, all_devs)

        self._result_in  = in_idx
        self._result_out = out_idx
        self.on_status(
            f"✅ Canal detectado: entrada={in_idx}"
            + (f" salida={out_idx}" if out_idx is not None else " (salida: usar defecto)")
        )
        self.on_found(in_idx, out_idx)
        self._done.set()

    def _find_matching_output(self, in_idx: int, all_devs) -> "int | None":
        """
        Busca el dispositivo de salida que probablemente es el mismo adaptador USB
        que el de entrada. Estrategia: nombre similar en el rango in_idx±2.
        """
        in_name = (all_devs[in_idx].get("name", "") if in_idx < len(all_devs) else "").lower()

        for delta in (0, 1, -1, 2, -2):
            out_idx = in_idx + delta
            if out_idx < 0 or out_idx >= len(all_devs):
                continue
            dev = all_devs[out_idx]
            if dev.get("max_output_channels", 0) > 0:
                name = dev.get("name", "").lower()
                # Si comparten parte del nombre → mismo adaptador
                words_in  = set(w for w in in_name.split() if len(w) > 2)
                words_out = set(w for w in name.split()    if len(w) > 2)
                if words_in & words_out:
                    return out_idx
                # Fallback: dispositivo adyacente con salida
                if delta in (0, 1, -1):
                    return out_idx

        return None
