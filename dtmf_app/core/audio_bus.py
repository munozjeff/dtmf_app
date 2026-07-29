# -*- coding: utf-8 -*-
"""
dtmf_app/core/audio_bus.py
===========================
Bus de audio de stream único con fan-out a múltiples consumidores.

Problema que resuelve:
    En el código anterior, PreCallAudioAnalyzer y PythonAudioMonitor
    abrían sd.InputStream al mismo dispositivo en momentos solapados.
    Con WASAPI exclusivo eso causaba fallo silencioso o glitches.

Solución:
    Un único InputStream → AudioBus → colas independientes por consumidor.

Uso:
    bus = AudioBus(device_index=0)
    bus.subscribe("dtmf",    callback_fn)   # callback(chunk_f32, sr)
    bus.subscribe("precall", callback_fn)
    bus.start()
    ...
    bus.stop()
"""

from __future__ import annotations

import queue
import threading
from math import gcd
from typing import Callable

import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
except Exception:
    _SD_OK = False

from .config import TARGET_SR, MONITOR_VIZ_HZ


# Tipo del callback: recibe (chunk_float32, sample_rate_nativo)
AudioCallback = Callable[[np.ndarray, int], None]


class AudioBus(threading.Thread):
    """
    Abre un único sd.InputStream y distribuye los chunks a N consumidores.

    Cada consumidor recibe su propia cola de chunks (no comparten referencia)
    para evitar condiciones de carrera. Los chunks atrasados (cola llena)
    se descartan sin bloquear el hilo de captura.

    Args:
        device_index: Índice del dispositivo de entrada (None = predeterminado)
        blocksize_ms: Tamaño del bloque en ms (default 40ms)
        maxqueue:     Máximo de chunks en cola por consumidor antes de descartar
    """

    def __init__(
        self,
        device_index: int | None = None,
        blocksize_ms: float = 40.0,
        maxqueue: int = 50,
    ):
        super().__init__(daemon=True, name="AudioBus")
        self.device_index  = device_index
        self.blocksize_ms  = blocksize_ms
        self.maxqueue      = maxqueue

        self._stop_ev      = threading.Event()
        self._lock         = threading.Lock()
        # {nombre: (cola, callback)}
        self._consumers: dict[str, tuple[queue.Queue, AudioCallback]] = {}
        self._sr_native: int | None = None

    # ── API pública ────────────────────────────────────────────

    @property
    def sample_rate(self) -> int | None:
        """Sample rate nativo del dispositivo (disponible tras start())."""
        return self._sr_native

    def subscribe(self, name: str, callback: AudioCallback) -> None:
        """
        Registra un consumidor.

        El callback se llama en un hilo dedicado por consumidor,
        nunca en el hilo de captura (no bloquea el stream).

        Args:
            name:     Identificador único del consumidor
            callback: fn(chunk: np.ndarray, sr: int) → None
        """
        with self._lock:
            q = queue.Queue(maxsize=self.maxqueue)
            self._consumers[name] = (q, callback)
            # Arrancar hilo despachador para este consumidor
            t = threading.Thread(
                target=self._dispatch_loop,
                args=(name, q, callback),
                daemon=True,
                name=f"AudioBus-{name}",
            )
            t.start()

    def unsubscribe(self, name: str) -> None:
        """Elimina un consumidor."""
        with self._lock:
            self._consumers.pop(name, None)

    def stop(self) -> None:
        """Detiene el stream de captura y todos los despachadores."""
        self._stop_ev.set()

    # ── Hilo principal — captura ────────────────────────────────

    def run(self) -> None:
        if not _SD_OK:
            print("[AudioBus] sounddevice no disponible — bus detenido")
            return

        try:
            dev_info         = sd.query_devices(self.device_index, "input")
            self._sr_native  = int(dev_info["default_samplerate"])
            blocksize        = int(self._sr_native * self.blocksize_ms / 1000)
            dev_name         = dev_info["name"]
        except Exception as exc:
            print(f"[AudioBus] Dispositivo inválido (idx={self.device_index}): {exc}")
            return

        print(f"[AudioBus] Iniciado — [{dev_name}] @ {self._sr_native} Hz  "
              f"blocksize={blocksize} samples ({self.blocksize_ms:.0f}ms)")

        def _callback(indata: np.ndarray, frames: int, time_info, status):
            if self._stop_ev.is_set():
                raise sd.CallbackStop()

            chunk = indata[:, 0].astype(np.float32)

            with self._lock:
                consumers = list(self._consumers.values())

            for q, _ in consumers:
                try:
                    q.put_nowait(chunk.copy())
                except queue.Full:
                    pass  # descartar — el consumidor no puede seguir el ritmo

        try:
            with sd.InputStream(
                device    = self.device_index,
                channels  = 1,
                samplerate= self._sr_native,
                blocksize = blocksize,
                dtype     = "float32",
                callback  = _callback,
            ):
                self._stop_ev.wait()
        except Exception as exc:
            if not self._stop_ev.is_set():
                print(f"[AudioBus] Error en stream: {exc}")

        print("[AudioBus] Detenido")

    # ── Despachador por consumidor ──────────────────────────────

    def _dispatch_loop(
        self,
        name: str,
        q: queue.Queue,
        callback: AudioCallback,
    ) -> None:
        """Despacha chunks de la cola al callback del consumidor."""
        while not self._stop_ev.is_set():
            try:
                chunk = q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                callback(chunk, self._sr_native or TARGET_SR)
            except Exception as exc:
                print(f"[AudioBus-{name}] Error en callback: {exc}")

        # Vaciar la cola al salir
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break


# ──────────────────────────────────────────────────────────────
# Helper: RMS visualizador (uso frecuente en consumidores)
# ──────────────────────────────────────────────────────────────

class RmsAccumulator:
    """
    Acumula chunks y emite el RMS promediado a una tasa objetivo (Hz).

    Uso típico dentro de un callback de consumidor:
        acc = RmsAccumulator(target_hz=15.0, emit_fn=lambda rms: socketio.emit(...))
        def my_callback(chunk, sr):
            acc.feed(chunk, sr)
    """

    def __init__(self, target_hz: float = MONITOR_VIZ_HZ, emit_fn=None):
        self.target_hz = target_hz
        self.emit_fn   = emit_fn
        self._acc      = 0.0
        self._n        = 0
        self._trigger  = 0   # nº de chunks por emisión (se calcula en el 1er feed)
        self._sr_last  = None

    def feed(self, chunk: np.ndarray, sr: int) -> None:
        if self._sr_last != sr:
            self._sr_last  = sr
            # bloques de 40ms → ~25 fps; target_hz determina cada cuántos bloques emitir
            blocks_per_sec = sr / max(len(chunk), 1)
            self._trigger  = max(1, int(blocks_per_sec / self.target_hz))

        rms = float(np.sqrt(np.mean(chunk ** 2) + 1e-12))
        self._acc += rms
        self._n   += 1

        if self._n >= self._trigger:
            if self.emit_fn:
                self.emit_fn(self._acc / self._n)
            self._acc = 0.0
            self._n   = 0
