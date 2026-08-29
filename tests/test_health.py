import json
import urllib.request

import pytest

from src.health import start_health_server


@pytest.fixture
def health_server():
    """Serve on an ephemeral port so the test never fights a real service."""
    server = start_health_server(port=0)
    yield server
    server.shutdown()
    server.server_close()


def _get(server, path="/"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    return urllib.request.urlopen(url, timeout=5)


def test_serves_200_for_a_wake_up_ping(health_server):
    response = _get(health_server)

    assert response.status == 200
    assert json.loads(response.read()) == {"status": "ok"}


def test_any_path_answers_so_a_ping_cannot_miss(health_server):
    for path in ("/", "/health", "/anything/at/all"):
        assert _get(health_server, path).status == 200


def test_head_requests_work_for_uptime_pingers(health_server):
    url = f"http://127.0.0.1:{health_server.server_port}/"
    request = urllib.request.Request(url, method="HEAD")

    assert urllib.request.urlopen(request, timeout=5).status == 200


def test_no_server_without_a_port_configured(monkeypatch):
    """Local runs must not open a listener."""
    monkeypatch.delenv("PORT", raising=False)
    assert start_health_server() is None


def test_blank_port_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("PORT", "   ")
    assert start_health_server() is None


def test_non_numeric_port_is_ignored_rather_than_crashing(monkeypatch):
    """A bad PORT must not take the bot down at startup."""
    monkeypatch.setenv("PORT", "not-a-port")
    assert start_health_server() is None


def test_port_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PORT", "0")
    server = start_health_server()
    try:
        assert server is not None
        assert _get(server).status == 200
    finally:
        server.shutdown()
        server.server_close()
