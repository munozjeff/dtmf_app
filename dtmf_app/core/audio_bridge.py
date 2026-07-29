# -*- coding: utf-8 -*-
"""
core/audio_bridge.py
====================
Puente bidireccional de audio en tiempo real entre el teléfono Android
y una interfaz de audio del PC (auriculares, interfaz USB, etc.).

Flujo de audio:
  phone_in_idx  (entrada) → captura el audio del destinatario → lo reproduce en pc_speaker_idx
  pc_mic_idx    (entrada) → captura el micrófono del agente   → lo reproduce en phone_out_idx

Cada dirección corre en un hilo propio con sounddevice.Stream para minimizar la latencia.

Requisito físico: cable de audio 3.5mm o interfaz USB que conecte:
  - Salida del teléfono  → Entrada del PC   (phone_in_idx)
  - Entrada del teléfono ← Salida del PC    (phone_out_idx)

Latencia típica: 40–80 ms (WASAPI shared mode)
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:
    _SD_OK = False


class AudioBridge(threading.Thread):
    """
    Puente de audio bidireccional PC ↔ Teléfono.

    Parámetros
    ----------
    phone_in_idx   : índice del dispositivo de entrada que recibe el audio del teléfono
    phone_out_idx  : índice del dispositivo de salida que envía audio al teléfono
    pc_speaker_idx : índice del dispositivo de salida donde el agente escucha (puede ser None → default)
    pc_mic_idx     : índice del dispositivo de entrada del micrófono del agente (puede ser None → default)
    block_ms       : tamaño de bloque en ms (latencia ≈ 2 × block_ms)
    gain_in        : ganancia aplicada al audio del teléfono (1.0 = sin cambio)
    gain_out       : ganancia aplicada al micrófono del agente
    on_status      : callback(msg: str, level: str) para eventos de estado
    """

    def __init__(
        self,
        phone_in_idx:   int | None,
        phone_out_idx:  int | None,
        pc_speaker_idx: int | None = None,
        pc_mic_idx:     int | None = None,
        block_ms:       int = 40,
        gain_in:        float = 1.0,
        gain_out:       float = 1.0,
        on_status:      Callable[[str, str], None] | None = None,
    ):
        super().__init__(daemon=True, name="AudioBridge")
        self.phone_in_idx   = phone_in_idx
        self.phone_out_idx  = phone_out_idx
        self.pc_speaker_idx = pc_speaker_idx
        self.pc_mic_idx     = pc_mic_idx
        self.block_ms       = block_ms
        self.gain_in        = float(gain_in)
        self.gain_out       = float(gain_out)
        self._on_status     = on_status

        self._stop_ev   = threading.Event()
        self._ready_ev  = threading.Event()  # se activa cuando ambos streams están abiertos
        self.is_running = False
        self._error:    str | None = None

    # ── API pública ───────────────────────────────────────────────

    def stop(self):
        """Detiene el puente de audio."""
        self._stop_ev.set()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Espera hasta que el puente esté activo. Retorna False si hay error."""
        self._ready_ev.wait(timeout)
        return self.is_running and self._error is None

    @property
    def error(self) -> str | None:
        return self._error

    # ── helpers internos ──────────────────────────────────────────

    def _log(self, msg: str, level: str = "info"):
        print(f"[AudioBridge] {msg}")
        if self._on_status:
            try:
                self._on_status(msg, level)
            except Exception:
                pass

    def _get_sr(self, idx: int | None, kind: str) -> int:
        """Obtiene el sample rate nativo del dispositivo."""
        if not _SD_OK:
            return 44100
        try:
            if idx is None:
                dev = sd.query_devices(kind=kind)
            else:
                dev = sd.query_devices(idx)
            return int(dev["default_samplerate"])
        except Exception:
            return 44100

    def _open_stream_with_retry(self, factory, max_attempts: int = 4, delay: float = 0.3):
        """Abre un sounddevice stream con retry/backoff para WASAPI."""
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            if self._stop_ev.is_set():
                return None
            try:
                return factory()
            except Exception as exc:
                last_exc = exc
                self._log(f"Intento {attempt}/{max_attempts} fallido: {exc}", "warn")
                if attempt < max_attempts:
                    time.sleep(delay)
                    delay *= 1.5
        raise RuntimeError(f"No se pudo abrir stream tras {max_attempts} intentos: {last_exc}")

    # ── Streams ───────────────────────────────────────────────────

    def _run_phone_to_speaker(self, phone_in_sr: int, speaker_sr: int):
        """
        Dirección 1: teléfono → auriculares del agente.
        Captura desde phone_in_idx y reproduce en pc_speaker_idx.
        """
        block_size_in = int(phone_in_sr * self.block_ms / 1000)
        buf = np.zeros((block_size_in, 1), dtype="float32")

        def cb_in(indata, frames, time_info, status):
            np.copyto(buf[:frames], indata[:frames])

        try:
            stream_in  = sd.InputStream(
                device     = self.phone_in_idx,
                channels   = 1,
                samplerate = phone_in_sr,
                blocksize  = block_size_in,
                dtype      = "float32",
                callback   = cb_in,
            )
            block_size_out = int(speaker_sr * self.block_ms / 1000)
            stream_out = sd.OutputStream(
                device     = self.pc_speaker_idx,
                channels   = 1,
                samplerate = speaker_sr,
                blocksize  = block_size_out,
                dtype      = "float32",
            )
            with stream_in, stream_out:
                self._log("🎧 Puente activo: Teléfono → Auriculares", "success")
                while not self._stop_ev.is_set():
                    chunk = buf[:block_size_in].copy() * self.gain_in
                    # Resamplear si sr diferente
                    if phone_in_sr != speaker_sr:
                        from math import gcd
                        from scipy.signal import resample_poly
                        g = gcd(speaker_sr, phone_in_sr)
                        chunk = resample_poly(
                            chunk[:, 0], speaker_sr // g, phone_in_sr // g
                        ).astype("float32").reshape(-1, 1)
                    # Recortar o rellenar al tamaño esperado por el output
                    need = block_size_out
                    if len(chunk) >= need:
                        chunk = chunk[:need]
                    else:
                        chunk = np.pad(chunk, ((0, need - len(chunk)), (0, 0)))
                    stream_out.write(chunk)
        except Exception as exc:
            self._log(f"❌ Error stream Teléfono→Auriculares: {exc}", "error")

    def _run_mic_to_phone(self, mic_sr: int, phone_out_sr: int):
        """
        Dirección 2: micrófono PC → teléfono.
        Captura desde pc_mic_idx y reproduce en phone_out_idx.
        """
        block_size_mic = int(mic_sr * self.block_ms / 1000)
        buf = np.zeros((block_size_mic, 1), dtype="float32")

        def cb_mic(indata, frames, time_info, status):
            np.copyto(buf[:frames], indata[:frames])

        try:
            stream_mic  = sd.InputStream(
                device     = self.pc_mic_idx,
                channels   = 1,
                samplerate = mic_sr,
                blocksize  = block_size_mic,
                dtype      = "float32",
                callback   = cb_mic,
            )
            block_size_phone = int(phone_out_sr * self.block_ms / 1000)
            stream_phone = sd.OutputStream(
                device     = self.phone_out_idx,
                channels   = 1,
                samplerate = phone_out_sr,
                blocksize  = block_size_phone,
                dtype      = "float32",
            )
            with stream_mic, stream_phone:
                self._log("🎤 Puente activo: Micrófono → Teléfono", "success")
                while not self._stop_ev.is_set():
                    chunk = buf[:block_size_mic].copy() * self.gain_out
                    if mic_sr != phone_out_sr:
                        from math import gcd
                        from scipy.signal import resample_poly
                        g = gcd(phone_out_sr, mic_sr)
                        chunk = resample_poly(
                            chunk[:, 0], phone_out_sr // g, mic_sr // g
                        ).astype("float32").reshape(-1, 1)
                    need = block_size_phone
                    if len(chunk) >= need:
                        chunk = chunk[:need]
                    else:
                        chunk = np.pad(chunk, ((0, need - len(chunk)), (0, 0)))
                    stream_phone.write(chunk)
        except Exception as exc:
            self._log(f"❌ Error stream Mic→Teléfono: {exc}", "error")

    # ── run ───────────────────────────────────────────────────────

    def run(self):
        if not _SD_OK:
            self._error = "sounddevice no instalado"
            self._log("❌ sounddevice no disponible", "error")
            self._ready_ev.set()
            return

        self._log("⏳ Iniciando puente de audio bidireccional…", "info")

        # Sample rates de cada interfaz
        phone_in_sr  = self._get_sr(self.phone_in_idx,   "input")
        phone_out_sr = self._get_sr(self.phone_out_idx,  "output")
        speaker_sr   = self._get_sr(self.pc_speaker_idx, "output")
        mic_sr       = self._get_sr(self.pc_mic_idx,     "input")

        self._log(
            f"  phone_in={phone_in_sr}Hz  phone_out={phone_out_sr}Hz  "
            f"speaker={speaker_sr}Hz  mic={mic_sr}Hz",
            "info",
        )

        self.is_running = True
        self._ready_ev.set()

        # Lanzar las dos direcciones en hilos separados
        t1 = threading.Thread(
            target=self._run_phone_to_speaker,
            args=(phone_in_sr, speaker_sr),
            daemon=True, name="Bridge-PhoneToSpkr",
        )
        t2 = threading.Thread(
            target=self._run_mic_to_phone,
            args=(mic_sr, phone_out_sr),
            daemon=True, name="Bridge-MicToPhone",
        )
        t1.start()
        t2.start()

        # Esperar hasta que se solicite parar
        self._stop_ev.wait()

        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        self.is_running = False
        self._log("🔇 Puente de audio detenido", "info")
