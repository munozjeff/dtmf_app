# -*- coding: utf-8 -*-
"""
Prueba de las dos mejores tecnicas para separar DTMF de voz:

1. CONCENTRACION ESPECTRAL DTMF
   Para una senal DTMF pura: 2*(P_fila + P_col) / energia_frame ≈ 1.0
   Para voz: la energia se distribuye en cientos de frecuencias → ratio << 0.3

2. ESTACIONARIEDAD INTRA-FRAME
   DTMF es una senal estacionaria: el mismo digito debe aparecer en 3 sub-frames.
   Voz es no-estacionaria: los sub-frames no coinciden.

Probamos con Grabacion7.wav (contiene todos los tonos).
"""
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt
import noisereduce as nr

# ── Cargar y preprocesar ──────────────────────────────────────
audio, sr = sf.read('grabacion7.wav')
audio = audio.astype(np.float32)
nyq = sr / 2
sos = butter(4, [300/nyq, 3400/nyq], btype='band', output='sos')
audio = sosfilt(sos, audio).astype(np.float32)
nc = audio[:sr//2]
audio = nr.reduce_noise(y=audio, y_noise=nc, sr=sr,
                        stationary=False, prop_decrease=0.85).astype(np.float32)
g = 10**(30/20); audio = audio * g
pk = np.max(np.abs(audio))
if pk > 1: audio /= pk

print(f"Audio: {len(audio)/sr:.2f}s  sr={sr}  pico={np.max(np.abs(audio)):.4f}")

ROW_FREQS = [697, 770, 852, 941]
COL_FREQS = [1209, 1336, 1477, 1633]
DTMF_MAP  = {
    (697,1209):'1',(697,1336):'2',(697,1477):'3',(697,1633):'A',
    (770,1209):'4',(770,1336):'5',(770,1477):'6',(770,1633):'B',
    (852,1209):'7',(852,1336):'8',(852,1477):'9',(852,1633):'C',
    (941,1209):'*',(941,1336):'0',(941,1477):'#',(941,1633):'D',
}

def goertzel(s, f, sr):
    N = len(s); k = int(0.5 + N*f/sr)
    w = 2*np.pi*k/N; c = 2*np.cos(w); s1 = s2 = 0.0
    for x in s:
        tmp = x + c*s1 - s2; s2 = s1; s1 = tmp
    return (s2**2 + s1**2 - c*s1*s2) / (N*N)

FRAME_MS = 40; HOP_MS = 10
FRAME = int(sr * FRAME_MS / 1000)   # 320 muestras
HOP   = int(sr * HOP_MS   / 1000)   # 80 muestras
total = (len(audio) - FRAME) // HOP + 1

# ── Pre-calcular todo ──────────────────────────────────────────
print(f"Pre-calculando {total} frames...")
fes  = []
rpow = []
cpow = []
for i in range(total):
    start = i * HOP
    fr = audio[start:start+FRAME]
    e  = float(np.mean(fr**2))
    fes.append(e)
    if e > 5e-7:
        rp = {f: goertzel(fr, f, sr) for f in ROW_FREQS}
        cp = {f: goertzel(fr, f, sr) for f in COL_FREQS}
    else:
        rp = {f: 0.0 for f in ROW_FREQS}
        cp = {f: 0.0 for f in COL_FREQS}
    rpow.append(rp); cpow.append(cp)

act = [e for e in fes if e > 5e-7]
med = float(np.median(act)) if act else 5e-7
print(f"Mediana energia activa: {med:.2e}\n")

# ── Mostrar concentracion espectral de los mejores candidatos ──
print("=== CONCENTRACION ESPECTRAL de los 30 frames con mayor dom_total ===")
print(f"{'t(s)':>6} {'energy':>10} {'row_dom':>8} {'col_dom':>8} {'dom_tot':>8} {'concentr':>9} {'digit'}")
print("-"*75)
raw = []
for i,(rp,cp) in enumerate(zip(rpow, cpow)):
    e = fes[i]
    if e < 5e-7: continue
    tot_p = sum(rp.values()) + sum(cp.values())
    if tot_p < 1e-14: continue
    br = max(rp, key=rp.get); bc = max(cp, key=cp.get)
    rd = rp[br] / (sum(rp.values()) + 1e-14)
    cd = cp[bc] / (sum(cp.values()) + 1e-14)
    dt = (rp[br] + cp[bc]) / (tot_p + 1e-14)
    # CONCENTRACION: cuanto de la energia total esta en las 2 freqs DTMF
    concentration = 2.0 * (rp[br] + cp[bc]) / (e + 1e-14)
    t = round(i * HOP_MS / 1000, 3)
    raw.append((t, e, rd, cd, dt, concentration, br, bc, DTMF_MAP.get((br,bc),'?')))

raw.sort(key=lambda x: -x[4])
for r in raw[:30]:
    t,e,rd,cd,dt,conc,br,bc,d = r
    print(f"{t:6.2f}  {e:10.2e}  {rd:8.4f}  {cd:8.4f}  {dt:8.4f}  {conc:9.4f}  {d} ({br}+{bc})")

# ── Detector mejorado con ambas tecnicas ──────────────────────
def detect_frame_v2(idx, row_thr, col_thr, tot_thr, conc_thr, stationarity):
    e  = fes[idx]
    rp = rpow[idx]; cp = cpow[idx]
    tot_p = sum(rp.values()) + sum(cp.values())
    if tot_p < 1e-14: return None

    br = max(rp, key=rp.get); bc = max(cp, key=cp.get)
    rd = rp[br] / (sum(rp.values()) + 1e-14)
    cd = cp[bc] / (sum(cp.values()) + 1e-14)
    dt = (rp[br] + cp[bc]) / (tot_p + 1e-14)

    if rd < row_thr or cd < col_thr or dt < tot_thr:
        return None

    # TECNICA 1: Concentracion espectral
    # 2*(P_row + P_col) / frame_energy ≈ 1.0 para DTMF puro
    concentration = 2.0 * (rp[br] + cp[bc]) / (e + 1e-14)
    if concentration < conc_thr:
        return None

    # TECNICA 2: Estacionariedad intra-frame
    # Dividir el frame en 3 sub-frames; todos deben votar el mismo digito
    if stationarity:
        start = idx * HOP
        fr    = audio[start:start+FRAME]
        sub_n = FRAME // 3
        votes = []
        for s in [0, sub_n, 2*sub_n]:
            sub = fr[s:s+sub_n]
            srp = {f: goertzel(sub, f, sr) for f in ROW_FREQS}
            scp = {f: goertzel(sub, f, sr) for f in COL_FREQS}
            sbr = max(srp, key=srp.get)
            sbc = max(scp, key=scp.get)
            votes.append((sbr, sbc))
        if len(set(votes)) > 1:   # sub-frames no coinciden → no es DTMF estacionario
            return None

    return DTMF_MAP.get((br, bc))

def analyze_v2(row_thr, col_thr, tot_thr, conc_thr, stationarity, min_ms):
    min_f = max(1, int(min_ms / HOP_MS))
    fl = []
    for i, e in enumerate(fes):
        t = round(i * HOP_MS / 1000, 3)
        if e < 5e-7:
            fl.append((t, None)); continue
        d = detect_frame_v2(i, row_thr, col_thr, tot_thr, conc_thr, stationarity)
        fl.append((t, d))

    tones = []; cd = None; cs = 0.0; con = 0
    for t, d in fl:
        if d and d == cd:
            con += 1
        else:
            if cd and con >= min_f:
                tones.append({'digit':cd,'start':cs,'end':t,'ms':round((t-cs)*1000)})
            cd = d; cs = t; con = 1 if d else 0
    if cd and con >= min_f:
        tones.append({'digit':cd,'start':cs,'end':fl[-1][0],'ms':round((fl[-1][0]-cs)*1000)})
    return tones

# ── Barrido de parametros ─────────────────────────────────────
print()
print("=== BARRIDO DE PARAMETROS CON NUEVAS TECNICAS ===\n")
print(f"{'n':>3} {'row':>5} {'col':>5} {'tot':>5} {'conc':>5} {'stat':>5} {'min':>4}  secuencia")
print("-"*75)

configs = []
for col_thr in [0.50, 0.60, 0.70]:
    for tot_thr in [0.82, 0.85, 0.90]:
        for conc_thr in [0.20, 0.30, 0.40, 0.50, 0.60]:
            for stat in [False, True]:
                for min_ms in [20, 30, 40]:
                    t = analyze_v2(0.78, col_thr, tot_thr, conc_thr, stat, min_ms)
                    seq = ''.join(x['digit'] for x in t)
                    configs.append((len(t), col_thr, tot_thr, conc_thr, stat, min_ms, seq))

configs.sort(key=lambda x: (-x[0], x[5]))
seen = set()
for n,col,tot,conc,stat,mn,seq in configs[:40]:
    if seq not in seen and n > 0:
        seen.add(seq)
        s = 'Si' if stat else 'No'
        print(f"{n:3d}  {col:.2f}  {tot:.2f}  {conc:.2f}  {conc:.2f}   {s:>5}  {mn:3d}ms  {seq}")

# ── Resultado detallado con los mejores params ─────────────────
best = max(configs, key=lambda x: (x[0], -x[5]))
n,col,tot,conc,stat,mn,seq = best
print()
print(f"=== MEJOR CONFIGURACION: row=0.78 col={col} tot={tot} conc={conc} stat={'Si' if stat else 'No'} min={mn}ms ===")
tones = analyze_v2(0.78, col, tot, conc, stat, mn)
print()
for i,t in enumerate(tones,1):
    print(f"  #{i:02d}  [{t['digit']}]  {t['start']:.3f}s - {t['end']:.3f}s  ({t['ms']}ms)")
print(f"\n  Secuencia detectada: {''.join(t['digit'] for t in tones)}")
print(f"  Total tonos: {len(tones)}")
print()
print("=== PARAMETROS FINALES PARA app.py ===")
print(f"ROW_DOM_THRESHOLD        = 0.78")
print(f"COL_DOM_THRESHOLD        = {col}")
print(f"TOTAL_DOM_THRESHOLD      = {tot}")
print(f"CONCENTRATION_THRESHOLD  = {conc}  # NUEVO: concentracion espectral DTMF")
print(f"STATIONARITY_CHECK       = {'True' if stat else 'False'}  # NUEVO: estacionariedad intra-frame")
print(f"MIN_TONE_MS              = {mn}")
