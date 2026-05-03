"""
dtmf_analyzer.py
================
Analizador de tonos DTMF (Dual-Tone Multi-Frequency) para telefonía.

Pipeline:
  1. Carga el audio (soporta .m4a, .wav, .mp3, .ogg, etc.)
  2. Convierte a mono y re-samplea a 8 kHz (estándar telefónico)
  3. Limpia el ruido de fondo con noisereduce
  4. Amplifica la señal
  5. Detecta segmentos con tonos (VAD simple por energía)
  6. Aplica el algoritmo de Goertzel para identificar las frecuencias DTMF
  7. Mapea las frecuencias al dígito/símbolo marcado
  8. Genera un gráfico opcional con el espectrograma y los tonos detectados
"""

import os
import sys
import numpy as np
import soundfile as sf
import noisereduce as nr
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy.signal import butter, sosfilt, resample_poly
from pydub import AudioSegment
from pydub import utils as pydub_utils
import librosa

# ─────────────────────────────────────────────
# Configurar path de ffmpeg explícitamente
# (evita dependencia del PATH del shell)
# ─────────────────────────────────────────────
_FFMPEG_CANDIDATES = [
    r"C:\Users\Milton\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]
for _candidate in _FFMPEG_CANDIDATES:
    if os.path.isfile(_candidate):
        _ffmpeg_dir = os.path.dirname(_candidate)
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
        AudioSegment.converter  = _candidate
        AudioSegment.ffmpeg     = _candidate
        AudioSegment.ffprobe    = _candidate.replace("ffmpeg.exe", "ffprobe.exe")
        print(f"[INFO] ffmpeg encontrado en: {_candidate}")
        break

# ─────────────────────────────────────────────
# Tabla DTMF estándar ITU-T Q.23
# Cada tecla genera la suma de una frecuencia de fila y una de columna
# ─────────────────────────────────────────────
DTMF_FREQS = {
    # (fila, columna)
    (697, 1209): "1",
    (697, 1336): "2",
    (697, 1477): "3",
    (697, 1633): "A",
    (770, 1209): "4",
    (770, 1336): "5",
    (770, 1477): "6",
    (770, 1633): "B",
    (852, 1209): "7",
    (852, 1336): "8",
    (852, 1477): "9",
    (852, 1633): "C",
    (941, 1209): "*",
    (941, 1336): "0",
    (941, 1477): "#",
    (941, 1633): "D",
}

ROW_FREQS = [697, 770, 852, 941]       # Hz — frecuencias de fila
COL_FREQS = [1209, 1336, 1477, 1633]   # Hz — frecuencias de columna

# ─────────────────────────────────────────────
# Parámetros configurables
# ─────────────────────────────────────────────
TARGET_SR        = 8000    # Hz  -- frecuencia de muestreo objetivo
FRAME_MS         = 40      # ms  -- frames de 40ms -> buena resolucion frecuencial
HOP_MS           = 10      # ms  -- desplazamiento entre frames
MIN_TONE_MS      = 20      # ms  -- minimo 20ms (2 frames); tonos reales ~170-340ms
ENERGY_THRESHOLD = 5e-7    # umbral de silencio absoluto
AMPLIFY_DB       = 30      # dB  -- ganancia de amplificacion

# Calibrados con Grabacion7.wav (audio con los 10 digitos reales):
#   row_dom: 0.97-0.999  -> umbral 0.78  |  col_dom: 0.99-0.999  -> umbral 0.50
#   dom_total: 0.99-0.999 -> umbral 0.82
ROW_DOM_THRESHOLD   = 0.78
COL_DOM_THRESHOLD   = 0.50
TOTAL_DOM_THRESHOLD = 0.82

# Concentracion espectral DTMF -- discriminador principal de voz
# 2*(P_fila + P_col) / energia_frame:
#   DTMF real  -> 0.54 - 0.86  (casi toda la energia en 2 frecuencias)
#   Voz/ruido  -> < 0.10       (energia dispersa en el espectro)
CONCENTRATION_THRESHOLD = 0.20   # conservador: cubre tonos con algo de ruido


# ══════════════════════════════════════════════
#  1. CARGA DE AUDIO
# ══════════════════════════════════════════════
def load_audio(path: str) -> tuple[np.ndarray, int]:
    """
    Carga cualquier formato de audio compatible con pydub/ffmpeg
    y lo devuelve como array float32 normalizado en [-1, 1].
    """
    ext = os.path.splitext(path)[1].lower()
    print(f"[INFO] Cargando archivo: {os.path.basename(path)}  (formato: {ext})")

    # Intentamos pydub primero (soporta .m4a, .aac, .mp3, etc. via ffmpeg)
    # Si ffmpeg no está disponible, usamos librosa como fallback universal
    if ext in (".m4a", ".aac", ".mp3", ".ogg", ".opus"):
        try:
            audio_seg = AudioSegment.from_file(path)
            audio_seg = audio_seg.set_channels(1)         # mono
            raw = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
            sr  = audio_seg.frame_rate
            max_val = float(2 ** (audio_seg.sample_width * 8 - 1))
            audio   = raw / max_val
            print("[INFO] Audio cargado con pydub/ffmpeg")
        except Exception as e:
            print(f"[WARN] pydub falló ({e}), intentando con librosa…")
            audio, sr = librosa.load(path, sr=None, mono=True)
            print("[INFO] Audio cargado con librosa")
    else:
        try:
            audio, sr = sf.read(path, always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            audio = audio.astype(np.float32)
        except Exception:
            audio, sr = librosa.load(path, sr=None, mono=True)

    duration = len(audio) / sr
    print(f"[INFO] Duración: {duration:.2f} s  |  Sample rate original: {sr} Hz  |  Muestras: {len(audio)}")
    return audio, sr


# ══════════════════════════════════════════════
#  2. RE-MUESTREO
# ══════════════════════════════════════════════
def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    from math import gcd
    g      = gcd(target_sr, orig_sr)
    up     = target_sr // g
    down   = orig_sr   // g
    audio  = resample_poly(audio, up, down)
    print(f"[INFO] Re-muestreo: {orig_sr} Hz -> {target_sr} Hz")
    return audio.astype(np.float32)


# ══════════════════════════════════════════════
#  3. LIMPIEZA DE RUIDO
# ══════════════════════════════════════════════
def reduce_noise(audio: np.ndarray, sr: int) -> np.ndarray:
    print("[INFO] Reduciendo ruido de fondo…")
    # Estimamos el perfil de ruido con el primer 10 % de la señal (asumiendo silencio inicial)
    noise_clip_len = max(int(0.1 * len(audio)), sr // 2)   # al menos 0.5 s
    noise_clip     = audio[:noise_clip_len]

    cleaned = nr.reduce_noise(
        y              = audio,
        y_noise        = noise_clip,
        sr             = sr,
        stationary     = False,
        prop_decrease  = 0.85,
    )
    return cleaned.astype(np.float32)


# ══════════════════════════════════════════════
#  4. AMPLIFICACIÓN
# ══════════════════════════════════════════════
def amplify(audio: np.ndarray, gain_db: float) -> np.ndarray:
    print(f"[INFO] Amplificando señal +{gain_db} dB…")
    gain   = 10 ** (gain_db / 20.0)
    audio  = audio * gain
    # Evitar clipping
    peak   = np.max(np.abs(audio))
    if peak > 1.0:
        audio = audio / peak
        print(f"[WARN] Señal recortada para evitar clipping (pico={peak:.2f})")
    return audio


# ══════════════════════════════════════════════
#  5. FILTRO DE PASO DE BANDA (300–3400 Hz — rango telefónico)
# ══════════════════════════════════════════════
def bandpass_filter(audio: np.ndarray, sr: int,
                    low_hz: float = 300.0, high_hz: float = 3400.0) -> np.ndarray:
    nyq   = sr / 2.0
    sos   = butter(N=4, Wn=[low_hz / nyq, high_hz / nyq], btype="band", output="sos")
    return sosfilt(sos, audio).astype(np.float32)


# ══════════════════════════════════════════════
#  6. ALGORITMO DE GOERTZEL
# ══════════════════════════════════════════════
def goertzel_power(samples: np.ndarray, target_freq: float, sr: int) -> float:
    """
    Calcula la potencia espectral en una frecuencia objetivo usando el
    algoritmo de Goertzel — eficiente para detectar tonos individuales.
    """
    N      = len(samples)
    k      = int(0.5 + N * target_freq / sr)
    omega  = 2.0 * np.pi * k / N
    coeff  = 2.0 * np.cos(omega)
    s_prev = 0.0
    s_prev2= 0.0
    for x in samples:
        s = x + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev  = s
    power = s_prev2 ** 2 + s_prev ** 2 - coeff * s_prev * s_prev2
    return power / (N * N)   # normalizado


# ══════════════════════════════════════════════
#  7. DETECCIÓN DE DTMF EN UN FRAME
# ══════════════════════════════════════════════
def detect_dtmf_frame(samples: np.ndarray, sr: int, frame_energy: float) -> str | None:
    """
    Detecta un digito DTMF en un frame usando tres filtros:
      1. Dominancia espectral dentro de cada grupo (fila / columna)
      2. Dominio total del par DTMF sobre el pool de 8 frecuencias
      3. Concentracion espectral DTMF -- discriminador principal de voz:
         2*(P_fila + P_col) / energia_frame >= CONCENTRATION_THRESHOLD
         DTMF real: ~0.54-0.86  |  Voz: tipicamente < 0.10
    """
    row_powers = {f: goertzel_power(samples, f, sr) for f in ROW_FREQS}
    col_powers = {f: goertzel_power(samples, f, sr) for f in COL_FREQS}

    best_row = max(row_powers, key=row_powers.get)
    best_col = max(col_powers, key=col_powers.get)

    total_power = sum(row_powers.values()) + sum(col_powers.values())
    if total_power < 1e-10:
        return None

    # Filtro 1 -- Dominancia dentro de cada grupo
    row_dominance = row_powers[best_row] / (sum(row_powers.values()) + 1e-12)
    col_dominance = col_powers[best_col] / (sum(col_powers.values()) + 1e-12)
    if row_dominance < ROW_DOM_THRESHOLD or col_dominance < COL_DOM_THRESHOLD:
        return None

    # Filtro 2 -- Dominio total del par DTMF
    dominant_power = row_powers[best_row] + col_powers[best_col]
    if dominant_power / (total_power + 1e-12) < TOTAL_DOM_THRESHOLD:
        return None

    # Filtro 3 -- Concentracion espectral DTMF
    concentration = 2.0 * dominant_power / (frame_energy + 1e-14)
    if concentration < CONCENTRATION_THRESHOLD:
        return None

    return DTMF_FREQS.get((best_row, best_col))


# ══════════════════════════════════════════════
#  8. ANÁLISIS COMPLETO — SEGMENTACIÓN POR FRAMES
# ══════════════════════════════════════════════
def analyze_dtmf(audio: np.ndarray, sr: int) -> list[dict]:
    """
    Analiza el audio frame a frame. El filtrado de voz se hace
    dentro de detect_dtmf_frame() via concentracion espectral.
    """
    frame_size   = int(sr * FRAME_MS  / 1000)
    hop_size     = int(sr * HOP_MS    / 1000)
    min_frames   = max(1, int(MIN_TONE_MS / HOP_MS))
    total_frames = (len(audio) - frame_size) // hop_size + 1

    print(f"[INFO] Analizando {total_frames} frames de {FRAME_MS} ms (hop={HOP_MS} ms)...")

    frame_results = []
    for i in range(total_frames):
        start  = i * hop_size
        frame  = audio[start:start + frame_size]
        energy = float(np.mean(frame ** 2))
        t      = i * HOP_MS / 1000.0

        if energy < ENERGY_THRESHOLD:
            frame_results.append((t, None))
            continue

        digit = detect_dtmf_frame(frame, sr, energy)
        frame_results.append((t, digit))

    # Agrupar frames consecutivos con el mismo digito
    tones = []
    current_digit = None
    current_start = 0.0
    consecutive   = 0

    for t, digit in frame_results:
        if digit == current_digit and digit is not None:
            consecutive += 1
        else:
            if current_digit is not None and consecutive >= min_frames:
                tones.append({
                    "digit"      : current_digit,
                    "start_s"    : round(current_start, 3),
                    "end_s"      : round(t, 3),
                    "duration_ms": round((t - current_start) * 1000),
                })
            current_digit = digit
            current_start = t
            consecutive   = 1 if digit else 0

    if current_digit is not None and consecutive >= min_frames:
        t_last = frame_results[-1][0]
        tones.append({
            "digit"      : current_digit,
            "start_s"    : round(current_start, 3),
            "end_s"      : round(t_last, 3),
            "duration_ms": round((t_last - current_start) * 1000),
        })

    return tones


# ══════════════════════════════════════════════
#  9. VISUALIZACIÓN
# ══════════════════════════════════════════════
def plot_results(audio_clean: np.ndarray, sr: int, tones: list[dict], output_path: str):
    """
    Genera un gráfico con:
      - Forma de onda del audio limpio
      - Espectrograma
      - Marcadores de tonos DTMF detectados
    """
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), facecolor="#0f1117")
    fig.suptitle("Análisis de Tonos DTMF", color="white", fontsize=16, fontweight="bold")

    time_axis = np.linspace(0, len(audio_clean) / sr, num=len(audio_clean))
    colors     = plt.cm.tab10(np.linspace(0, 1, 16))
    digit_color = {}
    all_digits  = list(set(t["digit"] for t in tones))
    for i, d in enumerate(all_digits):
        digit_color[d] = colors[i % len(colors)]

    # ── Forma de onda ──
    ax1 = axes[0]
    ax1.set_facecolor("#1a1d27")
    ax1.plot(time_axis, audio_clean, color="#4fc3f7", linewidth=0.5, alpha=0.8)
    ax1.set_ylabel("Amplitud", color="white")
    ax1.set_title("Forma de onda (audio limpio y amplificado)", color="#aaaaaa", fontsize=10)
    ax1.tick_params(colors="white")
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
    Pxx, freqs, t_spec, im = ax2.specgram(
        audio_clean, NFFT=512, Fs=sr, noverlap=400,
        cmap="inferno", vmin=-80, vmax=0
    )
    ax2.set_ylim(0, 4000)
    ax2.set_ylabel("Frecuencia (Hz)", color="white")
    ax2.set_xlabel("Tiempo (s)", color="white")
    ax2.set_title("Espectrograma (0–4 kHz)", color="#aaaaaa", fontsize=10)
    ax2.tick_params(colors="white")
    for spine in ax2.spines.values():
        spine.set_color("#333355")

    # Líneas de referencia para las frecuencias DTMF
    for f in ROW_FREQS + COL_FREQS:
        ax2.axhline(y=f, color="#ffffff", linewidth=0.4, linestyle="--", alpha=0.4)
        ax2.text(0.01, f + 15, f"{f} Hz", color="#cccccc", fontsize=6, alpha=0.7,
                 transform=ax2.get_yaxis_transform())

    for tone in tones:
        col = digit_color.get(tone["digit"], "yellow")
        ax2.axvspan(tone["start_s"], tone["end_s"], alpha=0.25, color=col)

    # Leyenda
    patches = [mpatches.Patch(color=digit_color[d], label=f'Dígito "{d}"') for d in all_digits]
    if patches:
        ax2.legend(handles=patches, loc="upper right",
                   facecolor="#1a1d27", edgecolor="#555577",
                   labelcolor="white", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    print(f"[INFO] Gráfico guardado en: {output_path}")
    plt.show()


# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
def main(audio_path: str, plot: bool = True):
    print("=" * 60)
    print("  ANALIZADOR DE TONOS DTMF -- Telefonia")
    print("=" * 60)

    if not os.path.isfile(audio_path):
        print(f"[ERROR] No se encontro el archivo: {audio_path}")
        sys.exit(1)

    ext = os.path.splitext(audio_path)[1].lower()

    # Si el archivo no es WAV, convertir con ffmpeg primero (evita problemas de
    # rutas Unicode con pydub/soundfile en Windows)
    if ext != ".wav":
        ffmpeg_exe = None
        for c in _FFMPEG_CANDIDATES:
            if os.path.isfile(c):
                ffmpeg_exe = c
                break
        if ffmpeg_exe:
            wav_path = os.path.splitext(audio_path)[0] + "_converted.wav"
            import subprocess
            result = subprocess.run(
                [ffmpeg_exe, "-i", audio_path, "-ar", str(TARGET_SR),
                 "-ac", "1", "-f", "wav", wav_path, "-y"],
                capture_output=True, text=True
            )
            if os.path.isfile(wav_path):
                print(f"[INFO] Convertido a WAV: {wav_path}")
                audio_path = wav_path
            else:
                print(f"[WARN] Conversion fallida, intentando carga directa")

    # 1. Cargar
    audio, sr = load_audio(audio_path)

    # 2. Re-muestrear a 8 kHz
    audio = resample_audio(audio, sr, TARGET_SR)
    sr    = TARGET_SR

    # 3. Filtro de paso de banda (rango telefónico)
    audio = bandpass_filter(audio, sr, low_hz=300, high_hz=3400)
    print("[INFO] Filtro paso de banda aplicado (300–3400 Hz)")

    # 4. Reducción de ruido
    audio = reduce_noise(audio, sr)

    # 5. Amplificación
    audio = amplify(audio, AMPLIFY_DB)

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
            print(f"  Tono #{i:02d}  →  Dígito: [{tone['digit']}]  "
                  f"| Inicio: {tone['start_s']:.3f} s  "
                  f"| Fin: {tone['end_s']:.3f} s  "
                  f"| Duración: {tone['duration_ms']} ms")
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
    # Ruta por defecto: el audio renombrado o el original
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    # Buscar cualquier m4a/wav en el directorio
    for fname in ["grabacion5.m4a", "audio_raw.wav", "Grabacion (5).m4a"]:
        candidate = os.path.join(script_dir, fname)
        if os.path.isfile(candidate):
            default_audio = candidate
            break
    else:
        # fallback
        default_audio = os.path.join(script_dir, "grabacion5.m4a")

    audio_file = sys.argv[1] if len(sys.argv) > 1 else default_audio
    main(audio_file, plot=True)
