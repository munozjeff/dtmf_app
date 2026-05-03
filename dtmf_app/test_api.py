# -*- coding: utf-8 -*-
import requests, json

url = 'http://localhost:5050/analyze'
audio_path = '../Grabacion7.m4a'

print("Enviando Grabacion7.m4a al endpoint /analyze ...")
with open(audio_path, 'rb') as f:
    r = requests.post(url, files={'audio': ('Grabacion7.m4a', f, 'audio/mp4')})

d = r.json()
if d.get('error'):
    print("ERROR:", d['error'])
else:
    print(f"Status: {r.status_code}  OK")
    print(f"Archivo: {d['filename']}")
    print(f"Duracion: {d['duration_s']}s")
    print(f"Tonos detectados: {len(d['tones'])}")
    print(f"Secuencia: {d['sequence']}")
    print(f"Chart incluido: {'Si' if d.get('chart') else 'No'}")
    print()
    for t in d['tones']:
        print(f"  [{t['digit']}]  {t['start_s']}s - {t['end_s']}s  ({t['duration_ms']}ms)")

print()
print("--- Probando grabacion5.m4a ---")
with open('../grabacion5.m4a', 'rb') as f:
    r2 = requests.post(url, files={'audio': ('grabacion5.m4a', f, 'audio/mp4')})

d2 = r2.json()
if d2.get('error'):
    print("ERROR:", d2['error'])
else:
    print(f"Duracion: {d2['duration_s']}s  |  Tonos: {len(d2['tones'])}  |  Secuencia: {d2['sequence']}")
    for t in d2['tones']:
        print(f"  [{t['digit']}]  {t['start_s']}s - {t['end_s']}s  ({t['duration_ms']}ms)")
