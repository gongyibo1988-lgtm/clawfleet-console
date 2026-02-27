from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from app.config import SecurityConfig


@dataclass
class SessionInfo:
    session_id: str
    username: str
    csrf_token: str
    expires_at: float


class SecurityManager:
    def __init__(self, config: SecurityConfig) -> None:
        self.config = config
        self._sessions: dict[str, SessionInfo] = {}
        self._confirm_tickets: dict[str, float] = {}

    def refresh_config(self, config: SecurityConfig) -> None:
        self.config = config
        self._sessions.clear()
        self._confirm_tickets.clear()

    def authenticate_credentials(self, username: str, password: str) -> bool:
        if not self.config.enable_auth:
            return True
        return username == self.config.username and password == self.config.password

    def create_session(self, username: str) -> SessionInfo:
        now = time.time()
        session = SessionInfo(
            session_id=secrets.token_urlsafe(24),
            username=username,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=now + self.config.session_ttl_seconds,
        )
        self._sessions[session.session_id] = session
        self._prune()
        return session

    def get_session(self, session_id: str | None) -> SessionInfo | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= time.time():
            self._sessions.pop(session_id, None)
            return None
        return session

    def remove_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._sessions.pop(session_id, None)

    def validate_csrf(self, session_id: str | None, csrf_token: str | None) -> bool:
        if not self.config.enable_auth:
            return True
        session = self.get_session(session_id)
        if session is None:
            return False
        return bool(csrf_token) and secrets.compare_digest(session.csrf_token, csrf_token)

    def create_confirm_ticket(self, code: str) -> str:
        if not secrets.compare_digest(code or "", self.config.operation_confirm_code):
            raise ValueError("invalid confirm code")
        ticket = secrets.token_urlsafe(18)
        self._confirm_tickets[ticket] = time.time() + self.config.confirm_ttl_seconds
        self._prune()
        return ticket

    def consume_confirm_ticket(self, ticket: str | None) -> bool:
        if not ticket:
            return False
        expires_at = self._confirm_tickets.get(ticket)
        if expires_at is None:
            return False
        if expires_at <= time.time():
            self._confirm_tickets.pop(ticket, None)
            return False
        self._confirm_tickets.pop(ticket, None)
        return True

    def _prune(self) -> None:
        now = time.time()
        stale_sessions = [session_id for session_id, info in self._sessions.items() if info.expires_at <= now]
        for session_id in stale_sessions:
            self._sessions.pop(session_id, None)
        stale_tickets = [ticket for ticket, expires in self._confirm_tickets.items() if expires <= now]
        for ticket in stale_tickets:
            self._confirm_tickets.pop(ticket, None)

