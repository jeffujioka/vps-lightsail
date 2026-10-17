# tests/e2e/fake_companion.py
"""FakeCompanion: the Shield companion's HTTP contract v2 on 127.0.0.1 (spec-01 §7 + §14 M17, as built
by plan 04a1; index §5.6, R26). Shipped complete by plan 05b2; spec-06 reuses it unchanged."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from vps_lightsail.core.fakes import read_json

if TYPE_CHECKING:                                    # conftest imports this module: no runtime cycle
    from tests.e2e.conftest import FakeWorld

MAX_CONFIG_BYTES = 16384                             # 04a1: a bigger body is "Invalid config: too large"
TEXT, JSON = "text/plain; charset=utf-8", "application/json"
ROUTES = {("GET", "/health"), ("GET", "/status"), ("POST", "/config"), ("POST", "/connect"), ("POST", "/disconnect")}
V2_ONLY = {("GET", "/status"), ("POST", "/connect")}
DEFAULT_BODY = {403: "Unauthorized", 404: "Not found"}


@dataclass(frozen=True)
class Req:
    method: str
    path: str
    query: dict[str, str]                            # last value per key
    authorization: str | None
    body: str
    aws_state: str | None                            # lightsail.json instance state at request time


def config_problem(text: str) -> str | None:
    """Reasons for `400 Invalid config: <reason>` (the real app parses wg-quick fully; this is enough)."""
    if len(text.encode()) > MAX_CONFIG_BYTES:
        return "too large"
    for section in ("[Interface]", "[Peer]"):
        if section not in text:
            return f"Missing {section} section"
    return None


class FakeCompanion:
    """In-memory stored config + tunnel state behind the real contract's routes, codes and texts."""

    def __init__(self, world: "FakeWorld", *, api: int = 2, token: str = "fake-txt-token",
                 name: str = "ShieldVPN") -> None:
        self.world, self.api, self.token, self.name = world, api, token, name
        self.requests: list[Req] = []
        self.config: str | None = None
        self.tunnel = "down"
        self._overrides: dict[str, tuple[int, str | None]] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        assert self._server is not None, "call FakeCompanion.start() first"
        return self._server.server_address[1]

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                url = urlsplit(self.path)
                size = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(size).decode("utf-8", "replace") if size else ""
                query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                code, ctype, text = fake.handle(self.command, url.path, query,
                                                self.headers.get("Authorization"), body)
                data = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _serve

            def log_message(self, *_args) -> None:     # silent: request lines never reach test output
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, name="fake-companion", daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def respond_with(self, path: str, code: int, body: str | None = None) -> None:
        """Sticky fault injection for PATH (the request is still recorded; routes do not run)."""
        with self._lock:
            self._overrides[path] = (code, body)

    def device_record(self) -> dict:
        return {"name": self.name, "ip": "127.0.0.1", "port": self.port, "token": self.token, "api": self.api}

    def address(self) -> str | None:
        for line in (self.config or "").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().lower() == "address":
                return value.split(",")[0].strip()
        return None

    def handle(self, method: str, path: str, query: dict[str, str], authorization: str | None,
               body: str) -> tuple[int, str, str]:
        with self._lock:
            self.requests.append(Req(method, path, dict(query), authorization, body, self._aws_state()))
            if path in self._overrides:
                code, text = self._overrides[path]
                return code, TEXT, text if text is not None else DEFAULT_BODY.get(code, HTTPStatus(code).phrase)
            return self._route((method, path), query, authorization == f"Bearer {self.token}", body)

    def _aws_state(self) -> str | None:
        instance = read_json(self.world.lightsail).get("instance")
        return instance.get("state") if instance else None

    def _route(self, key: tuple[str, str], query: dict[str, str], authed: bool, body: str) -> tuple[int, str, str]:
        if key == ("GET", "/health"):
            return 200, TEXT, "ok"
        if key not in ROUTES or (self.api == 1 and key in V2_ONLY):
            return 404, TEXT, "Not found"
        if not authed:                                          # auth before any body validation
            return 403, TEXT, "Unauthorized"
        if key == ("GET", "/status"):
            return 200, JSON, json.dumps({"api": 2, "tunnel": self.tunnel, "has_config": self.config is not None,
                                          "address": self.address()})
        if key == ("POST", "/connect"):
            if self.config is None:
                return 409, TEXT, "No stored config"
            self.tunnel = "up"
            return 200, TEXT, "Connected"
        if key == ("POST", "/disconnect"):
            self.tunnel = "down"                                # the stored config is kept
            return 200, TEXT, "Disconnected"
        activate = "true" if self.api == 1 else query.get("activate", "true")    # v1 ignores activate
        if activate not in ("true", "false"):
            return 400, TEXT, "Invalid activate value"
        if not body.strip():
            return 400, TEXT, "Empty config"
        if problem := config_problem(body):
            return 400, TEXT, f"Invalid config: {problem}"
        self.config, self.tunnel = body, ("up" if activate == "true" else "down")     # U3
        return 200, TEXT, "OK — tunnel activated" if activate == "true" else "OK — config stored"
