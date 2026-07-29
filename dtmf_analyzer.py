# -*- coding: utf-8 -*-
"""
dtmf_analyzer.py
================
Analizador de tonos DTMF (Dual-Tone Multi-Frequency) para telefonía.

Pipeline:
  1. Carga el audio (soporta .m4a, .wav, .mp3, .ogg, etc. via ffmpeg)
  2. Convierte a mono y re-samplea a 8 kHz (estándar telefónico)
  3. Filtra en banda 300–3400 Hz
  4. Limpia el ruido de fondo con noisereduce
  5. Amplifica la señal
  6. Aplica el algoritmo de Goertzel vectorizado (core/dtmf_engine.py)
  7. Mapea las frecuencias al dígito/símbolo marcado
  8. Genera un gráfico opcional con el espectrograma y los tonos detectados

v2: Motor DTMF y configuración importados desde dtmf_app/core/ (sin duplicación).
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess

import numpy as np
import soundfile as sf
import noisereduce as nr
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ── Core DTMF — fuente única de verdad ──────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "dtmf_app"))

from dtmf_app.core.config import (
    TARGET_SR, DTMF_MAP, ROW_FREQS, COL_FREQS,
    FRAME_MS, HOP_MS, MIN_TONE_MS, ENERGY_THRESHOLD, AMPLIFY_DB,
    DIGIT_COLORS,
)
from dtmf_app.core.dtmf_engine import (
    bandpass_filter,
    amplify,
    analyze_dtmf,
    resample_audio,
    build_chart,
)

# ─────────────────────────────────────────────
# Configurar path de ffmpeg (portable — sin hardcode al usuario Milton)
# ─────────────────────────────────────────────
def _find_ffmpeg() -> str | None:
    """Busca ffmpeg en PATH y luego en rutas conocidas de Windows."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    _candidates = [
        r"C:\Users\Milton\AppData\Local\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    ]
    for path in _candidates:
        if os.path.isfile(path):
            return path
    return None

_FFMPEG_EXE = _find_ffmpeg()
if _FFMPEG_EXE:
    _ffmpeg_dir = os.path.dirname(_FFMPEG_EXE)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    print(f"[INFO] ffmpeg: {_FFMPEG_EXE}")
else:
    print("[WARN] ffmpeg no encontrado — solo archivos WAV")


# ══════════════════════════════════════════════
#  CARGA DE AUDIO
# ══════════════════════════════════════════════

def load_audio(path: str) -> tuple[np.ndarray, int]:
    """
    Carga cualquier formato de audio soportado por ffmpeg y lo devuelve
    como array float32 normalizado en [-1, 1] a su sample rate original.
    """
    ext = os.path.splitext(path)[1].lower()
    print(f"[INFO] Cargando: {os.path.basename(path)}  ({ext})")

    # Convertir a WAV si no lo es (evita problemas de Unicode con soundfile en Windows)
    if ext != ".wav" and _FFMPEG_EXE:
        wav_path = os.path.splitext(path)[0] + "_converted.wav"
        result = subprocess.run(
            [_FFMPEG_EXE, "-y", "-i", path,
             "-ar", str(TARGET_SR), "-ac", "1", "-f", "wav", wav_path],
            capture_output=True, text=True, timeout=120
        )
        if os.path.isfile(wav_path):
            print(f"[INFO] Convertido a WAV: {os.path.basename(wav_path)}")
            path = wav_path
        else:
            print(f"[WARN] Conversión fallida: {result.stderr[-300:]}")

    audio, sr = sf.read(path, always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)

    duration = len(audio) / sr
    print(f"[INFO] Duración: {duration:.2f}s  |  SR: {sr} Hz  |  Muestras: {len(audio)}")
    return audio, sr


# ══════════════════════════════════════════════
#  REDUCCIÓN DE RUIDO (app-specific, no en core)
# ══════════════════════════════════════════════

def reduce_noise(audio: np.ndarray, sr: int) -> np.ndarray:
    """Reduce ruido estimando el perfil del primer 10% de la señal."""
    print("[INFO] Reduciendo ruido de fondo…")
    noise_len = max(int(0.1 * len(audio)), sr // 2)
    noise_clip = audio[:noise_len]
    return nr.reduce_noise(
        y=audio, y_noise=noise_clip, sr=sr,
        stationary=False, prop_decrease=0.85,
    ).astype(np.float32)


# ══════════════════════════════════════════════
#  VISUALIZACIÓN (standalone — guarda a disco)
# ══════════════════════════════════════════════

def plot_results(audio_clean: np.ndarray, sr: int, tones: list[dict], output_path: str):
    """
    Genera un gráfico con forma de onda + espectrograma y lo guarda en disco.
    Para uso standalone (python dtmf_analyzer.py archivo.m4a).
    La versión base64 para la API Flask viene de core.dtmf_engine.build_chart().
    """
    duration = len(audio_clean) / sr
    time_axis = np.linspace(0, duration, num=len(audio_clean))

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), facecolor="#0f1117")
    fig.suptitle("Análisis de Tonos DTMF", color="white", fontsize=16, fontweight="bold")

    colors = plt.cm.tab10(np.linspace(0, 1, 16))
    all_digits = list({t["digit"] for t in tones})
    digit_color = {d: colors[i % len(colors)] for i, d in enumerate(all_digits)}

    # ── Forma de onda ──
    ax1 = axes[0]
    ax1.set_facecolor("#1a1d27")
    ax1.plot(time_axis, audio_clean, color="#4fc3f7", linewidth=0.5, alpha=0.8)
    ax1.set_ylabel("Amplitud", color="white")
    ax1.set_title("Forma de onda (audio limpio y amplificado)", color="#aaaaaa", fontsize=10)
    ax1.tick_params(colors="white")
    ax1.set_xlim(0, duration)
    for spine in ax1.spines.values():
        spine.set_color("#333355")

    for tone in tones:
        col = digit_color.get(tone["digit"], "yellow")
        ax1.axvspan(tone["start_s"], tone["end_s"], alpha=0.35, color=col)
        mid = (tone["start_s"] + tone["end_s"]) / 2
        ax1.text(mid, ax1.get_ylim()[1] * 0.75, tone["digit"],
                 color="white", fontsize=11, fontweight="bold",
                 ha="center", va="center",
                 bbox=dict(boxstyle="round,pad=0.2", fc=col, alpha=0.8, ec="none"))

    # ── Espectrograma ──
    ax2 = axes[1]
    ax2.set_facecolor("#1a1d27")
    ax2.specgram(audio_clean, NFFT=512, Fs=sr, noverlap=400, cmap="inferno", vmin=-80, vmax=0)
    ax2.set_ylim(0, 4000)
    ax2.set_xlim(0, duration)
    ax2.set_ylabel("Frecuencia (Hz)", color="white")
    ax2.set_xlabel("Tiempo (s)", color="white")
    ax2.set_title("Espectrograma (0–4 kHz)", color="#aaaaaa", fontsize=10)
    ax2.tick_params(colors="white")
    for spine in ax2.spines.values():
        spine.set_color("#333355")

    for f in ROW_FREQS + COL_FREQS:
        ax2.axhline(y=f, color="#ffffff", linewidth=0.4, linestyle="--", alpha=0.4)
        ax2.text(0.01, f + 15, f"{f} Hz", color="#cccccc", fontsize=6, alpha=0.7,
                 transform=ax2.get_yaxis_transform())

    for tone in tones:
        col = digit_color.get(tone["digit"], "yellow")
        ax2.axvspan(tone["start_s"], tone["end_s"], alpha=0.25, color=col)

    patches = [mpatches.Patch(color=digit_color[d], label=f'Dígito "{d}"') for d in all_digits]
    if patches:
        ax2.legend(handles=patches, loc="upper right",
                   facecolor="#1a1d27", edgecolor="#555577",
                   labelcolor="white", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    print(f"[INFO] Gráfico guardado: {output_path}")
    plt.show()


# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════

def main(audio_path: str, plot: bool = True):
    print("=" * 60)
    print("  ANALIZADOR DE TONOS DTMF — Telefonía  (v2 core)")
    print("=" * 60)

    if not os.path.isfile(audio_path):
        print(f"[ERROR] Archivo no encontrado: {audio_path}")
        sys.exit(1)

    # 1. Cargar
    audio, sr = load_audio(audio_path)

    # 2. Re-muestrear a 8 kHz
    audio = resample_audio(audio, sr, TARGET_SR)
    sr    = TARGET_SR

    # 3. Filtro pasa-banda
    audio = bandpass_filter(audio, sr)
    print("[INFO] Filtro pasa-banda 300–3400 Hz aplicado")

    # 4. Reducción de ruido
    audio = reduce_noise(audio, sr)

    # 5. Amplificación
    audio = amplify(audio, AMPLIFY_DB)
    print(f"[INFO] Amplificación +{AMPLIFY_DB} dB aplicada")

    # 6. Análisis DTMF
    tones = analyze_dtmf(audio, sr)

    # 7. Resultados
    print()
    print("=" * 60)
    print("  RESULTADOS — Tonos DTMF Detectados")
    print("=" * 60)
    if not tones:
        print("[RESULTADO] No se detectaron tonos DTMF en el audio.")
    else:
        sequence = ""
        for i, tone in enumerate(tones, 1):
            print(f"  Tono #{i:02d}  →  [{tone['digit']}]  "
                  f"| Inicio: {tone['start_s']:.3f}s  "
                  f"| Fin: {tone['end_s']:.3f}s  "
                  f"| Duración: {tone['duration_ms']}ms")
            sequence += tone["digit"]
        print()
        print(f"  ╔══════════════════════════════╗")
        print(f"  ║  Secuencia marcada: {sequence:<9} ║")
        print(f"  ╚══════════════════════════════╝")

    # 8. Gráfico
    if plot and tones:
        output_img = os.path.splitext(audio_path)[0] + "_dtmf_analysis.png"
        plot_results(audio, sr, tones, output_img)

    return tones


if __name__ == "__main__":
    _script_dir = os.path.dirname(os.path.abspath(__file__))

    # Buscar archivo de prueba por defecto
    _default_audio = None
    for fname in ["grabacion5.m4a", "grabacion7.wav", "audio_raw.wav",
                  "Grabacion7.m4a", "Grabación22.m4a"]:
        candidate = os.path.join(_script_dir, fname)
        if os.path.isfile(candidate):
            _default_audio = candidate
            break

    if _default_audio is None:
        print("[ERROR] No se encontró ningún archivo de audio de prueba en el directorio.")
        sys.exit(1)

    audio_file = sys.argv[1] if len(sys.argv) > 1 else _default_audio
    main(audio_file, plot=True)
