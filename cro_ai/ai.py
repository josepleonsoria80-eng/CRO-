"""Capa de IA generativa (opcional): interpreta los hallazgos y mejora el copy.

El LLM NO calcula nada: recibe las cifras ya calculadas por `analysis` y solo (1) redacta
el resumen ejecutivo y (2) propone copy alternativo para cada herramienta. Su salida se
valida y se limita en longitud; las afirmaciones sobre la tienda se restringen a los hechos
verificados por el rastreo.

Sin ANTHROPIC_API_KEY el pipeline funciona igual con copy y resumen deterministas.
Modelo configurable con CRO_AI_MODEL.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = (
    "Eres un especialista senior en CRO de e-commerce. Recibes métricas ya calculadas "
    "(no las recalcules ni inventes cifras). Responde SOLO con JSON válido. "
    "Regla de oro del copy: nunca prometas descuentos, plazos, devoluciones, garantías ni "
    "stock que no figuren en `verified_facts`. Sin presión engañosa ni falsa escasez. "
    "Español claro, tono cercano, títulos <= 45 caracteres, cuerpos <= 140, CTA <= 28."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _call(prompt: str, max_tokens: int = 3000) -> str:
    body = json.dumps({
        "model": os.environ.get("CRO_AI_MODEL", DEFAULT_MODEL),
        "max_tokens": max_tokens,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def _clip(v, n: int) -> str | None:
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def enhance(report: dict, config: dict, profile: dict | None, call=_call) -> dict | None:
    """Devuelve {'narrative': str, 'copy': {tool_id: {...}}} o None si no hay IA/falla."""
    if call is _call and not available():
        return None
    facts = {
        "free_shipping_text": (profile or {}).get("free_shipping_text", ""),
        "signals_present": [k for k, v in (profile or {}).get("signals", {}).items() if v.get("present")],
        "profile_available": profile is not None,
    }
    payload = {
        "summary": report["summary"],
        "funnel": report["funnel"],
        "drivers": [d for d in report["drivers"] if d["significant"]],
        "patterns": report["patterns"][:6],
        "ideal_journey": [s["event"] for s in report["ideal_journey"]],
        "segments": report["segments"],
        "verified_facts": facts,
        "tools": [{"id": t["id"], "type": t["type"], "trigger": t["trigger"], "content": t["content"]} for t in config["tools"]],
        "gaps": config["gaps"],
    }
    prompt = (
        "Datos del análisis:\n" + json.dumps(payload, ensure_ascii=False) +
        "\n\nDevuelve JSON con: {\"narrative\": \"resumen ejecutivo en markdown (<= 250 palabras): "
        "qué patrón convierte, dónde se pierde la gente y qué hacer primero\", "
        "\"copy\": {\"<tool_id>\": {\"title\": \"\", \"body\": \"\", \"cta\": \"\"}}}. "
        "Incluye una entrada de copy por cada herramienta."
    )
    try:
        data = _parse_json(call(prompt))
    except Exception as exc:  # red caída, JSON roto, etc.: el pipeline sigue sin IA
        print(f"[ai] aviso: no se pudo usar la IA ({exc.__class__.__name__}); se usa el copy determinista")
        return None
    out = {"narrative": _clip(data.get("narrative"), 3000), "copy": {}}
    known = {t["id"] for t in config["tools"]}
    for tid, c in (data.get("copy") or {}).items():
        if tid in known and isinstance(c, dict):
            clean = {k: _clip(c.get(k), n) for k, n in (("title", 45), ("body", 140), ("cta", 28))}
            clean = {k: v for k, v in clean.items() if v}
            if clean:
                out["copy"][tid] = clean
    return out


def apply_copy(config: dict, enhanced: dict | None) -> None:
    if not enhanced:
        return
    for t in config["tools"]:
        c = enhanced["copy"].get(t["id"])
        if c:
            t["content"].update(c)
            t["copy_source"] = "ai"
