# -*- coding: utf-8 -*-
"""
DTMF Analyzer - Flask Backend
==============================
API REST que recibe un archivo de audio, lo procesa con el pipeline
DTMF (filtrado, reduccion de ruido, amplificacion, Goertzel) y devuelve
los tonos detectados junto con una imagen del espectrograma.
"""

import os
import sys
import uuid
import json
import base64
import subprocess
import tempfile
import traceback
from io import BytesIO
from math import gcd

import numpy as np
import soundfile as sf
import noisereduce as nr
import matplotlib
matplotlib.use("Agg")   # sin GUI - render a buffer
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy.signal import butter, sosfilt, resample_poly
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ──────────────────────────────────────────────────────────────
# Configuracion de ffmpeg
# ──────────────────────────────────────────────────────────────
_FFMPEG_CANDIDATES = [
    r"C:\Users\Milton\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",   # si ya esta en el PATH del sistema
]

FFMPEG_EXE = None
for _c in _FFMPEG_CANDIDATES:
    try:
        result = subprocess.run([_c, "-version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            FFMPEG_EXE = _c
            break
    except Exception:
        continue

if FFMPEG_EXE:
    _bin = os.path.dirname(FFMPEG_EXE)
    os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
    print(f"[OK] ffmpeg: {FFMPEG_EXE}")
else:
    print("[WARN] ffmpeg no encontrado - solo se aceptaran archivos WAV")

# ──────────────────────────────────────────────────────────────
# Tabla DTMF ITU-T Q.23
# ──────────────────────────────────────────────────────────────
DTMF_MAP = {
    (697, 1209): "1", (697, 1336): "2", (697, 1477): "3", (697, 1633): "A",
    (770, 1209): "4", (770, 1336): "5", (770, 1477): "6", (770, 1633): "B",
    (852, 1209): "7", (852, 1336): "8", (852, 1477): "9", (852, 1633): "C",
    (941, 1209): "*", (941, 1336): "0", (941, 1477): "#", (941, 1633): "D",
}
ROW_FREQS = [697, 770, 852, 941]
COL_FREQS = [1209, 1336, 1477, 1633]

# ──────────────────────────────────────────────────────────────
# Parametros del pipeline
# ──────────────────────────────────────────────────────────────
TARGET_SR        = 8000
FRAME_MS         = 40      # frames de 40 ms -> buena resolucion frecuencial
HOP_MS           = 10
MIN_TONE_MS      = 20      # minimo 20 ms (2 frames)
ENERGY_THRESHOLD = 5e-7    # umbral de silencio absoluto
AMPLIFY_DB       = 30

# ---- Umbrales de dominancia espectral ----
# Calibrados con Grabacion7.wav (audio con los 10 digitos reales):
#   row_dom en tonos reales: 0.97 - 0.999  -> umbral 0.78 (amplio margen)
#   col_dom en tonos reales: 0.99 - 0.999  -> umbral 0.50
#   dom_total en reales:     0.99 - 0.999  -> umbral 0.82
ROW_DOM_THRESHOLD   = 0.78
COL_DOM_THRESHOLD   = 0.50
TOTAL_DOM_THRESHOLD = 0.82

# ---- Concentracion espectral DTMF (discriminador principal de voz) ----
# Para un tono DTMF puro:
#   2 * (P_fila + P_col) / energia_frame  ~= 0.54 - 0.86  (medido en Grabacion7)
# Para voz: energia distribuida en cientos de frecuencias -> concentracion < 0.10
# No necesita suprimir la voz: mide la pureza espectral objetivamente.
CONCENTRATION_THRESHOLD = 0.20   # conservador: cubre tonos con algo de ruido

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "wav", "mp3", "m4a", "aac", "ogg", "opus",
    "flac", "wma", "mp4", "webm", "3gp", "amr"
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024   # 100 MB max


# ══════════════════════════════════════════════
#  PIPELINE DE AUDIO
# ══════════════════════════════════════════════

def convert_to_wav(src_path: str) -> str:
    """Convierte cualquier formato a WAV 8kHz mono usando ffmpeg."""
    if not FFMPEG_EXE:
        raise RuntimeError("ffmpeg no disponible para convertir este formato.")
    dst_path = src_path + "_conv.wav"
    result = subprocess.run(
        [FFMPEG_EXE, "-y", "-i", src_path,
         "-ar", str(TARGET_SR), "-ac", "1", "-f", "wav", dst_path],
        capture_output=True, text=True, timeout=120
    )
    if not os.path.isfile(dst_path):
        raise RuntimeError(f"ffmpeg fallo: {result.stderr[-500:]}")
    return dst_path


def load_audio(path: str):
    """Carga el audio como float32 normalizado en [-1,1]."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext != "wav":
        path = convert_to_wav(path)

    audio, sr = sf.read(path, always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)

    # Resamplear si no esta en TARGET_SR
    if sr != TARGET_SR:
        g = gcd(TARGET_SR, sr)
        audio = resample_poly(audio, TARGET_SR // g, sr // g).astype(np.float32)
        sr = TARGET_SR

    return audio, sr, path   # path puede haber cambiado si se convirtio


def bandpass_filter(audio: np.ndarray, sr: int) -> np.ndarray:
    nyq = sr / 2.0
    sos = butter(4, [300 / nyq, 3400 / nyq], btype="band", output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def reduce_noise_audio(audio: np.ndarray, sr: int) -> np.ndarray:
    noise_len = max(int(0.1 * len(audio)), sr // 2)
    noise_clip = audio[:noise_len]
    return nr.reduce_noise(
        y=audio, y_noise=noise_clip, sr=sr,
        stationary=False, prop_decrease=0.85
    ).astype(np.float32)


def amplify(audio: np.ndarray, gain_db: float) -> np.ndarray:
    gain = 10 ** (gain_db / 20.0)
    audio = audio * gain
    peak = np.max(np.abs(audio))
    if peak > 1.0:
        audio = audio / peak
    return audio


def goertzel(samples: np.ndarray, freq: float, sr: int) -> float:
    N = len(samples)
    k = int(0.5 + N * freq / sr)
    omega = 2.0 * np.pi * k / N
    coeff = 2.0 * np.cos(omega)
    s1 = s2 = 0.0
    for x in samples:
        s = x + coeff * s1 - s2
        s2 = s1
        s1 = s
    return (s2 ** 2 + s1 ** 2 - coeff * s1 * s2) / (N * N)


def detect_dtmf_frame(samples: np.ndarray, sr: int, frame_energy: float):
    """
    Detecta un digito DTMF usando tres filtros en cascada:

    1. Dominancia espectral (row / col / total)
       El digito DTMF debe dominar dentro de su grupo de frecuencias.

    2. Concentracion espectral DTMF  <-- discriminador principal de voz
       2*(P_fila + P_col) / energia_frame >= CONCENTRATION_THRESHOLD
       Para DTMF real: ~0.54-0.86  |  Para voz: tipicamente < 0.10
       No necesita suprimir la voz: mide la pureza espectral directamente.

    3. Duracion minima aplicada en analyze_dtmf() via MIN_TONE_MS.
    """
    row_p = {f: goertzel(samples, f, sr) for f in ROW_FREQS}
    col_p = {f: goertzel(samples, f, sr) for f in COL_FREQS}
    total = sum(row_p.values()) + sum(col_p.values())
    if total < 1e-14:
        return None

    br = max(row_p, key=row_p.get)
    bc = max(col_p, key=col_p.get)

    # Filtro 1 -- Dominancia dentro de cada grupo de frecuencias
    row_dom   = row_p[br] / (sum(row_p.values()) + 1e-14)
    col_dom   = col_p[bc] / (sum(col_p.values()) + 1e-14)
    if row_dom < ROW_DOM_THRESHOLD or col_dom < COL_DOM_THRESHOLD:
        return None

    # Filtro 2 -- Dominio total (ambas frecuencias dominan el pool DTMF)
    dom_total = (row_p[br] + col_p[bc]) / (total + 1e-14)
    if dom_total < TOTAL_DOM_THRESHOLD:
        return None

    # Filtro 3 -- Concentracion espectral DTMF
    # 2*(P_row + P_col) / frame_energy:
    #   DTMF puro  -> 0.54 - 0.86  (casi toda la energia en 2 frecuencias)
    #   Voz/ruido  -> < 0.10       (energia dispersa en todo el espectro)
    concentration = 2.0 * (row_p[br] + col_p[bc]) / (frame_energy + 1e-14)
    if concentration < CONCENTRATION_THRESHOLD:
        return None

    return DTMF_MAP.get((br, bc))


def analyze_dtmf(audio: np.ndarray, sr: int):
    """
    Analiza el audio frame a frame y devuelve la lista de tonos DTMF.
    El filtrado de voz se realiza dentro de detect_dtmf_frame() via
    concentracion espectral, no por supresion de energia.
    """
    frame_size   = int(sr * FRAME_MS / 1000)
    hop_size     = int(sr * HOP_MS  / 1000)
    min_frames   = max(1, int(MIN_TONE_MS / HOP_MS))
    total_frames = (len(audio) - frame_size) // hop_size + 1

    frame_log = []
    for i in range(total_frames):
        start  = i * hop_size
        frame  = audio[start: start + frame_size]
        energy = float(np.mean(frame ** 2))
        t      = round(i * HOP_MS / 1000.0, 3)

        if energy < ENERGY_THRESHOLD:
            frame_log.append((t, None))
            continue

        # Pasar la energia del frame al detector para el calculo de concentracion
        digit = detect_dtmf_frame(frame, sr, energy)
        frame_log.append((t, digit))

    # Agrupar frames consecutivos con el mismo digito
    tones = []
    cur_digit = None
    cur_start = 0.0
    consec    = 0

    for t, digit in frame_log:
        if digit is not None and digit == cur_digit:
            consec += 1
        else:
            if cur_digit is not None and consec >= min_frames:
                tones.append({
                    "digit"      : cur_digit,
                    "start_s"    : round(cur_start, 3),
                    "end_s"      : round(t, 3),
                    "duration_ms": round((t - cur_start) * 1000),
                })
            cur_digit = digit
            cur_start = t
            consec    = 1 if digit else 0

    if cur_digit is not None and consec >= min_frames and frame_log:
        t_last = frame_log[-1][0]
        tones.append({
            "digit"      : cur_digit,
            "start_s"    : round(cur_start, 3),
            "end_s"      : round(t_last, 3),
            "duration_ms": round((t_last - cur_start) * 1000),
        })

    return tones


# ══════════════════════════════════════════════
#  GENERACION DE GRAFICO
# ══════════════════════════════════════════════

# Colores por digito DTMF
DIGIT_COLORS = {
    "1":"#4fc3f7","2":"#81c784","3":"#ffb74d","4":"#ba68c8",
    "5":"#f06292","6":"#4dd0e1","7":"#aed581","8":"#ff8a65",
    "9":"#90caf9","0":"#a5d6a7","*":"#ffe082","#":"#ef9a9a",
    "A":"#b39ddb","B":"#80cbc4","C":"#ffcc02","D":"#ff7043",
}

def build_chart(audio: np.ndarray, sr: int, tones: list, duration: float) -> str:
    """Genera el grafico y lo devuelve como base64 PNG."""
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), facecolor="#0d1117")
    fig.suptitle("Analisis de Tonos DTMF", color="white",
                 fontsize=17, fontweight="bold", y=0.98)

    time_axis = np.linspace(0, len(audio) / sr, num=len(audio))

    # ── Forma de onda ──
    ax1 = axes[0]
    ax1.set_facecolor("#161b22")
    ax1.plot(time_axis, audio, color="#4fc3f7", linewidth=0.4, alpha=0.85)
    ax1.set_ylabel("Amplitud", color="#8b949e", fontsize=9)
    ax1.set_title("Forma de onda (audio limpio + amplificado)",
                  color="#8b949e", fontsize=9)
    ax1.tick_params(colors="#8b949e", labelsize=8)
    ax1.set_xlim(0, duration)
    for sp in ax1.spines.values():
        sp.set_color("#30363d")

    y_max = max(np.max(np.abs(audio)) * 1.1, 0.01)
    ax1.set_ylim(-y_max, y_max)

    for tone in tones:
        col = DIGIT_COLORS.get(tone["digit"], "#ffffff")
        ax1.axvspan(tone["start_s"], tone["end_s"], alpha=0.30, color=col, zorder=2)
        mid = (tone["start_s"] + tone["end_s"]) / 2
        ax1.text(mid, y_max * 0.65, tone["digit"],
                 color="white", fontsize=10, fontweight="bold",
                 ha="center", va="center", zorder=3,
                 bbox=dict(boxstyle="round,pad=0.25", fc=col, alpha=0.9, ec="none"))

    # ── Espectrograma ──
    ax2 = axes[1]
    ax2.set_facecolor("#161b22")
    try:
        ax2.specgram(audio, NFFT=512, Fs=sr, noverlap=400,
                     cmap="inferno", vmin=-80, vmax=0)
    except Exception:
        pass
    ax2.set_ylim(0, 4000)
    ax2.set_xlim(0, duration)
    ax2.set_ylabel("Frecuencia (Hz)", color="#8b949e", fontsize=9)
    ax2.set_xlabel("Tiempo (s)", color="#8b949e", fontsize=9)
    ax2.set_title("Espectrograma 0-4 kHz  (lineas = frecuencias DTMF)",
                  color="#8b949e", fontsize=9)
    ax2.tick_params(colors="#8b949e", labelsize=8)
    for sp in ax2.spines.values():
        sp.set_color("#30363d")

    for f in ROW_FREQS + COL_FREQS:
        ax2.axhline(y=f, color="#ffffff", linewidth=0.35,
                    linestyle="--", alpha=0.35)
        ax2.text(0.005, f + 18, f"{f} Hz",
                 color="#8b949e", fontsize=5.5, alpha=0.8,
                 transform=ax2.get_yaxis_transform())

    for tone in tones:
        col = DIGIT_COLORS.get(tone["digit"], "#ffffff")
        ax2.axvspan(tone["start_s"], tone["end_s"], alpha=0.22, color=col)

    # Leyenda
    seen = sorted(set(t["digit"] for t in tones))
    patches = [mpatches.Patch(color=DIGIT_COLORS.get(d, "#fff"), label=f'"{d}"')
               for d in seen]
    if patches:
        ax2.legend(handles=patches, loc="upper right",
                   facecolor="#161b22", edgecolor="#30363d",
                   labelcolor="white", fontsize=7, framealpha=0.9)

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ══════════════════════════════════════════════
#  RUTAS FLASK
# ══════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    if "audio" not in request.files:
        return jsonify({"error": "No se recibio ningun archivo."}), 400

    f = request.files["audio"]
    if not f or not f.filename:
        return jsonify({"error": "Archivo invalido."}), 400

    ext = os.path.splitext(f.filename)[1].lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Formato '{ext}' no soportado."}), 400

    # Guardar con nombre seguro
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    src_path  = os.path.join(UPLOAD_FOLDER, safe_name)
    f.save(src_path)

    tmp_files = [src_path]
    try:
        # Pipeline
        audio, sr, loaded_path = load_audio(src_path)
        if loaded_path != src_path:
            tmp_files.append(loaded_path)

        duration = len(audio) / sr

        audio = bandpass_filter(audio, sr)
        audio = reduce_noise_audio(audio, sr)
        audio = amplify(audio, AMPLIFY_DB)

        tones = analyze_dtmf(audio, sr)
        sequence = "".join(t["digit"] for t in tones)

        chart_b64 = build_chart(audio, sr, tones, duration)

        payload = {
            "ok"        : True,
            "filename"  : f.filename,
            "duration_s": round(duration, 2),
            "sample_rate": sr,
            "tones"     : tones,
            "sequence"  : sequence,
            "chart"     : chart_b64,
        }
        return jsonify(payload)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        for p in tmp_files:
            try:
                os.remove(p)
            except Exception:
                pass


@app.route("/health")
def health():
    return jsonify({"status": "ok", "ffmpeg": FFMPEG_EXE or "not found"})


if __name__ == "__main__":
    print("=" * 55)
    print("  DTMF Analyzer - Servidor Web")
    print("  Abre: http://localhost:5050")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5050, debug=False)
