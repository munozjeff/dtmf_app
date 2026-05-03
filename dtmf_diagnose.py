# -*- coding: utf-8 -*-
"""
Script de diagnostico: muestra la potencia Goertzel real del audio
en cada frecuencia DTMF para calibrar umbrales.
"""
import os, sys
import numpy as np
from math import gcd
from scipy.signal import resample_poly
from pydub import AudioSegment
import noisereduce as nr
from scipy.signal import butter, sosfilt

# --- ffmpeg path ---
_FFMPEG = (
    r"C:\Users\Milton\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1-full_build\bin\ffmpeg.exe"
)
if os.path.isfile(_FFMPEG):
    _bin = os.path.dirname(_FFMPEG)
    os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
    AudioSegment.converter = _FFMPEG
    AudioSegment.ffmpeg    = _FFMPEG
    AudioSegment.ffprobe   = _FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")

# --- cargar audio (WAV pre-convertido con ffmpeg) ---
import soundfile as sf
AUDIO = os.path.join(os.path.dirname(__file__), "audio_raw.wav")
audio, sr = sf.read(AUDIO)
if audio.ndim > 1:
    audio = audio.mean(axis=1)
audio = audio.astype(np.float32)
print(f"Cargado: {len(audio)} muestras, sr={sr}, duracion={len(audio)/sr:.2f}s")

# --- bandpass 300-3400 Hz ---
nyq = sr/2
sos = butter(4, [300/nyq, 3400/nyq], btype='band', output='sos')
audio = sosfilt(sos, audio).astype(np.float32)

# --- reducir ruido ---
nc_len = max(int(0.1 * len(audio)), sr//2)
audio = nr.reduce_noise(y=audio, y_noise=audio[:nc_len], sr=sr,
                        stationary=False, prop_decrease=0.85).astype(np.float32)

# --- amplificar 12 dB ---
g = 10 ** (12/20)
audio = (audio * g)
pk = np.max(np.abs(audio))
if pk > 1.0:
    audio /= pk

print(f"Post-procesado: pico={np.max(np.abs(audio)):.4f}, energia_media={np.mean(audio**2):.6f}")

# --- Goertzel ---
ROW_FREQS = [697, 770, 852, 941]
COL_FREQS = [1209, 1336, 1477, 1633]
ALL = ROW_FREQS + COL_FREQS

def goertzel(samples, freq, sr):
    N = len(samples)
    k = int(0.5 + N * freq / sr)
    omega = 2*np.pi*k/N
    coeff = 2*np.cos(omega)
    s1 = s2 = 0.0
    for x in samples:
        s = x + coeff*s1 - s2
        s2 = s1; s1 = s
    return (s2**2 + s1**2 - coeff*s1*s2) / (N*N)

frame_size = int(sr * 25/1000)  # 25 ms
hop_size   = int(sr * 10/1000)  # 10 ms
total_frames = (len(audio) - frame_size) // hop_size + 1

max_powers   = {f: 0.0 for f in ALL}
frame_energies = []
active_count = 0

DTMF_MAP = {
    (697,1209):"1",(697,1336):"2",(697,1477):"3",(697,1633):"A",
    (770,1209):"4",(770,1336):"5",(770,1477):"6",(770,1633):"B",
    (852,1209):"7",(852,1336):"8",(852,1477):"9",(852,1633):"C",
    (941,1209):"*",(941,1336):"0",(941,1477):"#",(941,1633):"D",
}

print(f"\nAnalizando {total_frames} frames...")
detections = []

for i in range(total_frames):
    start = i * hop_size
    frame = audio[start:start+frame_size]
    energy = np.mean(frame**2)
    frame_energies.append(energy)

    if energy < 1e-6:
        continue
    active_count += 1

    pows = {f: goertzel(frame, f, sr) for f in ALL}
    for f, p in pows.items():
        if p > max_powers[f]:
            max_powers[f] = p

    row_p = {f: pows[f] for f in ROW_FREQS}
    col_p = {f: pows[f] for f in COL_FREQS}
    br = max(row_p, key=row_p.get)
    bc = max(col_p, key=col_p.get)

    total = sum(pows.values())
    row_dom = row_p[br] / (sum(row_p.values())+1e-12)
    col_dom = col_p[bc] / (sum(col_p.values())+1e-12)
    dom_total = (row_p[br] + col_p[bc]) / (total + 1e-12)

    detections.append({
        "t": i*10/1000,
        "energy": energy,
        "best_row": br,
        "best_col": bc,
        "row_dom": row_dom,
        "col_dom": col_dom,
        "dom_total": dom_total,
        "digit": DTMF_MAP.get((br, bc), "?"),
        "total_power": total,
    })

print(f"\nFrames activos (energia>1e-6): {active_count}/{total_frames}")
print(f"Energia maxima: {max(frame_energies):.6f}")
print(f"Energia media:  {np.mean(frame_energies):.6f}")

print("\n--- Potencia maxima por frecuencia DTMF ---")
for f in ALL:
    tag = "ROW" if f in ROW_FREQS else "COL"
    print(f"  [{tag}] {f:4d} Hz: {max_powers[f]:.8f}")

if detections:
    print("\n--- Top 20 frames por dominancia total ---")
    top = sorted(detections, key=lambda x: x["dom_total"], reverse=True)[:20]
    print(f"  {'t(s)':>6} | {'energy':>10} | {'row_dom':>8} | {'col_dom':>8} | {'dom_tot':>8} | digit")
    print("  " + "-"*65)
    for d in top:
        print(f"  {d['t']:6.3f} | {d['energy']:10.6f} | {d['row_dom']:8.4f} | {d['col_dom']:8.4f} | {d['dom_total']:8.4f} | {d['digit']}  ({d['best_row']}Hz + {d['best_col']}Hz)")

    print("\n--- Distribucion de digitos en frames activos ---")
    from collections import Counter
    cnt = Counter(d["digit"] for d in detections)
    for digit, count in cnt.most_common():
        print(f"  '{digit}': {count} frames")
