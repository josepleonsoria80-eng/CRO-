"""Servidor mínimo: sirve el widget y la config, y recibe eventos en /collect (JSONL).

Pensado para pruebas y despliegues pequeños. En producción conviene ponerlo detrás de un
proxy con TLS y fijar --allow-origin al dominio de la tienda.
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 64 * 1024
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
from .admin import COOKIE  # noqa: E402

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


def make_handler(serve_dir: Path, events_path: Path, allow_origin: str, site=None, admin=None):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Headers", "content-type")

        # ---------------------------------------------------------------- backoffice
        def _json(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _admin_get(self, parsed) -> None:
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if parsed.path == "/admin" and admin.token_ok(q.get("token")):
                self.send_response(302)  # token en la URL solo una vez: pasa a cookie y se limpia
                self.send_header("Set-Cookie", f"{COOKIE}={admin.token}; HttpOnly; SameSite=Strict; Path=/admin")
                self.send_header("Location", "/admin")
                self.end_headers()
                return
            if not admin.cookie_ok(self.headers.get("Cookie")):
                body = "Acceso restringido. Abre /admin?token=TU_TOKEN (se imprime al arrancar el servidor).".encode()
                self.send_response(401)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/admin":
                data = (STATIC / "admin.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            num = lambda k, d: int(q[k]) if q.get(k, "").isdigit() else d  # noqa: E731
            routes = {
                "/admin/api/overview": lambda: admin.overview(),
                "/admin/api/tools": lambda: admin.tools(),
                "/admin/api/report": lambda: {"report": admin.report()},
                "/admin/api/events": lambda: admin.events_page(num("limit", 100), num("offset", 0), q.get("name", ""), q.get("session", "")),
                "/admin/api/sessions": lambda: admin.sessions(num("limit", 100)),
            }
            fn = routes.get(parsed.path)
            self._json(200, fn()) if fn else self._json(404, {"error": "no existe"})

        def _admin_post(self, parsed) -> None:
            if not admin.cookie_ok(self.headers.get("Cookie")) or self.headers.get("X-CRO-Admin") != "1":
                return self._json(403, {"error": "no autorizado"})
            m = re.fullmatch(r"/admin/api/tools/([\w.:-]{1,80})", parsed.path)
            length = int(self.headers.get("Content-Length") or 0)
            if not m or length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "petición inválida"})
            try:
                patch = json.loads(self.rfile.read(length))
                if not isinstance(patch, dict):
                    raise ValueError("se esperaba un objeto")
                self._json(200, admin.update_tool(m.group(1), patch))
            except KeyError:
                self._json(404, {"error": "herramienta desconocida"})
            except ValueError as exc:
                self._json(422, {"error": str(exc)})

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            if admin and (parsed.path == "/admin" or parsed.path.startswith("/admin/")):
                return self._admin_get(parsed)
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
            parsed = urlparse(self.path)
            if admin and parsed.path.startswith("/admin/"):
                return self._admin_post(parsed)
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


def serve(serve_dir: str, events_path: str, port: int = 8000, allow_origin: str = "*", site=None, admin=None) -> ThreadingHTTPServer:
    events = Path(events_path)
    events.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Path(serve_dir), events, allow_origin, site, admin))
    return srv
