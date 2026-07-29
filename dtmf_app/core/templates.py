# -*- coding: utf-8 -*-
"""
core/templates.py
=================
Sistema de plantillas de configuración IVR.

Cada plantilla guarda:
  - Modo de llamada (IVR / Puente / IVR+Puente)
  - Configuración de timers, opciones IVR
  - Rutas de audios copiados al directorio dtmf_app/templates/audio/
  - Configuración del puente de audio
  - Opciones de grabación y WhatsApp

Almacenamiento: JSON en dtmf_app/templates/<slug>.json
Audios:         dtmf_app/templates/audio/
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from typing import Any


# ── Directorio base de plantillas ───────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_APP_DIR   = os.path.dirname(_HERE)
TMPL_DIR   = os.path.join(_APP_DIR, "templates")
TMPL_AUDIO = os.path.join(TMPL_DIR, "audio")


def _ensure_dirs():
    os.makedirs(TMPL_DIR,   exist_ok=True)
    os.makedirs(TMPL_AUDIO, exist_ok=True)


def _slug(name: str) -> str:
    """Convierte el nombre a un slug seguro para nombre de archivo."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:60] or "plantilla"


def _copy_audio(src: str, slug: str, key: str) -> str | None:
    """
    Copia un archivo de audio al directorio de plantillas.
    Retorna la ruta destino o None si el origen no existe.
    """
    if not src or not os.path.isfile(src):
        return src   # puede ser None o ruta ya dentro de templates/
    _ensure_dirs()
    ext  = os.path.splitext(src)[1]
    dest = os.path.join(TMPL_AUDIO, f"{slug}__{key}{ext}")
    if os.path.abspath(src) != os.path.abspath(dest):
        shutil.copy2(src, dest)
    return dest


# ══════════════════════════════════════════════════════════════
#  TemplateManager
# ══════════════════════════════════════════════════════════════

class TemplateManager:
    """
    CRUD de plantillas de configuración IVR.

    Uso típico:
        tm = TemplateManager()
        tm.save("Mi Campaña IVR", config_dict)
        template = tm.load("mi_campana_ivr")
        names = tm.list_all()
        tm.delete("mi_campana_ivr")
    """

    def __init__(self):
        _ensure_dirs()

    # ── helpers ──────────────────────────────────────────────────

    def _path(self, slug: str) -> str:
        return os.path.join(TMPL_DIR, f"{slug}.json")

    def _read(self, slug: str) -> dict:
        p = self._path(slug)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"Plantilla '{slug}' no encontrada")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, slug: str, data: dict):
        _ensure_dirs()
        with open(self._path(slug), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── API pública ───────────────────────────────────────────────

    def list_all(self) -> list[dict]:
        """
        Lista todas las plantillas con metadatos básicos.
        Retorna lista de dicts: {slug, name, call_mode, updated_at}
        """
        _ensure_dirs()
        result = []
        for fn in sorted(os.listdir(TMPL_DIR)):
            if not fn.endswith(".json"):
                continue
            slug = fn[:-5]
            try:
                data = self._read(slug)
                result.append({
                    "slug":       slug,
                    "name":       data.get("name", slug),
                    "call_mode":  data.get("call_mode", "ivr"),
                    "updated_at": data.get("updated_at", ""),
                    "dialing_mode": data.get("dialing_mode", "auto"),
                })
            except Exception:
                pass
        return result

    def save(self, name: str, config: dict) -> str:
        """
        Guarda una plantilla con la configuración completa.
        Copia los archivos de audio al directorio de plantillas.
        Retorna el slug generado.
        """
        slug = _slug(name)
        now  = datetime.now().isoformat(timespec="seconds")

        # Copiar audios al directorio de plantillas
        audio_keys = ["audio_welcome", "audio_menu", "audio_bye", "audio_no_tone"]
        config_out = dict(config)
        for key in audio_keys:
            src = config_out.get(key)
            if src:
                config_out[key] = _copy_audio(src, slug, key)

        # Copiar audios de opciones IVR (por cada opción puede haber audio_bye)
        ivr_options = config_out.get("ivr_options", {})
        if isinstance(ivr_options, dict):
            new_opts: dict[str, Any] = {}
            for digit, val in ivr_options.items():
                if isinstance(val, dict) and val.get("audio_bye"):
                    copied = _copy_audio(val["audio_bye"], slug, f"opt_{digit}_bye")
                    new_opts[digit] = {**val, "audio_bye": copied}
                else:
                    new_opts[digit] = val
            config_out["ivr_options"] = new_opts

        data = {
            "name":         name,
            "slug":         slug,
            "created_at":   config_out.pop("created_at", now),
            "updated_at":   now,
            **config_out,
        }
        self._write(slug, data)
        return slug

    def load(self, slug: str) -> dict:
        """
        Carga una plantilla por slug.
        Verifica que los archivos de audio referenciados existen.
        """
        data = self._read(slug)

        # Verificar audios — marcar los que no existen
        audio_keys = ["audio_welcome", "audio_menu", "audio_bye", "audio_no_tone"]
        missing = []
        for key in audio_keys:
            path = data.get(key)
            if path and not os.path.isfile(path):
                missing.append(key)
                data[key] = None
        if missing:
            data["_missing_audio"] = missing

        return data

    def delete(self, slug: str) -> bool:
        """
        Elimina una plantilla y sus audios exclusivos.
        Retorna True si fue eliminada.
        """
        p = self._path(slug)
        if not os.path.isfile(p):
            return False

        # Leer para obtener rutas de audios
        try:
            data = self._read(slug)
            audio_keys = ["audio_welcome", "audio_menu", "audio_bye", "audio_no_tone"]
            for key in audio_keys:
                path = data.get(key)
                if path and os.path.isfile(path):
                    # Solo borrar si está dentro del directorio de plantillas
                    if os.path.abspath(path).startswith(os.path.abspath(TMPL_AUDIO)):
                        # Verificar que ninguna otra plantilla lo usa
                        in_use = any(
                            other.get(key) == path
                            for s, other in [
                                (s, self._read(s))
                                for s in [
                                    fn[:-5] for fn in os.listdir(TMPL_DIR)
                                    if fn.endswith(".json") and fn != f"{slug}.json"
                                ]
                            ]
                        )
                        if not in_use:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
        except Exception:
            pass

        os.remove(p)
        return True

    def exists(self, slug: str) -> bool:
        return os.path.isfile(self._path(slug))

    def rename(self, old_slug: str, new_name: str) -> str:
        """Renombra una plantilla (slug nuevo)."""
        data = self._read(old_slug)
        new_slug = _slug(new_name)
        data["name"]       = new_name
        data["slug"]       = new_slug
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(new_slug, data)
        if new_slug != old_slug:
            os.remove(self._path(old_slug))
        return new_slug


# ── Instancia global ─────────────────────────────────────────────
template_manager = TemplateManager()
