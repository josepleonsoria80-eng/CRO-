"""Backoffice: lógica de datos y autenticación (la UI está en static/admin.html).

Seguridad:
  * Acceso con token (CRO_ADMIN_TOKEN o uno aleatorio impreso al arrancar) → cookie HttpOnly/SameSite=Strict.
  * Las rutas /admin NO envían cabeceras CORS: otras webs no pueden leerlas desde el navegador.
  * Las escrituras exigen cookie + cabecera X-CRO-Admin (anti-CSRF).
  * Los eventos llegan de internet vía /collect (no son de fiar): la UI los pinta siempre con textContent.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from collections import Counter, defaultdict
from http.cookies import SimpleCookie
from pathlib import Path

from . import experiments
from .events import CRO_EVENTS, Event, sessionize

COOKIE = "cro_admin"
LIMITS = {"title": 45, "body": 140, "cta": 28}


def new_token() -> str:
    return os.environ.get("CRO_ADMIN_TOKEN") or secrets.token_urlsafe(16)


class Admin:
    def __init__(self, events_path: str | Path, config_path: str | Path, report_path: str | Path | None, token: str):
        self.events_path, self.config_path = Path(events_path), Path(config_path)
        self.report_path = Path(report_path) if report_path else None
        self.token = token

    # ------------------------------------------------------------ auth
    def token_ok(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(str(candidate), self.token)

    def cookie_ok(self, header: str | None) -> bool:
        if not header:
            return False
        c = SimpleCookie()
        try:
            c.load(header)
        except Exception:
            return False
        return COOKIE in c and self.token_ok(c[COOKIE].value)

    # ------------------------------------------------------------ datos
    def events(self) -> list[Event]:
        out: list[Event] = []
        if not self.events_path.exists():
            return out
        with self.events_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(Event.from_dict(json.loads(line)))
                except (ValueError, KeyError, TypeError):
                    continue  # línea corrupta o a medio escribir
        return out

    def config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {"tools": [], "gaps": []}

    def overview(self) -> dict:
        evs = self.events()
        sessions = sessionize(evs)
        organic = [s for s in sessions if s.names]
        buyers = sum(s.converted for s in organic)
        exp = experiments.evaluate(sessions)
        by_tool = self.tool_stats(evs)
        return {
            "events": len(evs),
            "sessions": len(organic),
            "buyers": buyers,
            "conversion_rate": round(buyers / len(organic), 4) if organic else 0,
            "revenue": round(sum(s.revenue for s in organic), 2),
            "last_event_ts": max((e.ts for e in evs), default=None),
            "experiment": exp["overall"],
            "tool_impressions": sum(v["impression"] for v in by_tool.values()),
            "tool_eligible": sum(v["eligible"] for v in by_tool.values()),
            "holdout_pct": self.config().get("experiment", {}).get("holdout_pct"),
        }

    @staticmethod
    def tool_stats(evs: list[Event]) -> dict[str, dict]:
        sess: dict = defaultdict(lambda: defaultdict(set))
        for e in evs:
            if e.name in CRO_EVENTS and e.props.get("tool_id"):
                key = (str(e.props["tool_id"]), e.name.removeprefix("cro_"))
                sess[key]["all"].add(e.session_id)
                sess[key][str(e.props.get("variant", "?"))].add(e.session_id)
        stats: dict[str, dict] = {}
        for (tool, kind), groups in sess.items():
            s = stats.setdefault(tool, {k: 0 for k in ("eligible", "impression", "click", "dismiss")})
            s[kind] = len(groups["all"])
            s.setdefault("by_variant", {}).setdefault(kind, {v: len(x) for v, x in groups.items() if v != "all"})
        return stats

    def tools(self) -> dict:
        cfg = self.config()
        evs = self.events()
        stats = self.tool_stats(evs)
        exp = experiments.evaluate(sessionize(evs))["by_tool"]
        rows = []
        for t in cfg["tools"]:
            s = stats.get(t["id"], {"eligible": 0, "impression": 0, "click": 0, "dismiss": 0})
            imp = s["impression"]
            rows.append({
                "id": t["id"], "type": t["type"], "priority": t.get("priority"), "stage": t.get("journey_stage"),
                "enabled": t.get("enabled", True), "content": t["content"], "trigger": t["trigger"],
                "est_extra_conversions_per_1000_sessions": t.get("est_extra_conversions_per_1000_sessions"),
                "claims_verified": t.get("claims_verified"), "copy_source": t.get("copy_source", "rules"),
                "eligible": s["eligible"], "impressions": imp, "clicks": s["click"], "dismissals": s["dismiss"],
                "ctr": round(s["click"] / imp, 4) if imp else None,
                "control_eligible": s.get("by_variant", {}).get("eligible", {}).get("control", 0),
                "experiment": exp.get(t["id"]),
            })
        return {"tools": rows, "gaps": cfg.get("gaps", []), "holdout_pct": cfg.get("experiment", {}).get("holdout_pct")}

    def report(self) -> dict | None:
        if self.report_path and self.report_path.exists():
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        return None

    def events_page(self, limit: int = 100, offset: int = 0, name: str = "", session: str = "") -> dict:
        evs = self.events()
        if name:
            evs = [e for e in evs if e.name == name]
        if session:
            evs = [e for e in evs if e.session_id == session]
        evs.sort(key=lambda e: e.ts, reverse=True)
        limit = max(1, min(limit, 500))
        names = sorted(Counter(e.name for e in self.events()))
        return {"total": len(evs), "offset": offset, "names": names,
                "items": [{"session_id": e.session_id, "ts": e.ts, "name": e.name, "page_type": e.page_type,
                           "url": e.url, "props": e.props} for e in evs[offset:offset + limit]]}

    def sessions(self, limit: int = 100) -> dict:
        out = []
        for s in sessionize(self.events()):
            shown = [str(e.props.get("tool_id")) for e in s.events if e.name == "cro_impression"]
            out.append({"id": s.id, "start": s.events[0].ts, "events": len(s.events), "converted": s.converted,
                        "variant": s.variant, "device": s.device, "source": s.source,
                        "revenue": s.revenue, "tools_shown": shown,
                        "path": [n for i, n in enumerate(s.names) if i == 0 or n != s.names[i - 1]][:12]})
        out.sort(key=lambda r: r["start"], reverse=True)
        return {"total": len(out), "items": out[:max(1, min(limit, 500))]}

    # ------------------------------------------------------------ escritura
    def update_tool(self, tool_id: str, patch: dict) -> dict:
        cfg = self.config()
        tool = next((t for t in cfg["tools"] if t["id"] == tool_id), None)
        if tool is None:
            raise KeyError(tool_id)
        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                raise ValueError("enabled debe ser true/false")
            tool["enabled"] = patch["enabled"]
        edited = False
        for k, n in LIMITS.items():
            if k in patch:
                v = patch[k]
                if not isinstance(v, str) or not v.strip() or len(v.strip()) > n:
                    raise ValueError(f"{k}: texto obligatorio de hasta {n} caracteres")
                if v.strip() != tool["content"].get(k):
                    tool["content"][k] = v.strip()
                    edited = True
        if edited:
            tool["copy_source"] = "manual"
            tool["claims_verified"] = False  # el copy editado a mano debe revisarse contra la tienda
        tool["edited_at"] = int(time.time())
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.config_path)
        return tool
