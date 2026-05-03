# -*- coding: utf-8 -*-
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt
import noisereduce as nr

ROW_FREQS = [697, 770, 852, 941]
COL_FREQS = [1209, 1336, 1477, 1633]
DTMF_MAP  = {
    (697,1209):'1',(697,1336):'2',(697,1477):'3',(697,1633):'A',
    (770,1209):'4',(770,1336):'5',(770,1477):'6',(770,1633):'B',
    (852,1209):'7',(852,1336):'8',(852,1477):'9',(852,1633):'C',
    (941,1209):'*',(941,1336):'0',(941,1477):'#',(941,1633):'D',
}
ROW_THR  = 0.78
COL_THR  = 0.50
TOT_THR  = 0.82
CONC_THR = 0.20
MIN_MS   = 20
FRAME_MS = 40
HOP_MS   = 10

def goertzel(s, f, sr):
    N = len(s); k = int(0.5 + N*f/sr)
    w = 2*np.pi*k/N; c = 2*np.cos(w); s1 = s2 = 0.0
    for x in s:
        tmp = x + c*s1 - s2; s2 = s1; s1 = tmp
    return (s2**2 + s1**2 - c*s1*s2) / (N*N)

def test_audio(wav_path, label):
    audio, sr = sf.read(wav_path)
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

    FRAME = int(sr * FRAME_MS / 1000)
    HOP   = int(sr * HOP_MS   / 1000)
    total = (len(audio) - FRAME) // HOP + 1
    min_f = max(1, int(MIN_MS / HOP_MS))

    tones = []; cur_d = None; cur_s = 0.0; con = 0
    for i in range(total):
        start = i * HOP
        fr    = audio[start:start+FRAME]
        e     = float(np.mean(fr**2))
        t     = round(i * HOP_MS / 1000, 3)

        if e < 5e-7:
            d = None
        else:
            rp = {f: goertzel(fr, f, sr) for f in ROW_FREQS}
            cp = {f: goertzel(fr, f, sr) for f in COL_FREQS}
            tp = sum(rp.values()) + sum(cp.values())
            if tp < 1e-14:
                d = None
            else:
                br = max(rp, key=rp.get); bc = max(cp, key=cp.get)
                rd   = rp[br] / (sum(rp.values()) + 1e-14)
                cd2  = cp[bc] / (sum(cp.values()) + 1e-14)
                dt   = (rp[br] + cp[bc]) / (tp + 1e-14)
                conc = 2.0 * (rp[br] + cp[bc]) / (e + 1e-14)
                if rd >= ROW_THR and cd2 >= COL_THR and dt >= TOT_THR and conc >= CONC_THR:
                    d = DTMF_MAP.get((br, bc))
                else:
                    d = None

        if d and d == cur_d:
            con += 1
        else:
            if cur_d and con >= min_f:
                tones.append({'d': cur_d, 's': cur_s, 'e': t,
                              'ms': round((t - cur_s)*1000)})
            cur_d = d; cur_s = t; con = 1 if d else 0

    if cur_d and con >= min_f:
        last_t = total * HOP_MS / 1000
        tones.append({'d': cur_d, 's': cur_s, 'e': last_t,
                      'ms': round((last_t - cur_s)*1000)})

    seq = ''.join(t['d'] for t in tones)
    print(f"  [{label}]")
    print(f"  Duracion: {len(audio)/sr:.2f}s  |  Tonos detectados: {len(tones)}")
    print(f"  Secuencia: {seq}")
    for i, t in enumerate(tones, 1):
        print(f"    #{i:02d} [{t['d']}]  {t['s']:.2f}s - {t['e']:.2f}s  ({t['ms']}ms)")

print("=" * 55)
print("  PRUEBA FINAL CON AMBOS AUDIOS")
print("  ROW=0.78  COL=0.50  TOT=0.82  CONC=0.20  MIN=20ms")
print("=" * 55)
print()
test_audio('grabacion5_converted.wav', 'grabacion5 (audio con voz + tonos)')
print()
test_audio('grabacion7.wav', 'grabacion7 (todos los digitos)')
