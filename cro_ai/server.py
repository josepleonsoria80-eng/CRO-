"""Servidor mínimo: sirve el widget y la config, y recibe eventos en /collect (JSONL).

Pensado para pruebas y despliegues pequeños. En producción conviene ponerlo detrás de un
proxy con TLS y fijar --allow-origin al dominio de la tienda.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 64 * 1024
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
STATIC = Path(__file__).parent / "static"
_lock = threading.Lock()


def clean_event(raw: dict) -> dict | None:
    """Valida y normaliza un evento entrante; descarta lo que no encaje."""
    try:
        name = str(raw["name"])
        sid = str(raw["session_id"])[:64]
        ts = float(raw["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    if not NAME_RE.match(name) or not sid:
        return None
    props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
    props = {str(k)[:32]: (v if isinstance(v, (int, float, bool)) else str(v)[:80]) for k, v in list(props.items())[:12]}
    return {"session_id": sid, "ts": ts, "name": name, "page_type": str(raw.get("page_type", ""))[:20],
            "url": str(raw.get("url", ""))[:200], "props": props}


def make_handler(serve_dir: Path, events_path: Path, allow_origin: str, site=None):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Headers", "content-type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            routes = {"/cro-widget.js": (STATIC / "cro-widget.js", "application/javascript"),
                      "/cro-config.json": (serve_dir / "cro-config.json", "application/json")}
            hit = routes.get(self.path.split("?")[0])
            page = site(self.path) if (site and not hit) else None
            if page is not None:
                hit, data = (None, "text/html"), page.encode()
            elif not hit or not hit[0].exists():
                self.send_response(404)
                self.end_headers()
                return
            else:
                data = hit[0].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", hit[1] + "; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/collect":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self.send_response(413)
                self.end_headers()
                return
            try:
                payload = json.loads(self.rfile.read(length))
                items = payload if isinstance(payload, list) else [payload]
                good = [e for e in (clean_event(i) for i in items[:50] if isinstance(i, dict)) if e]
            except ValueError:
                good = []
            if good:
                with _lock, events_path.open("a", encoding="utf-8") as f:
                    for e in good:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
            self.send_response(204 if good else 400)
            self._cors()
            self.end_headers()

        def log_message(self, *a):  # silencioso
            pass

    return Handler


def serve(serve_dir: str, events_path: str, port: int = 8000, allow_origin: str = "*", site=None) -> ThreadingHTTPServer:
    events = Path(events_path)
    events.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Path(serve_dir), events, allow_origin, site))
    return srv
