from app.config import SecurityConfig
from app.security_manager import SecurityManager


def test_security_manager_session_and_csrf() -> None:
    manager = SecurityManager(SecurityConfig(username="u", password="p"))
    assert manager.authenticate_credentials("u", "p") is True
    session = manager.create_session("u")
    assert manager.get_session(session.session_id) is not None
    assert manager.validate_csrf(session.session_id, session.csrf_token) is True
    assert manager.validate_csrf(session.session_id, "bad") is False


def test_security_manager_confirm_ticket_is_one_time() -> None:
    manager = SecurityManager(SecurityConfig(operation_confirm_code="ok-code"))
    ticket = manager.create_confirm_ticket("ok-code")
    assert manager.consume_confirm_ticket(ticket) is True
    assert manager.consume_confirm_ticket(ticket) is False

