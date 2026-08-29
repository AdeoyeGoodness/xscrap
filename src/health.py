import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    """Answers any path with 200, so a wake-up ping cannot miss."""

    def _respond(self, include_body: bool) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond(include_body=True)

    def do_HEAD(self) -> None:
        # Uptime pingers commonly use HEAD rather than GET.
        self._respond(include_body=False)

    def log_message(self, *args) -> None:
        # Silence per-request logging; it would bury the bot's own output.
        pass


def start_health_server(port: Optional[int] = None) -> Optional[ThreadingHTTPServer]:
    """Serve a health endpoint in a background thread.

    Hosts like Render require a web service to bind $PORT or the deploy fails,
    and they spin the instance down until an inbound HTTP request arrives - so
    this endpoint is both the deploy requirement and the way to wake the bot.
    Returns None when no port is configured, which is the normal local case.
    """
    if port is None:
        raw_port = os.getenv("PORT", "").strip()
        if not raw_port:
            return None
        try:
            port = int(raw_port)
        except ValueError:
            logger.warning("Ignoring non-numeric PORT=%r", raw_port)
            return None

    # 0.0.0.0 so the platform's proxy can reach it; localhost would not deploy.
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    )
    thread.start()
    logger.info("Health endpoint listening on port %d", server.server_port)
    return server
