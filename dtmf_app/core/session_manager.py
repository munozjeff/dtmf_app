"""
dtmf_app/core/session_manager.py
──────────────────────────────────────────────────────────────────────
Registro central de todas las sesiones IVR activas.

Responsabilidades:
  - Crear / obtener / eliminar sesiones
  - Validar que no haya conflictos (mismo device_id o mismo canal de audio)
  - Proveer la lista de canales de audio ocupados para AudioChannelProber
  - API de control: start, stop, pause, resume por session_id
"""

from __future__ import annotations
import threading
from typing import Callable, Dict, Optional, Set

from .ivr_session import (
    IVRSession, SessionConfig,
    SESSION_IDLE, SESSION_READY, SESSION_RUNNING,
    SESSION_PAUSED, SESSION_DONE, SESSION_ERROR,
)


class SessionManager:
    """
    Singleton de gestión de sesiones IVR.

    Uso en app.py:
        from dtmf_app.core.session_manager import session_manager

        sid = session_manager.create(config, emit_fn)
        session_manager.start(sid)
        session_manager.stop(sid)
        sessions = session_manager.list_all()
    """

    def __init__(self):
        self._sessions: Dict[str, IVRSession] = {}
        self._lock = threading.Lock()

    # ── CRUD básico ───────────────────────────────────────────────

    def create(self,
               config: SessionConfig,
               emit_fn: Callable,
               label: Optional[str] = None) -> str:
        """
        Crea una nueva sesión y la registra.
        Devuelve el session_id asignado.
        """
        session = IVRSession(config=config, emit_fn=emit_fn)
        if label:
            session.label = label
        with self._lock:
            self._sessions[session.session_id] = session
        print(f"[SessionManager] Nueva sesión: {session.session_id} — {config.device_id}")
        return session.session_id

    def get(self, session_id: str) -> Optional[IVRSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> bool:
        """Elimina una sesión (solo si no está corriendo)."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.is_active:
                print(f"[SessionManager] No se puede eliminar sesión activa: {session_id}")
                return False
            del self._sessions[session_id]
        print(f"[SessionManager] Sesión eliminada: {session_id}")
        return True

    def list_all(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]

    # ── Control de sesión ─────────────────────────────────────────

    def start(self, session_id: str, campaign_factory: Callable) -> bool:
        """
        Lanza la campaña de la sesión.

        Args:
            session_id:       ID de la sesión.
            campaign_factory: callable(session) → IVRCampaign o ManualCallSession.
                              El factory recibe la sesión y debe devolver
                              el objeto de campaña ya configurado pero SIN iniciar.
        """
        session = self.get(session_id)
        if session is None:
            print(f"[SessionManager] start: sesión {session_id} no encontrada")
            return False

        if session.is_active:
            print(f"[SessionManager] Sesión {session_id} ya está activa")
            return False

        try:
            campaign = campaign_factory(session)
            session.campaign = campaign
            session.total    = len(session.config.numbers) if session.config.numbers else 1
            session.set_status(SESSION_RUNNING)
            campaign.start()
            print(f"[SessionManager] Campaña iniciada: {session_id}")
            return True
        except Exception as exc:
            print(f"[SessionManager] Error iniciando campaña {session_id}: {exc}")
            session.set_status(SESSION_ERROR)
            return False

    def stop(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False

        # Detener campaña
        if session.campaign is not None:
            try:
                if hasattr(session.campaign, "stop"):
                    session.campaign.stop()
                elif hasattr(session.campaign, "hangup"):
                    session.campaign.hangup()
            except Exception as exc:
                print(f"[SessionManager] Error deteniendo campaña {session_id}: {exc}")

        # Detener watchdog
        if session.watchdog is not None:
            try:
                session.watchdog.stop()
            except Exception:
                pass

        session.set_status(SESSION_DONE)
        return True

    def pause(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.campaign and hasattr(session.campaign, "pause"):
            session.campaign.pause()
            session.set_status(SESSION_PAUSED)
            return True
        return False

    def resume(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.campaign and hasattr(session.campaign, "resume"):
            session.campaign.resume()
            session.set_status(SESSION_RUNNING)
            return True
        return False

    # ── Utilidades ────────────────────────────────────────────────

    def occupied_inputs(self) -> Set[int]:
        """Devuelve los índices de canal de entrada ya en uso."""
        occupied = set()
        with self._lock:
            for s in self._sessions.values():
                if s.is_active and s.config.audio_in_idx is not None:
                    occupied.add(s.config.audio_in_idx)
        return occupied

    def occupied_outputs(self) -> Set[int]:
        """Devuelve los índices de canal de salida ya en uso."""
        occupied = set()
        with self._lock:
            for s in self._sessions.values():
                if s.is_active and s.config.audio_out_idx is not None:
                    occupied.add(s.config.audio_out_idx)
        return occupied

    def occupied_devices(self) -> Set[str]:
        """Devuelve los device_id ADB ya en uso."""
        occupied = set()
        with self._lock:
            for s in self._sessions.values():
                if s.is_active and s.config.device_id:
                    occupied.add(s.config.device_id)
        return occupied

    def validate_config(self, config: SessionConfig) -> "list[str]":
        """
        Valida que una nueva configuración no entre en conflicto con las sesiones activas.
        Devuelve lista de errores (vacía = válida).
        """
        errors: list[str] = []

        if config.device_id in self.occupied_devices():
            errors.append(f"El dispositivo ADB '{config.device_id}' ya está en uso por otra sesión activa.")

        if config.audio_in_idx is not None and config.audio_in_idx in self.occupied_inputs():
            errors.append(f"El canal de entrada {config.audio_in_idx} ya está en uso por otra sesión activa.")

        if config.audio_out_idx is not None and config.audio_out_idx in self.occupied_outputs():
            errors.append(f"El canal de salida {config.audio_out_idx} ya está en uso por otra sesión activa.")

        return errors


# Instancia singleton compartida por toda la app
session_manager = SessionManager()
