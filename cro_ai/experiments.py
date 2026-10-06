"""Medición de las herramientas CRO: sesiones con tratamiento vs. grupo de control (holdout).

El widget asigna cada sesión a `treatment` o `control` de forma aleatoria. En ambos grupos
registra `cro_eligible` cuando una herramienta habría saltado, pero solo el grupo tratamiento
la ve. Comparar solo las sesiones elegibles de ambos grupos da una estimación causal limpia.
"""
from __future__ import annotations

import math

from .events import Session


def _two_prop_z(c1: int, n1: int, c2: int, n2: int) -> tuple[float, float]:
    if not n1 or not n2:
        return 0.0, 1.0
    p1, p2 = c1 / n1, c2 / n2
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def _group(sessions: list[Session]) -> dict:
    n = len(sessions)
    c = sum(s.converted for s in sessions)
    return {"sessions": n, "conversions": c, "conversion_rate": round(c / n, 4) if n else 0.0}


def evaluate(sessions: list[Session]) -> dict:
    """Resultado global y por herramienta."""
    def eligible_tools(s: Session) -> set[str]:
        return {str(e.props.get("tool_id")) for e in s.events if e.name == "cro_eligible"}

    elig = [s for s in sessions if eligible_tools(s)]
    result = {"overall": _compare(elig), "by_tool": {}}
    tools = sorted({t for s in elig for t in eligible_tools(s)})
    for t in tools:
        result["by_tool"][t] = _compare([s for s in elig if t in eligible_tools(s)])
    return result


def _compare(sessions: list[Session]) -> dict:
    treat = [s for s in sessions if s.variant == "treatment"]
    ctrl = [s for s in sessions if s.variant == "control"]
    t, c = _group(treat), _group(ctrl)
    z, p = _two_prop_z(t["conversions"], t["sessions"], c["conversions"], c["sessions"])
    uplift = (t["conversion_rate"] / c["conversion_rate"] - 1) if c["conversion_rate"] else None
    enough = min(t["sessions"], c["sessions"]) >= 200
    return {
        "treatment": t,
        "control": c,
        "relative_uplift": round(uplift, 3) if uplift is not None else None,
        "p_value": float(f"{p:.3g}"),
        "verdict": ("insuficiente: faltan sesiones (>=200 por grupo)" if not enough
                    else "mejora significativa" if p < 0.05 and t["conversion_rate"] > c["conversion_rate"]
                    else "empeora significativamente" if p < 0.05
                    else "sin diferencia detectable"),
    }
