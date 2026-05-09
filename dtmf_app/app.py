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
import threading
import csv
import time
from collections import deque
from datetime import datetime
from io import BytesIO
from math import gcd

# IVR — reproducción de audio
try:
    import pygame
    pygame.mixer.init()
    _PYGAME_OK = True
except Exception:
    _PYGAME_OK = False
    print("[WARN] pygame no disponible — reproducción de audio desactivada")

# IVR — lectura de Excel
try:
    import openpyxl
    _OPENPYXL_OK = True
except Exception:
    _OPENPYXL_OK = False
    print("[WARN] openpyxl no disponible — carga de Excel desactivada")

# IVR — CallMonitor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from estado_llamada import CallMonitor
    _MONITOR_OK = True
except Exception as _e:
    _MONITOR_OK = False
    print(f"[WARN] CallMonitor no disponible: {_e}")

# IVR — WhatsApp Notifier
try:
    from notificaciones.whatsapp_ivr_notifier import (
        WhatsAppIVRNotifier, build_notification_message
    )
    _WA_OK = True
except Exception as _e:
    _WA_OK = False
    print(f"[WARN] WhatsAppIVRNotifier no disponible: {_e}")

# Audio del sistema (monitor DTMF en Python)
try:
    import sounddevice as sd
    _SD_OK = True
except Exception:
    _SD_OK = False
    print("[WARN] sounddevice no disponible")

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
from flask_socketio import SocketIO, emit

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
CONCENTRATION_THRESHOLD = 0.15   # Mas permisivo para captacion via microfono ambiente

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "wav", "mp3", "m4a", "aac", "ogg", "opus",
    "flac", "wma", "mp4", "webm", "3gp", "amr"
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/*": {"origins": "*"}})
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024   # 100 MB max
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


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

    digit = DTMF_MAP.get((br, bc))
    if not digit and frame_energy > 1e-5:
        # Log si hay mucha energia pero no mapea a DTMF
        print(f"[DEBUG-DTMF] Energia alta ({frame_energy:.2e}) pero no es DTMF. Row:{br}({row_dom:.2f}) Col:{bc}({col_dom:.2f}) Conc:{concentration:.2f}")

    return digit


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


# ══════════════════════════════════════════════
#  WEBSOCKET — MONITOR EN TIEMPO REAL
# ══════════════════════════════════════════════

# Buffer deslizante por sesion (sid -> list of float32)
_rt_buffers = {}
_rt_sr       = {}   # sample rate del cliente por sesion

# Filtro pasa-banda reutilizable (se construye una vez por sr)
_bp_cache = {}

def _get_bandpass(sr: int):
    if sr not in _bp_cache:
        nyq = sr / 2.0
        sos = butter(4, [300 / nyq, 3400 / nyq], btype="band", output="sos")
        _bp_cache[sr] = sos
    return _bp_cache[sr]


@socketio.on("connect")
def on_connect():
    sid = request.sid
    _rt_buffers[sid] = []
    _rt_sr[sid]      = 8000
    emit("connected", {"sid": sid})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    _rt_buffers.pop(sid, None)
    _rt_sr.pop(sid, None)


@socketio.on("rt_config")
def on_rt_config(data):
    """Cliente informa su sample rate."""
    sid = request.sid
    _rt_sr[sid] = int(data.get("sampleRate", 44100))


@socketio.on("audio_chunk")
def on_audio_chunk(data):
    """
    Recibe un chunk de PCM Float32 del navegador (base64 o lista),
    lo acumula en un buffer deslizante de 80 ms,
    aplica Goertzel y emite el digito detectado (o None).
    """
    sid       = request.sid
    client_sr = _rt_sr.get(sid, 44100)

    # Decodificar: el cliente envia JSON con {pcm: [f32, ...], sr: int}
    pcm_list  = data.get("pcm", [])
    if not pcm_list:
        return

    client_sr = int(data.get("sr", client_sr))
    _rt_sr[sid] = client_sr

    # Convertir a numpy float32
    chunk = np.array(pcm_list, dtype=np.float32)

    # Resamplear a 8 kHz si es necesario
    if client_sr != TARGET_SR:
        g     = gcd(TARGET_SR, client_sr)
        chunk = resample_poly(chunk, TARGET_SR // g, client_sr // g).astype(np.float32)

    # Acumular en buffer
    buf = _rt_buffers.setdefault(sid, [])
    buf.extend(chunk.tolist())

    # Ventana de analisis: 80 ms = 640 muestras a 8kHz
    WINDOW = int(TARGET_SR * 0.08)   # 640
    HOP    = int(TARGET_SR * 0.02)   # 160 (20 ms hop)

    if len(buf) < WINDOW:
        return   # todavia no hay suficientes muestras

    # Tomar la ventana mas reciente
    frame_np = np.array(buf[-WINDOW:], dtype=np.float32)

    # Filtro pasa-banda
    sos        = _get_bandpass(TARGET_SR)
    frame_filt = sosfilt(sos, frame_np).astype(np.float32)

    energy = float(np.mean(frame_filt ** 2))
    if energy < ENERGY_THRESHOLD:
        emit("rt_digit", {"digit": None, "energy": 0.0})
        # Limpiar buffer acumulado (silencio)
        _rt_buffers[sid] = []
        return

    digit = detect_dtmf_frame(frame_filt, TARGET_SR, energy)

    # Mantener buffer deslizante: descartar muestras antiguas
    if len(buf) > WINDOW * 4:
        _rt_buffers[sid] = buf[-WINDOW:]

    emit("rt_digit", {
        "digit" : digit,
        "energy": round(float(energy), 8),
    })

    # Diagnostico: si la campaña esta activa, contar frames recibidos
    if _ivr_dtmf_callback:
        if not hasattr(on_audio_chunk, "_cnt"): on_audio_chunk._cnt = 0
        on_audio_chunk._cnt += 1
        if on_audio_chunk._cnt % 25 == 0: # aprox cada 1 seg (40ms * 25)
            _emit_ivr("ivr_log", {"msg": f"  [DEBUG] Mic activo ({energy:.2e})", "level": "info"})

    # Si hay una campaña IVR activa y se detectó un dígito real → notificarla
    if digit and _ivr_dtmf_callback:
        print(f"[IVR-RT] Digito '{digit}' detectado (E={energy:.2e}) -> enviando a campaña")
        try:
            _ivr_dtmf_callback(digit)
        except Exception as exc:
            print(f"[IVR-RT] Error en callback: {exc}")


# ══════════════════════════════════════════════
#  IVR AUTOMATOR — Configuración y estado global
# ══════════════════════════════════════════════

IVR_RESULTS_CSV = os.path.join(os.path.dirname(__file__), "ivr_results.csv")
IVR_AUDIO_FOLDER = os.path.join(os.path.dirname(__file__), "ivr_audio")
os.makedirs(IVR_AUDIO_FOLDER, exist_ok=True)

# Estado global de la campaña (singleton)
_ivr_campaign: "IVRCampaign | None" = None
_ivr_lock = threading.Lock()
_adb_watchdog: "ADBWatchdog | None" = None   # monitor de conexión ADB

# Cuando el IVR está ACTIVO, cualquier dígito DTMF detectado por
# el monitor de micrófono se desvía aquí en lugar de sólo emitirse a la UI
_ivr_dtmf_callback = None   # callable(digit) | None

# Dispositivo de salida de audio seleccionado (nombre para pygame)
_audio_output_device_name: str | None = None

# ── WhatsApp Notifications ──────────────────────────────────────────
WA_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "wa_notif_config.json")

# Instancia única del notificador
_wa_notifier: "WhatsAppIVRNotifier | None" = None

# Config activa (se carga del JSON al iniciar)
_wa_config: dict = {
    "enabled": False,
    "contact": "",   # grupo o número principal
    "backup":  "",   # número de respaldo
}


def _wa_load_config():
    """Carga la configuración de notificaciones WA desde disco."""
    global _wa_config, _wa_notifier
    if os.path.isfile(WA_CONFIG_FILE):
        try:
            with open(WA_CONFIG_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _wa_config.update(data)
            print(f"[WA] Config cargada: enabled={_wa_config['enabled']} contact='{_wa_config['contact']}'")
        except Exception as exc:
            print(f"[WA] Error leyendo config: {exc}")
    # Inicializar instancia del notificador si selenium está disponible
    if _WA_OK:
        _wa_notifier = WhatsAppIVRNotifier()


def _wa_save_config():
    """Persiste la configuración de notificaciones WA en disco."""
    try:
        with open(WA_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(_wa_config, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[WA] Error guardando config: {exc}")


def _send_whatsapp_notification(number: str, status: str,
                                 digit: str | None = None,
                                 option_desc: str | None = None):
    """
    Envía notificación WhatsApp al finalizar una llamada.
    Se llama desde _process_number() si las notificaciones están activas.
    """
    if not _WA_OK or not _wa_notifier:
        return
    if not _wa_config.get("enabled"):
        return
    contacto = _wa_config.get("contact", "").strip()
    if not contacto:
        print("[WA] ⚠ Sin contacto destino configurado")
        return
    backup = _wa_config.get("backup", "").strip() or None
    mensaje = build_notification_message(number, status, digit, option_desc)
    _wa_notifier.enqueue_notification(contacto, mensaje, backup)


# Cargar config al arrancar
_wa_load_config()


def _save_call_result(number: str, status: str, digit: str | None, notes: str = ""):
    """Guarda el resultado de una llamada en el CSV de resultados."""
    file_exists = os.path.isfile(IVR_RESULTS_CSV)
    with open(IVR_RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "numero", "estado", "tono_detectado", "notas"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            number, status, digit or "", notes
        ])


def _play_audio(path: str, cancel_event: threading.Event = None):
    """
    Reproduce un archivo de audio usando pygame, con soporte de dispositivo de salida.
    Si se proporciona cancel_event y se activa, la reproducción se detiene inmediatamente
    (útil para cortar el audio cuando el cliente cuelga durante la llamada).
    """
    if not _PYGAME_OK or not path or not os.path.isfile(path):
        return
    try:
        print(f"[IVR-Audio] Reproduciendo: {os.path.basename(path)}")
        # Reinicializar mixer si hay dispositivo de salida específico
        if _audio_output_device_name:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.quit()
                pygame.mixer.init(devicename=_audio_output_device_name)
            except Exception as ex:
                print(f"[IVR-Audio] No se pudo seleccionar salida '{_audio_output_device_name}': {ex}")
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
        elif not pygame.mixer.get_init():
            pygame.mixer.init()

        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        start = time.time()
        while pygame.mixer.music.get_busy() and (time.time() - start) < 60:
            # Verificar si el cliente colgó o se solicitó detener la campaña
            if cancel_event and cancel_event.is_set():
                pygame.mixer.music.stop()
                print(f"[IVR-Audio] Reproducción cancelada (llamada terminada): {os.path.basename(path)}")
                return
            time.sleep(0.1)
        print(f"[IVR-Audio] Fin reproduccion: {os.path.basename(path)}")
    except Exception as exc:
        print(f"[IVR] Error reproduciendo audio: {exc}")


def _emit_ivr(event: str, data: dict):
    """Emite un evento Socket.IO desde cualquier hilo."""
    socketio.emit(event, data)


# ════════════════════════════════════════════
#  ADB WATCHDOG — Monitor de conexión en tiempo real
# ════════════════════════════════════════════

class ADBWatchdog(threading.Thread):
    """
    Monitorea en tiempo real si el dispositivo ADB sigue conectado.
    - Si se desconecta: pausa la campaña activa y emite alerta a la UI.
    - Si reconecta:     reanuda automáticamente la campaña.
    """
    CHECK_INTERVAL = 3.0   # segundos entre verificaciones

    def __init__(self, device_id: str):
        super().__init__(daemon=True, name=f"ADBWatchdog-{device_id}")
        self.device_id  = device_id
        self._stop_ev   = threading.Event()
        self._connected = True   # optimista al inicio

    # ── Control ──────────────────────────────────────────────────

    def stop(self):
        self._stop_ev.set()

    # ── Hilo principal ────────────────────────────────────────────

    def run(self):
        print(f"[ADBWatchdog] Iniciando monitoreo: {self.device_id}")
        # Primera verificación inmediata
        self._connected = self._check()
        _emit_ivr("adb_status", {
            "connected": self._connected,
            "device_id": self.device_id,
        })

        while not self._stop_ev.wait(self.CHECK_INTERVAL):
            connected = self._check()
            if connected != self._connected:
                self._connected = connected
                self._on_change(connected)

        print(f"[ADBWatchdog] Detenido: {self.device_id}")

    def _check(self) -> bool:
        """Retorna True si el dispositivo responde como 'device' en ADB."""
        try:
            cmd = ["adb", "-s", self.device_id, "get-state"]
            r   = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return r.returncode == 0 and "device" in r.stdout
        except Exception:
            return False

    def _on_change(self, connected: bool):
        """Reacciona al cambio de estado de conexión."""
        _emit_ivr("adb_status", {"connected": connected, "device_id": self.device_id})

        if connected:
            # Reconectado — reanudar campaña si estaba pausada
            _emit_ivr("ivr_log", {
                "msg": f"✅ Dispositivo ADB reconectado: {self.device_id}",
                "level": "success"
            })
            if _ivr_campaign and _ivr_campaign.is_running:
                _ivr_campaign.resume()
                _emit_ivr("ivr_log", {"msg": "▶️ Campaña reanudada", "level": "success"})
        else:
            # Desconectado — pausar campaña
            _emit_ivr("ivr_log", {
                "msg": f"⚠️ ADB desconectado: {self.device_id} — campaña en pausa",
                "level": "warn"
            })
            if _ivr_campaign and _ivr_campaign.is_running:
                _ivr_campaign.pause()
                _emit_ivr("ivr_log", {
                    "msg": "⏸️ Esperando reconexión del dispositivo…",
                    "level": "warn"
                })


# ══════════════════════════════════════════════
#  PRE-CALL AUDIO ANALYZER
#  Detecta ring tones y voz del operador ANTES de que la llamada entre en ACTIVE
#  Técnicas: Goertzel @ ring freqs + Spectral Flatness + ZCR
# ══════════════════════════════════════════════

class PreCallAudioAnalyzer(threading.Thread):
    """
    Analiza el audio capturado internamente durante la fase DIALING/CONNECTING
    para distinguir:
      - Tonos de timbre (ring tones): señal tonal estrecha con patrón ON/OFF
      - Voz del operador: señal broadband sostenida (número apagado/no disponible)

    Resultado:
      self.ring_count     (int)  — número de rings completos detectados
      self.operator_voice (bool) — True si se detectó voz del operador
    """

    # ── Parámetros de análisis ─────────────────────────────────────
    FRAME_MS     = 100          # ms por ventana de análisis
    # Frecuencias típicas de ring tone (Hz) — Colombia: ~425 Hz
    RING_FREQS   = [400, 425, 440, 450]
    RING_E_THR   = 5e-5         # energía mínima para procesar el frame
    FLAT_TONE    = 0.12         # spectral flatness ≤ → frame tonal (ring)
    FLAT_VOICE   = 0.22         # spectral flatness ≥ → frame broadband (voz)
    ZCR_VOICE    = 0.07         # ZCR normalizado ≥ → componente vocal
    RING_ON_MIN  = 0.7          # duración mínima burst de ring (s)
    RING_ON_MAX  = 3.2          # duración máxima burst de ring (s)
    RING_OFF_MIN = 1.2          # silencio mínimo entre rings (s)
    VOICE_WIN    = 1.5          # ventana para evaluar voz sostenida (s)
    VOICE_RATIO  = 0.55         # % frames de voz para declarar operator_voice
    MAX_RINGS    = 2            # límite de rings para clasificar como UNAVAILABLE

    def __init__(self, device_index=None):
        super().__init__(daemon=True, name="PreCallAudioAnalyzer")
        self.device_index   = device_index
        self._stop_ev       = threading.Event()
        self.ring_count     = 0
        self.operator_voice = False
        # Estado interno del detector de ring
        self._in_ring       = False
        self._ring_start    = 0.0
        self._silence_start = 0.0
        self._in_silence    = False
        self._pending_ring  = False   # burst completado, esperando silencio para confirmar
        self._pending_start = 0.0
        # Historial de clasificaciones de frame para VAD
        self._frame_classes: list = []   # "ring" | "voice" | "silence"

    # ── API pública ────────────────────────────────────────────────

    def stop(self):
        self._stop_ev.set()

    # ── Métodos de análisis espectral ──────────────────────────────

    @staticmethod
    def _goertzel_ring_energy(frame: np.ndarray, sr: int) -> float:
        """Suma de energía Goertzel en las frecuencias típicas de ring tone."""
        N = len(frame)
        total = 0.0
        for freq in PreCallAudioAnalyzer.RING_FREQS:
            k      = int(0.5 + N * freq / sr)
            omega  = 2.0 * np.pi * k / N
            coeff  = 2.0 * np.cos(omega)
            s1 = s2 = 0.0
            for x in frame:
                s  = float(x) + coeff * s1 - s2
                s2 = s1
                s1 = s
            total += (s2**2 + s1**2 - coeff * s1 * s2) / (N * N)
        return total

    @staticmethod
    def _spectral_flatness(frame: np.ndarray) -> float:
        """Spectral flatness (Wiener entropy): 0=tonal puro, 1=ruido blanco."""
        fft_mag = np.abs(np.fft.rfft(frame))
        fft_mag = fft_mag[fft_mag > 1e-12]   # evitar log(0)
        if len(fft_mag) < 4:
            return 0.0
        geo_mean = np.exp(np.mean(np.log(fft_mag)))
        ari_mean = np.mean(fft_mag)
        return float(geo_mean / (ari_mean + 1e-12))

    @staticmethod
    def _zcr(frame: np.ndarray) -> float:
        """Zero-crossing rate normalizado (0–1)."""
        signs = np.sign(frame)
        signs[signs == 0] = 1
        crossings = np.sum(np.abs(np.diff(signs))) / 2
        return float(crossings / len(frame))

    # ── Clasificador de frame ──────────────────────────────────────

    def _classify_frame(self, energy: float, ring_e: float,
                        flatness: float, zcr: float) -> str:
        """Clasifica un frame de audio como 'ring', 'voice' o 'silence'."""
        if energy < self.RING_E_THR:
            return "silence"

        # Tonal + energía concentrada en frecuencias de ring → ring
        if flatness <= self.FLAT_TONE and ring_e > energy * 0.30:
            return "ring"

        # Broadband + ZCR alto → voz
        if flatness >= self.FLAT_VOICE and zcr >= self.ZCR_VOICE:
            return "voice"

        # Broadband sin ZCR suficiente pero claramente no tonal → voice (conservador)
        if flatness >= self.FLAT_VOICE:
            return "voice"

        return "silence"   # ambiguo → tratar como silencio

    # ── Máquina de estados para ring detection ─────────────────────

    def _update_ring_state(self, cls: str, now: float):
        """Actualiza el conteo de rings en base a la secuencia ON/OFF."""
        is_active = (cls == "ring")

        if is_active:
            if not self._in_ring:
                # Inicio de nuevo burst
                self._in_ring    = True
                self._ring_start = now
                self._in_silence = False
                self._silence_start = 0.0
        else:
            if self._in_ring:
                burst_dur = now - self._ring_start
                self._in_ring = False
                if self.RING_ON_MIN <= burst_dur <= self.RING_ON_MAX:
                    # Burst de duración correcta → esperamos el silencio para confirmar
                    self._pending_ring  = True
                    self._pending_start = now
                elif burst_dur > self.RING_ON_MAX:
                    # Demasiado largo para ser ring → ignorar
                    self._pending_ring = False
                self._silence_start = now
                self._in_silence    = True

            if self._in_silence and self._pending_ring:
                silence_dur = now - self._pending_start
                if silence_dur >= self.RING_OFF_MIN:
                    # Ring confirmado (burst + silencio correcto)
                    self.ring_count   += 1
                    self._pending_ring = False
                    print(f"[PreCall] Ring #{self.ring_count} detectado")

    # ── Evaluador de voz sostenida (VAD) ───────────────────────────

    def _update_vad(self, cls: str):
        """Acumula clasificaciones y detecta voz sostenida del operador."""
        self._frame_classes.append(cls)
        frames_in_window = int(self.VOICE_WIN * 1000 / self.FRAME_MS)
        if len(self._frame_classes) > frames_in_window * 2:
            self._frame_classes = self._frame_classes[-frames_in_window:]
        if len(self._frame_classes) >= frames_in_window:
            window = self._frame_classes[-frames_in_window:]
            voice_ratio = window.count("voice") / len(window)
            if voice_ratio >= self.VOICE_RATIO:
                self.operator_voice = True

    # ── Hilo principal ────────────────────────────────────────────

    def run(self):
        if not _SD_OK:
            print("[PreCall] sounddevice no disponible — análisis pre-llamada omitido")
            return
        try:
            dev_info  = sd.query_devices(self.device_index, "input")
            sr_native = int(dev_info["default_samplerate"])
        except Exception as exc:
            print(f"[PreCall] Dispositivo inválido: {exc}")
            return

        frame_samples_native = int(sr_native * self.FRAME_MS / 1000)
        frame_samples_8k     = int(TARGET_SR * self.FRAME_MS / 1000)

        print(f"[PreCall] Iniciado — dispositivo idx={self.device_index} @ {sr_native} Hz")

        try:
            with sd.InputStream(
                device      = self.device_index,
                channels    = 1,
                samplerate  = sr_native,
                blocksize   = frame_samples_native,
                dtype       = "float32",
            ) as stream:
                while not self._stop_ev.is_set():
                    data, _ = stream.read(frame_samples_native)
                    if self._stop_ev.is_set():
                        break

                    chunk = data[:, 0].astype(np.float32)

                    # Resamplear a 8 kHz para análisis
                    if sr_native != TARGET_SR:
                        g     = gcd(TARGET_SR, sr_native)
                        chunk = resample_poly(
                            chunk, TARGET_SR // g, sr_native // g
                        ).astype(np.float32)

                    # Tomar exactamente frame_samples_8k muestras
                    if len(chunk) > frame_samples_8k:
                        chunk = chunk[:frame_samples_8k]
                    elif len(chunk) < frame_samples_8k // 2:
                        continue

                    energy   = float(np.mean(chunk ** 2))
                    ring_e   = self._goertzel_ring_energy(chunk, TARGET_SR)
                    flatness = self._spectral_flatness(chunk)
                    zcr      = self._zcr(chunk)

                    cls = self._classify_frame(energy, ring_e, flatness, zcr)
                    now = time.time()

                    self._update_ring_state(cls, now)
                    self._update_vad(cls)

        except Exception as exc:
            print(f"[PreCall] Error en captura: {exc}")

        print(f"[PreCall] Detenido — rings={self.ring_count} voice={self.operator_voice}")


class IVRCampaign(threading.Thread):
    """
    Hilo que procesa una cola de números telefónicos uno por uno.
    Para cada número:
      1. Marca con ADB
      2. Monitorea el estado via logcat (CallMonitor)
      3. Si ACTIVE → reproduce audio inicial
      4. Espera tono DTMF del cliente via monitor de micrófono
      5. Si detecta dígito válido → reproduce audio de despedida → cuelga
      6. Si no detecta en timeout → reproduce audio intermedio → repite
      7. Guarda resultado en CSV
    """

    def __init__(self, config: dict):
        super().__init__(daemon=True, name="IVRCampaign")
        self.config        = config
        self.queue         = deque(config.get("numbers", []))
        self.total         = len(self.queue)
        self.processed     = 0
        self.device_id     = config.get("device_id")
        self.delay_s       = float(config.get("delay_seconds", 5))
        self.audio_welcome  = config.get("audio_welcome")   # bienvenida — se reproduce UNA vez
        self.audio_menu     = config.get("audio_menu")      # menú IVR — se repite N veces
        self.audio_bye      = config.get("audio_bye")       # despedida global (fallback por opción)
        self.audio_no_tone  = config.get("audio_no_tone")   # audio al agotar intentos sin tono
        self.ivr_options    = config.get("ivr_options", {}) # {"1": "Interesado", ...}
        self.tone_timeout   = float(config.get("tone_timeout", 10))
        self.menu_repeats   = int(config.get("menu_repeats", 2))  # veces que se repite el menú IVR
        self.is_test        = config.get("is_test", False)

        self._stop_event   = threading.Event()
        self._pause_event  = threading.Event()
        self._pause_event.set()   # no pausado al inicio

        # Para sincronizar detección de tono dentro de handle_active
        self._digit_event  = threading.Event()
        self._last_digit   = None

    # ── API pública ──────────────────────────────────────────────

    def stop(self):
        self._stop_event.set()
        self._digit_event.set()   # desbloquear espera de tono

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_running(self):
        return self.is_alive() and not self._stop_event.is_set()

    def on_dtmf(self, digit: str):
        """Llamado por el handler WebSocket cuando llega un dígito DTMF."""
        print(f"[IVRCampaign] Digito recibido en campaña: {digit}")
        self._last_digit = digit
        self._digit_event.set()

    # ── Hilo principal ───────────────────────────────────────────

    def run(self):
        global _ivr_dtmf_callback
        _ivr_dtmf_callback = self.on_dtmf

        _emit_ivr("ivr_log", {"msg": f"🚀 Campaña iniciada — {self.total} números en cola",
                               "level": "info"})

        while self.queue and not self._stop_event.is_set():
            self._pause_event.wait()   # bloqueante si está pausado
            if self._stop_event.is_set():
                break

            number = self.queue.popleft()
            self.processed += 1
            self._process_number(number)

            if self.queue and not self._stop_event.is_set():
                _emit_ivr("ivr_log", {"msg": f"⏳ Esperando {self.delay_s}s antes de la próxima llamada",
                                       "level": "info"})
                self._interruptible_sleep(self.delay_s)

        _ivr_dtmf_callback = None
        status = "detenida" if self._stop_event.is_set() else "completada"
        _emit_ivr("ivr_log", {"msg": f"✅ Campaña {status}. Procesados: {self.processed}/{self.total}",
                               "level": "success"})
        _emit_ivr("ivr_campaign_done", {"processed": self.processed, "total": self.total})

    def _process_number(self, number: str):
        """Ejecuta el flujo completo para un número: marcar → monitorear → reaccionar."""
        _emit_ivr("ivr_call_update", {
            "number": number, "status": "CALLING",
            "processed": self.processed, "total": self.total
        })
        _emit_ivr("ivr_log", {"msg": f"📞 Marcando: {number}", "level": "info"})

        result_status = "NO_ANSWER"
        result_digit  = None

        # — 1. Marcar ——————————————————————————————————————————
        try:
            self._adb(["shell", "am", "start", "-a",
                       "android.intent.action.CALL", "-d", f"tel:{number}"])
        except Exception as exc:
            _emit_ivr("ivr_log", {"msg": f"❌ Error marcando {number}: {exc}", "level": "error"})
            _save_call_result(number, "ADB_ERROR", None, str(exc))
            _emit_ivr("ivr_call_update", {"number": number, "status": "ERROR",
                                           "processed": self.processed, "total": self.total})
            return

        # — 2. Monitorear estado —————————————————————————————
        # call_stop:        desbloquea el bucle de espera inicial (ACTIVE o DISCONNECTED temprano)
        # monitor_stop:     detiene el hilo logcat — SOLO al final del flujo completo
        # call_disconnected: se activa si el cliente cuelga EN CUALQUIER momento
        call_stop         = threading.Event()
        monitor_stop      = threading.Event()   # ← evento INDEPENDIENTE para el CallMonitor
        call_disconnected = threading.Event()
        call_state        = {"current": "CONNECTING"}
        # Timestamp en que la llamada entró a DIALING — usado para el discriminador de tiempo
        # Un número apagado/no disponible pasa a ACTIVE en < 10s desde DIALING
        # Un número que timbra (incluso al buzón) pasa a ACTIVE en ≥10s
        dialing_start_time: list = [time.time()]   # lista mutable para acceso desde closure
        MIN_DIALING_SECS = 15.0   # seg — umbral de tiempo en DIALING para confirmar que timbó

        def on_state(state: str):
            call_state["current"] = state
            _emit_ivr("ivr_status", {"number": number, "state": state})
            _emit_ivr("ivr_log", {"msg": f"  -> Estado: {state}", "level": "info"})
            if state == "DIALING":
                dialing_start_time[0] = time.time()   # registrar inicio de DIALING
            elif state == "ACTIVE":
                call_stop.set()          # desbloquea el bucle de espera inicial — NO detiene el monitor
            elif state == "DISCONNECTED":
                call_stop.set()          # desbloquea el bucle de espera inicial
                call_disconnected.set()  # señala cuelgue a _handle_active()
                # NO hacemos monitor_stop.set() aquí; el monitor se para al final del flujo

        if _MONITOR_OK:
            monitor = CallMonitor(device_id=self.device_id)
            # Usamos monitor_stop (no call_stop) para que el monitor siga leyendo
            # logcat incluso después de entrar a ACTIVE — así detecta el DISCONNECTED
            # que ocurre cuando el cliente cuelga durante la reproducción de audio.
            monitor.start(on_state_change=on_state, stop_event=monitor_stop, clear_logs=True)
        else:
            time.sleep(3)
            call_state["current"] = "ACTIVE"

        # — 3. Analizador de audio pre-llamada (durante DIALING) ——————
        # Detecta ring tones y voz del operador ANTES de que la llamada entre en ACTIVE.
        # Si hay voz del operador con ≤ MAX_RINGS rings → número apagado/no disponible.
        pre_call = None
        if _SD_OK and _audio_monitor_device is not None:
            pre_call = PreCallAudioAnalyzer(device_index=_audio_monitor_device)
            pre_call.start()
            _emit_ivr("ivr_log", {
                "msg": "  🔍 Analizando audio pre-llamada (ring/voz)...",
                "level": "info"
            })

        # Esperar hasta que la llamada entre en ACTIVE o se desconecte (máx 60 s)
        deadline = time.time() + 60
        while not call_stop.is_set() and not self._stop_event.is_set():
            if time.time() > deadline:
                break
            time.sleep(0.2)

        # Detener analizador pre-llamada y leer resultados
        pre_rings = 0
        pre_voice = False
        if pre_call and pre_call.is_alive():
            pre_call.stop()
            pre_call.join(timeout=2.0)
        if pre_call:
            pre_rings = pre_call.ring_count
            pre_voice = pre_call.operator_voice
            _emit_ivr("ivr_log", {
                "msg": f"  📊 Pre-llamada: {pre_rings} ring(s) — voz_operador={pre_voice}",
                "level": "info"
            })

        final_state    = call_state["current"]
        time_in_dialing = time.time() - dialing_start_time[0]   # cuánto tiempo estuvo en DIALING

        if final_state == "ACTIVE":
            _emit_ivr("ivr_log", {
                "msg": f"  ⏱️ Tiempo en DIALING: {time_in_dialing:.1f}s",
                "level": "info"
            })
            # Condición UNAVAILABLE:
            #   1. El analizador detectó voz del operador  Y
            #   2. Se contaron pocos rings (indicador de audio)  Y
            #   3. El tiempo en DIALING fue < MIN_DIALING_SECS  ← GUARDIA PRINCIPAL
            #      (si estuvo ≥10s en DIALING, definitivamente timbó → NO es UNAVAILABLE)
            is_unavailable = (
                pre_voice
                and pre_rings <= PreCallAudioAnalyzer.MAX_RINGS
                and time_in_dialing < MIN_DIALING_SECS
            )
            if is_unavailable:
                # ⛔ Operador respondió sin dar tiempo a suficientes rings → apagado/sin servicio
                result_status = "UNAVAILABLE"
                _emit_ivr("ivr_log", {
                    "msg": f"  ⛔ Número no disponible — {pre_rings} ring(s), {time_in_dialing:.1f}s antes del operador",
                    "level": "warn"
                })
                self._hang_up()
            else:
                # El monitor sigue corriendo → puede detectar DISCONNECTED durante _handle_active()
                result_status, result_digit = self._handle_active(number, call_disconnected)
        elif final_state in ("DISCONNECTED", "CONNECTING", "DIALING"):
            result_status = "NO_ANSWER" if final_state != "DISCONNECTED" else "DISCONNECTED"
        else:
            result_status = "UNKNOWN"

        # Ahora sí detenemos el monitor logcat
        if _MONITOR_OK:
            monitor_stop.set()
            monitor.stop()

        self._hang_up()

        # Incluir info de pre-llamada en las notas del CSV cuando aplica
        pre_notes = ""
        if pre_call and (pre_voice or pre_rings > 0):
            pre_notes = f"rings={pre_rings} voice={pre_voice}"
        _save_call_result(number, result_status, result_digit, pre_notes)
        _emit_ivr("ivr_call_update", {
            "number": number, "status": result_status,
            "digit": result_digit,
            "processed": self.processed, "total": self.total
        })
        _emit_ivr("ivr_log", {
            "msg": f"  OK {number} -> {result_status}" + (f" (opcion: {result_digit})" if result_digit else ""),
            "level": "success"
        })

        # ── WhatsApp notification ────────────────────────────────────
        if _wa_config.get("enabled") and _wa_notifier:
            option_desc = None
            if result_digit and self.ivr_options:
                opt = self.ivr_options.get(result_digit)
                if isinstance(opt, dict):
                    option_desc = opt.get("desc", result_digit)
                elif opt:
                    option_desc = str(opt)
            _send_whatsapp_notification(number, result_status, result_digit, option_desc)

    def _handle_active(self, number: str,
                        disconnect_event: threading.Event = None) -> tuple[str, str | None]:
        """
        La llamada fue contestada (ACTIVE).
        Reproduce audio inicial, espera tono válido, actúa.
        Tonos no configurados se ignoran y se sigue esperando.

        disconnect_event: evento que se activa si el cliente cuelga en cualquier momento.
                          Cuando se detecta, se interrumpe la reproducción de audio y
                          el bucle de espera de tono inmediatamente.

        Retorna (status, digit_detectado)
        """
        # Evento combinado: cuelgue del cliente O stop de la campaña
        def _caller_gone() -> bool:
            """True si el cliente colgó o se detuvo la campaña."""
            return self._stop_event.is_set() or (
                disconnect_event is not None and disconnect_event.is_set()
            )

        _emit_ivr("ivr_log", {"msg": "  Llamada contestada — activando monitor de audio...", "level": "success"})
        start_audio_monitor(_audio_monitor_device)

        self._digit_event.clear()
        self._last_digit = None

        # ══ 1. BIENVENIDA — se reproduce UNA sola vez ═══════════════════
        _emit_ivr("ivr_log", {"msg": "  🎙️ Reproduciendo bienvenida...", "level": "info"})
        _play_audio(self.audio_welcome, cancel_event=disconnect_event)

        if _caller_gone():
            stop_audio_monitor()
            if disconnect_event and disconnect_event.is_set():
                _emit_ivr("ivr_log", {"msg": "  📵 Cliente colgó durante la bienvenida", "level": "warn"})
                return "DISCONNECTED_DURING_CALL", None
            return "STOPPED", None

        # ══ 2. MENÚ IVR — se repite menu_repeats veces ══════════════════
        # Barge-in: si ya detectó algo durante la bienvenida, evaluamos abajo
        if self._digit_event.is_set():
            _emit_ivr("ivr_log", {"msg": "  (Señal detectada durante la bienvenida — evaluando...)", "level": "info"})

        for attempt in range(self.menu_repeats):
            # ── Verificar antes de reproducir el menú ──────────────────
            if _caller_gone():
                stop_audio_monitor()
                if disconnect_event and disconnect_event.is_set():
                    _emit_ivr("ivr_log", {"msg": "  📵 Cliente colgó antes del menú IVR", "level": "warn"})
                    return "DISCONNECTED_DURING_CALL", None
                return "STOPPED", None

            _emit_ivr("ivr_log", {
                "msg": f"  📋 Reproduciendo menú IVR (intento {attempt + 1}/{self.menu_repeats})...",
                "level": "info"
            })
            _play_audio(self.audio_menu, cancel_event=disconnect_event)

            if _caller_gone():
                stop_audio_monitor()
                if disconnect_event and disconnect_event.is_set():
                    _emit_ivr("ivr_log", {"msg": "  📵 Cliente colgó durante el menú IVR", "level": "warn"})
                    return "DISCONNECTED_DURING_CALL", None
                return "STOPPED", None

            # ── Esperar tono DTMF ───────────────────────────────────────
            deadline = time.time() + self.tone_timeout
            _emit_ivr("ivr_log", {"msg": f"  ⏳ Esperando tono válido ({self.tone_timeout}s)...", "level": "info"})

            digit = None
            while time.time() < deadline:
                # Verificar cuelgue o stop de campaña
                if _caller_gone():
                    stop_audio_monitor()
                    if disconnect_event and disconnect_event.is_set():
                        _emit_ivr("ivr_log", {"msg": "  📵 Cliente colgó mientras esperaba tono", "level": "warn"})
                        return "DISCONNECTED_DURING_CALL", None
                    return "STOPPED", None

                remaining = max(0.1, deadline - time.time())
                self._digit_event.wait(timeout=remaining)
                self._digit_event.clear()

                candidate = self._last_digit
                self._last_digit = None

                if not candidate:
                    continue

                if candidate in self.ivr_options:
                    digit = candidate
                    break
                else:
                    _emit_ivr("ivr_log", {
                        "msg": f"  Tono '{candidate}' no configurado — ignorando",
                        "level": "info"
                    })

            if digit:
                # ── Tono válido detectado ───────────────────────────────
                option_data = self.ivr_options[digit]
                if isinstance(option_data, dict):
                    desc      = option_data.get("desc", digit)
                    audio_bye = option_data.get("audio_bye") or self.audio_bye
                else:
                    desc      = str(option_data)
                    audio_bye = self.audio_bye

                _emit_ivr("ivr_log", {"msg": f"  ✅ Tono válido: {digit} — {desc}", "level": "success"})
                _emit_ivr("ivr_digit", {"number": number, "digit": digit, "option": desc})
                _play_audio(audio_bye, cancel_event=disconnect_event)
                self._hang_up()
                stop_audio_monitor()
                return "ANSWERED_TONE", digit

            # Timeout sin tono — continuar al siguiente intento del menú

        # ══ 3. SIN TONO — agotados todos los intentos ═══════════════════
        _emit_ivr("ivr_log", {"msg": "  ⚠️ Sin tono detectado en todos los intentos", "level": "warn"})
        if self.audio_no_tone:
            _emit_ivr("ivr_log", {"msg": "  🔔 Reproduciendo audio de cierre (sin tono)...", "level": "info"})
            _play_audio(self.audio_no_tone, cancel_event=disconnect_event)
        self._hang_up()
        stop_audio_monitor()
        return "ANSWERED_NO_TONE", None

    # ── Helpers ADB ──────────────────────────────────────────────

    def _adb(self, args: list):
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        cmd += args
        return subprocess.run(cmd, capture_output=True, timeout=10)

    def _hang_up(self):
        try:
            self._adb(["shell", "input", "keyevent", "6"])  # KEYCODE_ENDCALL
        except Exception:
            pass

    def _interruptible_sleep(self, seconds: float):
        deadline = time.time() + seconds
        while time.time() < deadline and not self._stop_event.is_set():
            time.sleep(0.2)


# ══════════════════════════════════════════════
#  MONITOR DE AUDIO EN PYTHON (sounddevice)
# ══════════════════════════════════════════════

_audio_monitor_thread: "PythonAudioMonitor | None" = None
_audio_monitor_device = None   # índice del dispositivo seleccionado

class PythonAudioMonitor(threading.Thread):
    """
    Captura audio directamente del dispositivo de entrada del sistema
    y ejecuta detección DTMF Goertzel en tiempo real.
    No depende del navegador ni de mediaDevices.
    """
    WINDOW = int(TARGET_SR * 0.08)   # 640 muestras @ 8 kHz
    HOP    = int(TARGET_SR * 0.02)   # 160 muestras

    def __init__(self, device_index=None):
        super().__init__(daemon=True, name="PythonAudioMonitor")
        self.device_index = device_index
        self._stop_ev = threading.Event()
        self._buf: list[float] = []
        self._last_digit: str | None = None
        self._last_emit = 0.0

    def run(self):
        if not _SD_OK:
            _emit_ivr("ivr_log", {"msg": "❌ sounddevice no instalado", "level": "error"})
            return
        try:
            dev_info = sd.query_devices(self.device_index, "input")
            sr_native = int(dev_info["default_samplerate"])
            dev_name  = dev_info["name"]
        except Exception as exc:
            _emit_ivr("ivr_log", {"msg": f"❌ Dispositivo audio inválido: {exc}", "level": "error"})
            return

        _emit_ivr("ivr_log", {
            "msg": f"🎤 Monitor activo: [{dev_name}] @ {sr_native} Hz",
            "level": "success"
        })

        def callback(indata, frames, time_info, status):
            if self._stop_ev.is_set():
                raise sd.CallbackStop()

            chunk = indata[:, 0].astype(np.float32)

            # Resamplear a 8 kHz si es necesario
            if sr_native != TARGET_SR:
                g = gcd(TARGET_SR, sr_native)
                chunk = resample_poly(chunk, TARGET_SR // g, sr_native // g).astype(np.float32)

            self._buf.extend(chunk.tolist())
            if len(self._buf) < self.WINDOW:
                return

            frame  = np.array(self._buf[-self.WINDOW:], dtype=np.float32)
            sos    = _get_bandpass(TARGET_SR)
            frame  = sosfilt(sos, frame).astype(np.float32)
            energy = float(np.mean(frame ** 2))

            if energy < ENERGY_THRESHOLD:
                self._last_digit = None
                if len(self._buf) > self.WINDOW * 4:
                    self._buf = self._buf[-self.WINDOW:]
                return

            digit = detect_dtmf_frame(frame, TARGET_SR, energy)

            # Emitir estado a la UI
            now = time.time()
            if now - self._last_emit > 0.08:
                self._last_emit = now
                socketio.emit("rt_digit", {"digit": digit, "energy": round(float(energy), 8)})

            # Notificar al IVR cuando hay dígito nuevo
            if digit and digit != self._last_digit:
                self._last_digit = digit
                print(f"[AudioMonitor] Dígito: {digit}  E={energy:.2e}")
                _emit_ivr("ivr_log", {"msg": f"  🎯 Tono detectado: {digit}", "level": "success"})
                if _ivr_dtmf_callback:
                    try:
                        _ivr_dtmf_callback(digit)
                    except Exception as exc:
                        print(f"[AudioMonitor] Error callback: {exc}")
            elif not digit:
                self._last_digit = None

            if len(self._buf) > self.WINDOW * 4:
                self._buf = self._buf[-self.WINDOW:]

        try:
            with sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=sr_native,
                blocksize=int(sr_native * 0.04),
                callback=callback,
            ):
                self._stop_ev.wait()
        except Exception as exc:
            _emit_ivr("ivr_log", {"msg": f"❌ Error en monitor de audio: {exc}", "level": "error"})

        _emit_ivr("ivr_log", {"msg": "🔇 Monitor de audio detenido", "level": "info"})

    def stop(self):
        self._stop_ev.set()


def start_audio_monitor(device_index=None):
    global _audio_monitor_thread
    if _audio_monitor_thread and _audio_monitor_thread.is_alive():
        _audio_monitor_thread.stop()
        _audio_monitor_thread.join(timeout=2)
    _audio_monitor_thread = PythonAudioMonitor(device_index)
    _audio_monitor_thread.start()


def stop_audio_monitor():
    global _audio_monitor_thread
    if _audio_monitor_thread:
        _audio_monitor_thread.stop()
        _audio_monitor_thread = None


@app.route("/ivr/audio_devices")
def ivr_audio_devices():
    """Lista los dispositivos de audio de entrada Y salida del sistema."""
    if not _SD_OK:
        return jsonify({"ok": False, "error": "sounddevice no instalado",
                        "inputs": [], "outputs": []}), 200
    try:
        devices   = sd.query_devices()
        default_in  = sd.default.device[0]
        default_out = sd.default.device[1]

        # Coleccionar entradas y salidas sin duplicar por nombre
        seen_in, seen_out = set(), set()
        inputs, outputs   = [], []

        for i, d in enumerate(devices):
            name = d["name"]
            sr   = int(d["default_samplerate"])
            base = {"index": i, "name": name, "samplerate": sr}

            if d["max_input_channels"] >= 1 and name not in seen_in:
                seen_in.add(name)
                inputs.append({**base,
                               "channels":   d["max_input_channels"],
                               "is_default": (i == default_in)})

            if d["max_output_channels"] >= 1 and name not in seen_out:
                seen_out.add(name)
                outputs.append({**base,
                                "channels":   d["max_output_channels"],
                                "is_default": (i == default_out)})

        return jsonify({"ok": True, "inputs": inputs, "outputs": outputs})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "inputs": [], "outputs": []}), 200


@app.route("/ivr/monitor/start", methods=["POST"])
def ivr_monitor_start():
    """Inicia el monitor de audio en Python."""
    data = request.get_json(silent=True) or {}
    device_index = data.get("device_index")  # None = predeterminado
    global _audio_monitor_device
    _audio_monitor_device = device_index
    start_audio_monitor(device_index)
    return jsonify({"ok": True, "msg": "Monitor de audio iniciado"})


@app.route("/ivr/monitor/stop", methods=["POST"])
def ivr_monitor_stop():
    """Detiene el monitor de audio en Python."""
    stop_audio_monitor()
    return jsonify({"ok": True, "msg": "Monitor detenido"})


@app.route("/ivr/test_output", methods=["POST"])
def ivr_test_output():
    """Reproduce un pitido de prueba en el dispositivo de salida seleccionado."""
    if not _SD_OK:
        return jsonify({"ok": False, "error": "sounddevice no instalado"})
    data = request.get_json(silent=True) or {}
    device_index = data.get("device_index")  # None = predeterminado

    def _play_beep():
        try:
            sr   = 44100
            dur  = 1.0      # 1 segundo
            freq = 1000.0   # 1 kHz — tono fácil de reconocer
            t    = np.linspace(0, dur, int(sr * dur), endpoint=False)
            # Envelope suave para evitar clicks
            env  = np.where(t < 0.05, t / 0.05,
                   np.where(t > 0.95, (dur - t) / 0.05, 1.0))
            wave = (np.sin(2 * np.pi * freq * t) * env * 0.7).astype(np.float32)
            kwargs = {"samplerate": sr}
            if device_index is not None:
                kwargs["device"] = int(device_index)
            sd.play(wave, **kwargs)
            sd.wait()
            _emit_ivr("ivr_log", {"msg": "🔊 Pitido de prueba reproducido OK", "level": "success"})
        except Exception as exc:
            _emit_ivr("ivr_log", {"msg": f"❌ Error reproduciendo pitido: {exc}", "level": "error"})

    threading.Thread(target=_play_beep, daemon=True).start()
    return jsonify({"ok": True, "msg": "Reproduciendo pitido..."})


@app.route("/ivr/test_input", methods=["POST"])
def ivr_test_input():
    """
    Captura 3 segundos de audio del mic seleccionado y emite el nivel de energía
    por socket. El frontend muestra una barra de nivel en tiempo real.
    """
    if not _SD_OK:
        return jsonify({"ok": False, "error": "sounddevice no instalado"})
    data = request.get_json(silent=True) or {}
    device_index = data.get("device_index")

    def _capture():
        try:
            dev_info  = sd.query_devices(device_index, "input")
            sr_native = int(dev_info["default_samplerate"])
            dev_name  = dev_info["name"]
            _emit_ivr("ivr_log", {"msg": f"🎤 Probando entrada: [{dev_name}]...", "level": "info"})

            duration  = 3.0   # segundos
            frames    = []
            stop_ev   = threading.Event()

            def callback(indata, n, t, status):
                chunk  = indata[:, 0].astype(np.float32)
                energy = float(np.mean(chunk ** 2))
                # Escala log para la barra (0-100)
                level = 0 if energy < 1e-10 else min(100, int(10 * np.log10(energy / 1e-10)))
                socketio.emit("input_test_level", {"level": level, "energy": round(energy, 8)})
                frames.append(chunk)

            with sd.InputStream(device=device_index, channels=1,
                                samplerate=sr_native, blocksize=int(sr_native * 0.1),
                                callback=callback):
                stop_ev.wait(timeout=duration)

            peak = max((float(np.max(np.abs(np.concatenate(frames)))) if frames else 0), 0)
            _emit_ivr("ivr_log", {
                "msg": f"✅ Prueba completada. Pico: {peak:.4f} {'(OK — se recibe señal)' if peak > 0.001 else '(SILENCIO — verifica el mic)'}",
                "level": "success" if peak > 0.001 else "warn"
            })
            socketio.emit("input_test_done", {"peak": round(peak, 5)})
        except Exception as exc:
            _emit_ivr("ivr_log", {"msg": f"❌ Error prueba entrada: {exc}", "level": "error"})
            socketio.emit("input_test_done", {"peak": 0})

    threading.Thread(target=_capture, daemon=True).start()
    return jsonify({"ok": True, "msg": "Capturando 3 segundos..."})


# ══════════════════════════════════════════════
#  IVR AUTOMATOR — Rutas Flask
# ══════════════════════════════════════════════

@app.route("/ivr/devices")
def ivr_devices():
    """Lista los dispositivos ADB conectados."""
    try:
        # Intentar encontrar adb en el PATH o con where
        adb_cmd = "adb"
        try:
            check = subprocess.run(["adb", "version"], capture_output=True, timeout=3)
            if check.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            # En Windows intentar con 'where'
            where = subprocess.run(["where", "adb"], capture_output=True, text=True, timeout=3)
            if where.returncode == 0 and where.stdout.strip():
                adb_cmd = where.stdout.strip().splitlines()[0]
            else:
                return jsonify({"ok": False, "error": "adb no encontrado en el sistema. Instala Android SDK Platform-Tools y agrégalo al PATH."}), 500

        result = subprocess.run([adb_cmd, "devices"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().splitlines()
        devices = []
        for line in lines[1:]:  # saltar cabecera
            line = line.strip()
            if line and "\t" in line:
                serial, state = line.split("\t", 1)
                if state.strip() == "device":
                    devices.append(serial.strip())
        return jsonify({"ok": True, "devices": devices})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/ivr/upload_numbers", methods=["POST"])
def ivr_upload_numbers():
    """Recibe un Excel, extrae la columna 'Celular' y devuelve la lista."""
    if not _OPENPYXL_OK:
        return jsonify({"ok": False, "error": "openpyxl no instalado"}), 500

    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No se recibió archivo"}), 400

    try:
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.active

        headers = [str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col_idx = None
        for i, h in enumerate(headers):
            if h.lower() == "celular":
                col_idx = i
                break

        if col_idx is None:
            return jsonify({"ok": False, "error": "No se encontró la columna 'Celular'"}), 400

        numbers = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            val = row[col_idx] if col_idx < len(row) else None
            if val is not None:
                num = str(val).strip().replace(" ", "").replace("-", "")
                if num:
                    numbers.append(num)

        wb.close()
        return jsonify({"ok": True, "numbers": numbers, "count": len(numbers)})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/ivr/upload_audio", methods=["POST"])
def ivr_upload_audio():
    """Guarda un archivo de audio para uso en el IVR. Tipo: 'initial'|'middle'|'bye'."""
    f = request.files.get("file")
    audio_type = request.form.get("type", "initial")  # initial | middle | bye
    if not f:
        return jsonify({"ok": False, "error": "No se recibió archivo"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    safe_name = f"ivr_{audio_type}{ext}"
    dest = os.path.join(IVR_AUDIO_FOLDER, safe_name)
    f.save(dest)
    return jsonify({"ok": True, "path": dest, "filename": f.filename, "type": audio_type})


@app.route("/ivr/wa/config", methods=["GET"])
def wa_config_get():
    """Devuelve la configuración actual de notificaciones WhatsApp."""
    return jsonify({
        "ok":      True,
        "config":  _wa_config,
        "available": _WA_OK,
        "browser": _wa_notifier.get_status() if _wa_notifier else {"status": "unavailable"},
    })


@app.route("/ivr/wa/config", methods=["POST"])
def wa_config_post():
    """Guarda la configuración de notificaciones WhatsApp."""
    global _wa_config
    data = request.get_json(force=True) or {}
    _wa_config["enabled"] = bool(data.get("enabled", False))
    _wa_config["contact"] = str(data.get("contact", "")).strip()
    _wa_config["backup"]  = str(data.get("backup",  "")).strip()
    _wa_save_config()
    return jsonify({"ok": True, "config": _wa_config})


@app.route("/ivr/wa/open_browser", methods=["POST"])
def wa_open_browser():
    """Abre Chrome con el perfil persistente de WhatsApp."""
    if not _WA_OK or not _wa_notifier:
        return jsonify({"ok": False, "error": "selenium no disponible"}), 500
    ok, msg = _wa_notifier.open_browser()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/ivr/wa/close_browser", methods=["POST"])
def wa_close_browser():
    """Cierra el navegador de WhatsApp."""
    if not _wa_notifier:
        return jsonify({"ok": False, "error": "Notificador no disponible"}), 500
    ok, msg = _wa_notifier.close_browser()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/ivr/wa/status")
def wa_status():
    """Estado actual del navegador de notificaciones."""
    if not _wa_notifier:
        return jsonify({"status": "unavailable", "message": "selenium no instalado", "available": False})
    s = _wa_notifier.get_status()
    s["available"] = _WA_OK
    return jsonify(s)


@app.route("/ivr/start", methods=["POST"])
def ivr_start():
    """Inicia una campaña o una prueba con 1 número."""
    global _ivr_campaign, _audio_monitor_device, _audio_output_device_name
    with _ivr_lock:
        if _ivr_campaign and _ivr_campaign.is_running:
            return jsonify({"ok": False, "error": "Ya hay una campaña activa"}), 409

    data = request.get_json(force=True) or {}

    numbers = data.get("numbers", [])
    if not numbers:
        return jsonify({"ok": False, "error": "Sin números en la cola"}), 400

    # Guardar dispositivos de audio seleccionados
    audio_input_index  = data.get("audio_device")        # índice int | None
    audio_output_index = data.get("audio_output_device") # índice int | None
    _audio_monitor_device = audio_input_index

    # Obtener nombre del dispositivo de salida para pygame
    if audio_output_index is not None and _SD_OK:
        try:
            _audio_output_device_name = sd.query_devices(int(audio_output_index))["name"]
            print(f"[IVR] Salida de audio: {_audio_output_device_name}")
        except Exception:
            _audio_output_device_name = None
    else:
        _audio_output_device_name = None

    config = {
        "numbers":        numbers,
        "device_id":      data.get("device_id"),
        "delay_seconds":  data.get("delay_seconds", 5),
        "audio_welcome":  data.get("audio_welcome"),   # bienvenida — una vez
        "audio_menu":     data.get("audio_menu"),      # menú IVR — repetido N veces
        "audio_bye":      data.get("audio_bye"),       # despedida global (fallback)
        "audio_no_tone":  data.get("audio_no_tone"),   # audio al agotar intentos sin tono
        "ivr_options":    data.get("ivr_options", {}),
        "tone_timeout":   data.get("tone_timeout", 10),
        "menu_repeats":   data.get("menu_repeats", 2),
        "is_test":        data.get("is_test", False),
    }

    with _ivr_lock:
        _ivr_campaign = IVRCampaign(config)
        _ivr_campaign.start()

    # ── Iniciar watchdog ADB ─────────────────────────────────────
    global _adb_watchdog
    device_id = config.get("device_id")
    if device_id:
        if _adb_watchdog and _adb_watchdog.is_alive():
            _adb_watchdog.stop()
        _adb_watchdog = ADBWatchdog(device_id)
        _adb_watchdog.start()

    # ── Asegurar browser WA si notificaciones activas ────────────────
    if _wa_config.get("enabled") and _wa_notifier:
        contact = _wa_config.get("contact", "").strip()
        if contact:
            threading.Thread(
                target=_wa_notifier.ensure_ready,
                daemon=True,
                name="WA-EnsureReady"
            ).start()
            _emit_ivr("ivr_log", {"msg": "🔔 Verificando WhatsApp para notificaciones…", "level": "info"})

    mode = "prueba" if config["is_test"] else "campaña"
    return jsonify({"ok": True, "msg": f"{mode.capitalize()} iniciada", "total": len(numbers)})


@app.route("/ivr/stop", methods=["POST"])
def ivr_stop():
    """Detiene la campaña activa."""
    global _ivr_campaign, _adb_watchdog
    with _ivr_lock:
        if not _ivr_campaign or not _ivr_campaign.is_running:
            return jsonify({"ok": False, "error": "No hay campaña activa"}), 409
        _ivr_campaign.stop()
    # Detener watchdog ADB también
    if _adb_watchdog and _adb_watchdog.is_alive():
        _adb_watchdog.stop()
        _adb_watchdog = None
    return jsonify({"ok": True, "msg": "Campaña detenida"})


@app.route("/ivr/adb/status")
def ivr_adb_status():
    """Verifica en tiempo real si el dispositivo ADB sigue conectado."""
    device_id = request.args.get("device_id", "").strip()
    if not device_id:
        return jsonify({"connected": False, "error": "Sin device_id"})
    try:
        r = subprocess.run(
            ["adb", "-s", device_id, "get-state"],
            capture_output=True, text=True, timeout=5
        )
        connected = r.returncode == 0 and "device" in r.stdout
        return jsonify({"connected": connected, "device_id": device_id})
    except Exception as exc:
        return jsonify({"connected": False, "device_id": device_id, "error": str(exc)})



@app.route("/ivr/status")
def ivr_status():
    """Estado actual de la campaña."""
    global _ivr_campaign
    if not _ivr_campaign:
        return jsonify({"running": False, "processed": 0, "total": 0})
    return jsonify({
        "running":   _ivr_campaign.is_running,
        "processed": _ivr_campaign.processed,
        "total":     _ivr_campaign.total,
    })


@app.route("/ivr/results")
def ivr_results():
    """Descarga el CSV de resultados."""
    if not os.path.isfile(IVR_RESULTS_CSV):
        return jsonify({"ok": False, "error": "Sin resultados aún"}), 404
    from flask import send_file
    return send_file(IVR_RESULTS_CSV, as_attachment=True,
                     download_name="ivr_results.csv", mimetype="text/csv")


if __name__ == "__main__":
    print("=" * 55)
    print("  DTMF Analyzer - Servidor Web  (WebSocket activo)")
    print("  Abre: http://localhost:5050")
    print("=" * 55)
    socketio.run(app, host="0.0.0.0", port=5050, debug=False, allow_unsafe_werkzeug=True)
