"""Tests for the /auth_status line rendering helpers in src/bot.py."""

from datetime import datetime, timezone

from src.bot import _handle_for_row, _render_account_line
from src.config import cookie_account_name


def test_handle_uses_real_username_for_known_cookie_row():
    name = cookie_account_name("tokA")
    mapping = {name: "levelsio"}
    assert _handle_for_row(name, mapping) == "@levelsio"


def test_handle_shows_raw_id_for_unknown_cookie_row():
    name = cookie_account_name("mystery")
    assert _handle_for_row(name, {}) == f"<code>{name}</code>"


def test_handle_treats_login_row_as_a_handle():
    # Interactive (/login) rows are stored under the real handle already.
    assert _handle_for_row("jack", {}) == "@jack"


def test_render_active_line():
    row = {"name": "jack", "active": True, "locked_until": None, "error_msg": None}
    line = _render_account_line(row, {})
    assert line == "✅ @jack — active"


def test_render_throttled_line():
    until = datetime(2026, 8, 29, 19, 45, tzinfo=timezone.utc)
    row = {"name": "jack", "active": True, "locked_until": until, "error_msg": None}
    line = _render_account_line(row, {})
    assert "🔒 @jack — throttled until 19:45 UTC" == line


def test_render_expired_line_includes_reason():
    row = {
        "name": "jack",
        "active": False,
        "locked_until": None,
        "error_msg": "(32) Could not authenticate you",
    }
    line = _render_account_line(row, {})
    assert line.startswith("❌ @jack — expired")
    assert "authenticate" in line


def test_render_expired_line_without_reason():
    row = {"name": "jack", "active": False, "locked_until": None, "error_msg": None}
    assert _render_account_line(row, {}) == "❌ @jack — expired"
