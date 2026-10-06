"""Esquema de eventos, lectura/escritura JSONL y agrupación en sesiones."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# Pasos mecánicos del embudo: casi siempre ocurren antes de comprar, así que no son
# "palancas" sobre las que se pueda actuar; se analizan aparte como embudo.
FUNNEL_STEPS = [
    "page_view_product",
    "add_to_cart",
    "view_cart",
    "begin_checkout",
    "add_payment_info",
    "purchase",
]
PURCHASE = "purchase"
# Eventos propios de las herramientas CRO (no forman parte del comportamiento orgánico).
CRO_EVENTS = {"cro_eligible", "cro_impression", "cro_click", "cro_dismiss"}


@dataclass
class Event:
    session_id: str
    ts: float
    name: str
    page_type: str = ""
    url: str = ""
    props: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            session_id=str(d["session_id"]),
            ts=float(d["ts"]),
            name=str(d["name"]),
            page_type=str(d.get("page_type", "")),
            url=str(d.get("url", "")),
            props=dict(d.get("props") or {}),
        )


@dataclass
class Session:
    id: str
    events: list[Event]

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.events if e.name not in CRO_EVENTS]

    @property
    def converted(self) -> bool:
        return any(e.name == PURCHASE for e in self.events)

    @property
    def revenue(self) -> float:
        return sum(float(e.props.get("value", 0) or 0) for e in self.events if e.name == PURCHASE)

    def prop(self, key: str, default: str = "unknown") -> str:
        for e in self.events:
            if key in e.props:
                return str(e.props[key])
        return default

    @property
    def device(self) -> str:
        return self.prop("device")

    @property
    def source(self) -> str:
        return self.prop("source")

    @property
    def variant(self) -> str | None:
        v = self.prop("variant", "")
        return v or None


def write_jsonl(events: Iterable[Event], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: str | Path) -> list[Event]:
    out: list[Event] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Event.from_dict(json.loads(line)))
    return out


def sessionize(events: Iterable[Event]) -> list[Session]:
    by_id: dict[str, list[Event]] = {}
    for e in events:
        by_id.setdefault(e.session_id, []).append(e)
    sessions = [Session(sid, sorted(evs, key=lambda e: e.ts)) for sid, evs in by_id.items()]
    sessions.sort(key=lambda s: s.events[0].ts)
    return sessions
