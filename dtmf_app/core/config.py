# -*- coding: utf-8 -*-
"""
dtmf_app/core/config.py
========================
Fuente única de verdad para todos los parámetros del pipeline DTMF y del IVR.
Importar desde aquí; nunca duplicar valores en otros módulos.
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────
# Audio — Frecuencias objetivo
# ──────────────────────────────────────────────────────────────

TARGET_SR: int = 8000          # Hz — frecuencia de muestreo objetivo (estándar telefónico)

# ──────────────────────────────────────────────────────────────
# Tabla DTMF ITU-T Q.23
# Cada tecla genera la suma de una frecuencia de fila + una de columna
# ──────────────────────────────────────────────────────────────

DTMF_MAP: dict[tuple[int, int], str] = {
    (697, 1209): "1", (697, 1336): "2", (697, 1477): "3", (697, 1633): "A",
    (770, 1209): "4", (770, 1336): "5", (770, 1477): "6", (770, 1633): "B",
    (852, 1209): "7", (852, 1336): "8", (852, 1477): "9", (852, 1633): "C",
    (941, 1209): "*", (941, 1336): "0", (941, 1477): "#", (941, 1633): "D",
}

ROW_FREQS: list[int] = [697, 770, 852, 941]       # Hz — frecuencias de fila
COL_FREQS: list[int] = [1209, 1336, 1477, 1633]   # Hz — frecuencias de columna

# Mapa inverso: dígito → (f_fila, f_col)  — para sintetizar tono DTMF limpio
DTMF_DIGIT_FREQS: dict[str, tuple[int, int]] = {v: k for k, v in DTMF_MAP.items()}

# ──────────────────────────────────────────────────────────────
# Pipeline de análisis DTMF
# ──────────────────────────────────────────────────────────────

FRAME_MS: int          = 40      # ms — duración de cada frame de análisis
HOP_MS: int            = 10      # ms — desplazamiento entre frames (solapamiento del 75%)
MIN_TONE_MS: int       = 20      # ms — duración mínima para validar un tono (2 frames)
ENERGY_THRESHOLD: float = 5e-7   # umbral de silencio absoluto (RMS²); frames más bajos se ignoran
AMPLIFY_DB: float      = 30.0    # dB — ganancia aplicada antes del análisis

# ──────────────────────────────────────────────────────────────
# Umbrales de dominancia espectral DTMF
# Calibrados con Grabacion7.wav (10 dígitos reales registrados):
#   row_dom  en tonos reales: 0.97–0.999 → umbral 0.78 (amplio margen)
#   col_dom  en tonos reales: 0.99–0.999 → umbral 0.50
#   dom_total en reales:      0.99–0.999 → umbral 0.82
# ──────────────────────────────────────────────────────────────

ROW_DOM_THRESHOLD: float   = 0.78
COL_DOM_THRESHOLD: float   = 0.50
TOTAL_DOM_THRESHOLD: float = 0.82

# ──────────────────────────────────────────────────────────────
# Concentración espectral DTMF
# 2*(P_fila + P_col) / energía_frame:
#   DTMF real → 0.54–0.86  (casi toda la energía en 2 frecuencias)
#   Voz/ruido → < 0.10      (energía dispersa en todo el espectro)
# ──────────────────────────────────────────────────────────────

CONCENTRATION_THRESHOLD: float = 0.15   # permisivo para captación via micrófono ambiente

# ──────────────────────────────────────────────────────────────
# Colores por dígito DTMF (para visualizaciones matplotlib)
# ──────────────────────────────────────────────────────────────

DIGIT_COLORS: dict[str, str] = {
    "1": "#4fc3f7", "2": "#81c784", "3": "#ffb74d", "4": "#ba68c8",
    "5": "#f06292", "6": "#4dd0e1", "7": "#aed581", "8": "#ff8a65",
    "9": "#90caf9", "0": "#a5d6a7", "*": "#ffe082", "#": "#ef9a9a",
    "A": "#b39ddb", "B": "#80cbc4", "C": "#ffcc02", "D": "#ff7043",
}

# ──────────────────────────────────────────────────────────────
# Pre-Call Audio Analyzer (detección de ring + voz de operador)
# ──────────────────────────────────────────────────────────────

PRECALL_FRAME_MS: int         = 100        # ms por ventana de análisis pre-llamada
RING_FREQS: list[int]         = [400, 425, 440, 450]  # Hz — Colombia: ~425 Hz
RING_E_THR: float             = 5e-5       # energía mínima para procesar frame
FLAT_TONE: float              = 0.12       # spectral flatness ≤ → frame tonal (ring)
FLAT_VOICE: float             = 0.22       # spectral flatness ≥ → frame broadband (voz)
ZCR_VOICE: float              = 0.07       # ZCR normalizado ≥ → componente vocal
RING_ON_MIN: float            = 0.7        # s — duración mínima burst de ring
RING_ON_MAX: float            = 3.2        # s — duración máxima burst de ring
RING_OFF_MIN: float           = 1.2        # s — silencio mínimo entre rings
VOICE_SUSTAINED_MIN: float    = 2.0        # s — segundos de voz continua → operador
ENERGY_THR_SIGNAL: float      = 8e-3       # RMS mínimo para "hay señal"
ENERGY_SUSTAINED_MIN: float   = 2.0        # s — señal sostenida → operador
MAX_RINGS: int                = 2          # límite de rings para clasificar como UNAVAILABLE

# ──────────────────────────────────────────────────────────────
# IVR Campaign — Valores por defecto
# ──────────────────────────────────────────────────────────────

IVR_DEFAULT_DELAY_S: float    = 5.0    # s entre llamadas consecutivas
IVR_DEFAULT_TONE_TIMEOUT: float = 10.0 # s esperando tono DTMF del cliente
IVR_DEFAULT_MENU_REPEATS: int = 2      # veces que se repite el menú si no hay tono
IVR_DIAL_TIMEOUT: float       = 60.0   # s máx esperando que la llamada entre en ACTIVE
IVR_MIN_DIALING_SECS: float   = 15.0   # s en DIALING para confirmar que timbró
IVR_POST_ACTIVE_LISTEN: float = 4.5    # s extra post-ACTIVE escuchando operador

# ──────────────────────────────────────────────────────────────
# ADB Watchdog
# ──────────────────────────────────────────────────────────────

ADB_WATCHDOG_INTERVAL: float  = 3.0   # s entre verificaciones de conexión ADB

# ──────────────────────────────────────────────────────────────
# Audio Monitor (PythonAudioMonitor)
# ──────────────────────────────────────────────────────────────

MONITOR_WINDOW_MS: float = 80.0   # ms — ventana deslizante de análisis
MONITOR_HOP_MS: float    = 20.0   # ms — hop del monitor en tiempo real
MONITOR_VIZ_HZ: float    = 15.0   # Hz — tasa de emisión de audio_viz

# ──────────────────────────────────────────────────────────────
# CallMonitor (logcat)
# ──────────────────────────────────────────────────────────────

# Tags de logcat que contienen eventos de estado de llamada.
# Distintas versiones de Android usan distintos tags.
TELECOM_LOGCAT_TAGS: list[str] = [
    "Telecom",
    "TelecomFramework",
    "CallsManager",
    "CallStateMachine",
]
CALL_MONITOR_TIMEOUT_S: float = 90.0  # s sin eventos → asumir llamada perdida

# ──────────────────────────────────────────────────────────────
# Modos de llamada
# ──────────────────────────────────────────────────────────────

CALL_MODE_IVR:       str = "ivr"        # IVR clásico: audio pregrabado + DTMF
CALL_MODE_BRIDGE:    str = "bridge"     # Puente directo: audio bidireccional desde que contesta
CALL_MODE_IVR_BRIDGE:str = "ivr_bridge" # IVR hasta dígito trigger → luego activa puente

# Dígito DTMF por defecto que activa el puente en modo ivr_bridge
BRIDGE_TRIGGER_DIGIT_DEFAULT: str = "0"

# ──────────────────────────────────────────────────────────────
# Audio Bridge — Parámetros por defecto
# ──────────────────────────────────────────────────────────────

BRIDGE_BLOCK_MS: int    = 40    # ms — tamaño de bloque (latencia ≈ 2×BLOCK_MS)
BRIDGE_GAIN_IN: float   = 1.0   # ganancia audio teléfono → auriculares
BRIDGE_GAIN_OUT: float  = 1.0   # ganancia micrófono PC  → teléfono
