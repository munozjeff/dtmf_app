"""
dtmf_app/core/audio_player.py
──────────────────────────────────────────────────────────────────────
Motor de audio multi-dispositivo que reemplaza pygame.mixer.

A diferencia de pygame (single global mixer), AudioPlayer abre un
sd.OutputStream dedicado por dispositivo, por lo que N instancias
pueden reproducir simultáneamente en N interfaces de audio distintas.

Dependencias: sounddevice, soundfile (o wave para WAV puros)
"""

from __future__ import annotations
import os
import threading
import time
import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

try:
    import soundfile as sf
    _SF_OK = True
except ImportError:
    _SF_OK = False


def _read_audio(path: str) -> "tuple[np.ndarray, int] | tuple[None, None]":
    """
    Lee un archivo de audio y devuelve (data_float32_mono, samplerate).
    Intenta soundfile primero (soporta MP3, FLAC, WAV, OGG…).
    Fallback a wave estándar para WAV puros.
    Devuelve (None, None) si falla.
    """
    if not os.path.isfile(path):
        return None, None

    # ── soundfile (preferido) ─────────────────────────────────────
    if _SF_OK:
        try:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            mono = data[:, 0]  # solo canal izquierdo
            return mono.astype(np.float32), int(sr)
        except Exception:
            pass

    # ── wave (WAV puro) ───────────────────────────────────────────
    try:
        import wave as _wave, struct
        with _wave.open(path, "rb") as wf:
            sr     = wf.getframerate()
            n_ch   = wf.getnchannels()
            sw     = wf.getsampwidth()
            raw    = wf.readframes(wf.getnframes())
        fmt   = {1: "b", 2: "h", 4: "i"}.get(sw, "h")
        samps = struct.unpack(f"<{len(raw)//sw}{fmt}", raw)
        data  = np.array(samps[::n_ch], dtype=np.float32)
        data /= float(2 ** (8 * sw - 1))
        return data, int(sr)
    except Exception:
        pass

    return None, None


class AudioPlayer:
    """
    Reproduce un archivo de audio en un dispositivo de salida específico.

    Uso:
        player = AudioPlayer(output_device_idx=3)
        player.play("welcome.wav", cancel_event=my_event)   # bloqueante
        player.stop()   # desde otro hilo
    """

    def __init__(self, output_device_idx: "int | None" = None):
        self.output_device_idx = output_device_idx
        self._stop_ev   = threading.Event()
        self._play_lock = threading.Lock()
        self._stream: "sd.OutputStream | None" = None

    # ── API pública ───────────────────────────────────────────────

    def play(self, path: str,
             cancel_event: "threading.Event | None" = None,
             on_rms: "callable | None" = None) -> bool:
        """
        Reproduce el archivo de audio de forma BLOQUEANTE.

        Args:
            path:         Ruta al archivo de audio.
            cancel_event: Si se activa, la reproducción se detiene inmediatamente.
            on_rms:       Callback(rms: float) llamado ~15 Hz durante reproducción.

        Returns:
            True si se reprodujo completo, False si se canceló o hubo error.
        """
        if not _SD_OK:
            print(f"[AudioPlayer] sounddevice no disponible — omitiendo {os.path.basename(path)}")
            return False

        data, sr = _read_audio(path)
        if data is None:
            print(f"[AudioPlayer] No se pudo leer: {path}")
            return False

        self._stop_ev.clear()

        with self._play_lock:
            return self._stream_play(data, sr, cancel_event, on_rms, path)

    def stop(self):
        """Para la reproducción actual (hilo-safe)."""
        self._stop_ev.set()

    # ── Reproducción interna ──────────────────────────────────────

    def _stream_play(self, data: np.ndarray, sr: int,
                     cancel_event: "threading.Event | None",
                     on_rms: "callable | None",
                     path: str) -> bool:
        """
        Abre un OutputStream dedicado y escribe en bloques.
        La apertura puede fallar con WASAPI si el dispositivo está ocupado;
        se hace un retry con backoff de hasta 3 intentos.
        """
        BLOCK_FRAMES = max(1, sr // 15)   # ~67 ms → ~15 Hz callbacks
        pos          = [0]                # mutable para closure

        def _gone() -> bool:
            return (self._stop_ev.is_set()
                    or (cancel_event is not None and cancel_event.is_set()))

        def _callback(outdata: np.ndarray, frames: int, _t, _status):
            if _gone():
                outdata[:] = 0
                raise sd.CallbackStop()

            start = pos[0]
            end   = start + frames
            chunk = data[start:end]

            if len(chunk) < frames:
                # Fin del audio — rellenar con silencio
                padded        = np.zeros(frames, dtype=np.float32)
                padded[:len(chunk)] = chunk
                outdata[:, 0] = padded
                pos[0]        = len(data)   # señal de fin
                if on_rms:
                    rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) else 0.0
                    on_rms(rms)
                raise sd.CallbackStop()
            else:
                outdata[:, 0] = chunk
                pos[0]        = end
                if on_rms:
                    rms = float(np.sqrt(np.mean(chunk**2)))
                    on_rms(rms)

        # ── Intento de apertura con retry ─────────────────────────
        name = os.path.basename(path)
        max_retries, delay = 3, 0.25

        for attempt in range(1, max_retries + 1):
            if _gone():
                return False
            try:
                kwargs: dict = dict(
                    samplerate = sr,
                    channels   = 1,
                    dtype      = "float32",
                    blocksize  = BLOCK_FRAMES,
                    callback   = _callback,
                )
                if self.output_device_idx is not None:
                    kwargs["device"] = self.output_device_idx

                print(f"[AudioPlayer] ▶ {name} → device={self.output_device_idx}")
                with sd.OutputStream(**kwargs) as stream:
                    self._stream = stream
                    # Esperar fin de reproducción
                    while stream.active and not _gone():
                        time.sleep(0.03)

                self._stream = None
                completed = pos[0] >= len(data) and not _gone()
                if completed:
                    print(f"[AudioPlayer] ■ {name} — completo")
                else:
                    print(f"[AudioPlayer] ■ {name} — cancelado (pos={pos[0]}/{len(data)})")
                return completed

            except sd.CallbackStop:
                self._stream = None
                return not _gone()

            except Exception as exc:
                self._stream = None
                if self._stop_ev.is_set() or (cancel_event and cancel_event.is_set()):
                    return False
                print(f"[AudioPlayer] Intento {attempt}/{max_retries}: {exc}")
                if attempt < max_retries:
                    time.sleep(delay); delay *= 1.5
                else:
                    print(f"[AudioPlayer] ❌ No se pudo abrir stream para {name}: {exc}")
                    return False
        return False
