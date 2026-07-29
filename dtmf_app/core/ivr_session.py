"""
dtmf_app/core/ivr_session.py
──────────────────────────────────────────────────────────────────────
Encapsula el estado completo de una sesión de automatización IVR.

Cada sesión tiene su propio:
  - Dispositivo ADB (device_id)
  - Canal de audio entrada (audio_in_idx)
  - Canal de audio salida (audio_out_idx)
  - AudioPlayer (reproducción en su canal)
  - Monitor DTMF (escucha en su canal)
  - Campaña IVR (IVRCampaign o ManualCallSession)
  - Log en tiempo real (emit a UI con session_id)

El SessionManager gestiona el ciclo de vida de N sesiones.
"""

from __future__ import annotations
import uuid
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

# Estados posibles de una sesión
SESSION_IDLE    = "IDLE"
SESSION_PROBING = "PROBING"     # auto-detección de canal en curso
SESSION_READY   = "READY"       # canal detectado, lista para lanzar
SESSION_RUNNING = "RUNNING"     # campaña/llamada activa
SESSION_PAUSED  = "PAUSED"
SESSION_DONE    = "DONE"
SESSION_ERROR   = "ERROR"


@dataclass
class SessionConfig:
    """Toda la configuración de una sesión (IVR + audio + dispositivo)."""

    # Dispositivo ADB
    device_id: str = ""

    # Canales de audio (None = detectar automáticamente)
    audio_in_idx:  Optional[int] = None
    audio_out_idx: Optional[int] = None
    audio_out_name: Optional[str] = None   # nombre para referencia

    # Config IVR
    numbers:       list     = field(default_factory=list)
    delay_seconds: float    = 5.0
    audio_welcome: Optional[str] = None
    audio_menu:    Optional[str] = None
    audio_bye:     Optional[str] = None
    audio_no_tone: Optional[str] = None
    ivr_options:   dict     = field(default_factory=dict)
    tone_timeout:  float    = 10.0
    menu_repeats:  int      = 2
    record_calls:  bool     = False
    is_test:       bool     = False

    def to_campaign_config(self) -> dict:
        """Convierte a dict compatible con IVRCampaign.__init__."""
        return {
            "numbers":       self.numbers,
            "device_id":     self.device_id,
            "delay_seconds": self.delay_seconds,
            "audio_welcome": self.audio_welcome,
            "audio_menu":    self.audio_menu,
            "audio_bye":     self.audio_bye,
            "audio_no_tone": self.audio_no_tone,
            "ivr_options":   self.ivr_options,
            "tone_timeout":  self.tone_timeout,
            "menu_repeats":  self.menu_repeats,
            "record_calls":  self.record_calls,
            "is_test":       self.is_test,
        }


class IVRSession:
    """
    Contenedor de una sesión de automatización IVR.

    Cada sesión tiene un ID único y referencias a sus objetos de
    audio y campaña. El SessionManager los crea y destruye.
    """

    def __init__(self,
                 session_id: Optional[str] = None,
                 config: Optional[SessionConfig] = None,
                 emit_fn: Optional[Callable] = None):
        """
        Args:
            session_id: ID único (se genera si no se pasa).
            config:     Configuración de la sesión.
            emit_fn:    Función socketio.emit(event, data) para notificar UI.
        """
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.config     = config or SessionConfig()
        self._emit      = emit_fn or (lambda ev, d: None)

        self.status     = SESSION_IDLE
        self.created_at = time.time()
        self.label      = f"Sesión {self.session_id}"   # nombre legible

        # Objetos de infraestructura (asignados en setup/start)
        self.campaign   = None    # IVRCampaign | ManualCallSession
        self.watchdog   = None    # ADBWatchdog
        self.prober     = None    # AudioChannelProber

        # Progreso
        self.processed  = 0
        self.total      = 0
        self.last_number: Optional[str] = None
        self.last_result: Optional[str] = None

        # Log en memoria (últimas N líneas)
        self._log_buffer: list[dict] = []
        self._log_lock   = threading.Lock()
        self._MAX_LOG    = 200

    # ── Estado ────────────────────────────────────────────────────

    def set_status(self, status: str):
        self.status = status
        self.emit("session_status", {
            "session_id": self.session_id,
            "status":     status,
            "label":      self.label,
        })

    # ── Log ───────────────────────────────────────────────────────

    def log(self, msg: str, level: str = "info"):
        entry = {"msg": msg, "level": level, "ts": time.time(),
                 "session_id": self.session_id}
        with self._log_lock:
            self._log_buffer.append(entry)
            if len(self._log_buffer) > self._MAX_LOG:
                self._log_buffer.pop(0)
        self.emit("session_log", entry)

    def get_log(self) -> list[dict]:
        with self._log_lock:
            return list(self._log_buffer)

    # ── Socket.IO ─────────────────────────────────────────────────

    def emit(self, event: str, data: dict):
        """Emite un evento a la UI con session_id incluido."""
        if "session_id" not in data:
            data = {**data, "session_id": self.session_id}
        self._emit(event, data)

    # ── Resumen serializable ──────────────────────────────────────

    def to_dict(self) -> dict:
        cfg = self.config
        return {
            "session_id":   self.session_id,
            "label":        self.label,
            "status":       self.status,
            "created_at":   self.created_at,
            "device_id":    cfg.device_id,
            "audio_in_idx": cfg.audio_in_idx,
            "audio_out_idx": cfg.audio_out_idx,
            "audio_out_name": cfg.audio_out_name,
            "processed":    self.processed,
            "total":        self.total,
            "last_number":  self.last_number,
            "last_result":  self.last_result,
            "numbers":      cfg.numbers,
            "delay_seconds": cfg.delay_seconds,
            "tone_timeout": cfg.tone_timeout,
            "menu_repeats": cfg.menu_repeats,
            "record_calls": cfg.record_calls,
            "is_test":      cfg.is_test,
        }

    @property
    def is_running(self) -> bool:
        return self.status == SESSION_RUNNING

    @property
    def is_active(self) -> bool:
        return self.status in (SESSION_RUNNING, SESSION_PAUSED, SESSION_PROBING)
