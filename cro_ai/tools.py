"""Convierte los hallazgos del análisis en herramientas CRO configurables.

Principios:
  * Solo se crea una herramienta para comportamientos con efecto estadísticamente
    significativo (ver `analysis.drivers`).
  * El copy nunca afirma algo que la tienda no ofrece: si se dispone del perfil del
    rastreo se usan solo hechos verificados; si no, copy neutro y `claims_verified=false`.
  * Si la mejor palanca no existe en la tienda, se reporta como *gap* en vez de inventarla.
  * El impacto estimado es una hipótesis (la correlación no es causalidad): se valida con
    el grupo de control que incluye el widget.
"""
from __future__ import annotations

import time

ADOPTION = 0.10          # % de sesiones empujadas que acaban haciendo el comportamiento
CAUSAL_DISCOUNT = 0.5    # descuento por posible sesgo de selección (los compradores ya eran más proclives)

# evento -> (señal del rastreo que debe existir, tipo de herramienta, etapa del journey)
LEVERS = {
    "view_reviews": ("reviews", "social_proof_prompt", "evaluar"),
    "view_shipping_info": ("shipping_info", "shipping_reassurance", "evaluar"),
    "view_size_guide": ("size_guide", "size_guide_prompt", "evaluar"),
    "add_to_wishlist": ("wishlist", "wishlist_prompt", "evaluar"),
    "search": ("search", "search_prompt", "descubrir"),
    "apply_coupon": ("coupon", "coupon_reminder", "decidir"),
}

DEFAULT_EVENT_SELECTORS = {
    "view_reviews": {"selector": "#reviews, .reviews, [data-cro='reviews']", "on": "visible"},
    "view_shipping_info": {"selector": "#shipping, .shipping-info, [data-cro='shipping']", "on": "visible"},
    "view_size_guide": {"selector": "[data-cro='size-guide'], .size-guide", "on": "click"},
    "add_to_wishlist": {"selector": "[data-cro='wishlist'], .wishlist", "on": "click"},
    "apply_coupon": {"selector": "[data-cro='coupon'] form, form.coupon", "on": "submit"},
    "search": {"selector": "input[type=search], input[name=q], input[name=s]", "on": "focus"},
    "add_to_cart": {"selector": "[data-cro='add-to-cart'], .add-to-cart, button[name=add-to-cart]", "on": "click"},
    "add_payment_info": {"selector": "[autocomplete^='cc-']", "on": "focus"},
}

PAGE_EVENTS = {"cart": ["view_cart"], "checkout": ["begin_checkout"], "confirmation": ["purchase"]}


def _facts(profile: dict | None) -> dict:
    sig = (profile or {}).get("signals", {})
    has = lambda k: bool(sig.get(k, {}).get("present"))  # noqa: E731
    return {
        "verified": profile is not None,
        "free_shipping_text": (profile or {}).get("free_shipping_text", ""),
        "returns": has("returns"),
        "trust_badges": has("trust_badges"),
    }


def _selector(event: str, profile: dict | None) -> dict:
    spec = dict(DEFAULT_EVENT_SELECTORS[event])
    key = {"view_reviews": "reviews", "view_shipping_info": "shipping_info", "view_size_guide": "size_guide",
           "add_to_wishlist": "wishlist", "add_to_cart": "add_to_cart", "search": "search"}.get(event)
    found = (profile or {}).get("selectors", {}).get(key) if key else None
    if found:
        spec["selector"] = f"{found}, {spec['selector']}"
    return spec


def _copy(event: str, facts: dict) -> dict:
    ship_body = facts["free_shipping_text"] or "Consulta aquí plazos y costes de envío antes de decidir."
    if facts["free_shipping_text"] and facts["returns"]:
        ship_body += " Y puedes devolverlo si no te convence."
    return {
        "view_reviews": {"title": "Lo que opinan otros clientes", "body": "Antes de decidir, mira las valoraciones de quienes ya lo compraron.", "cta": "Ver opiniones"},
        "view_shipping_info": {"title": "Sin sorpresas con el envío", "body": ship_body, "cta": "Ver detalles de envío"},
        "view_size_guide": {"title": "¿Dudas con la talla?", "body": "La guía de tallas te ayuda a acertar a la primera.", "cta": "Abrir guía de tallas"},
        "add_to_wishlist": {"title": "Guárdalo para después", "body": "Añádelo a tu lista y compáralo con calma.", "cta": "Guardar en favoritos"},
        "search": {"title": "¿Buscas algo concreto?", "body": "Escribe lo que necesitas y te llevamos directo al producto.", "cta": "Buscar"},
        "apply_coupon": {"title": "¿Tienes un código?", "body": "Si tienes un cupón, aplícalo antes de pagar.", "cta": "Introducir código"},
    }[event]


def _estimate(row: dict) -> float:
    gain = max(row["cr_with"] - row["cr_without"], 0)
    return round(1000 * (1 - row["share_of_sessions"]) * ADOPTION * CAUSAL_DISCOUNT * gain, 2)


def generate_tools(report: dict, profile: dict | None = None, holdout_pct: int = 10) -> dict:
    facts = _facts(profile)
    sig = (profile or {}).get("signals", {})
    tools: list[dict] = []
    gaps: list[dict] = []

    for row in report["drivers"]:
        ev = row["event"]
        if not row["significant"] or ev not in LEVERS:
            continue
        signal, ttype, stage = LEVERS[ev]
        if profile is not None and not sig.get(signal, {}).get("present"):
            gaps.append({
                "event": ev,
                "message": f"'{ev}' es una de las palancas con más efecto (x{row.get('adj_odds_ratio', row['odds_ratio'])} "
                           f"en probabilidad de comprar, ajustado) pero el rastreo no detectó '{signal}' en la tienda. "
                           "Implementarlo suele ser más rentable que cualquier nudge.",
            })
            continue
        trigger = {"missing_event": ev, "after_seconds": 15}
        if ev == "search":
            trigger.update(page_type=["home", "category"], after_seconds=12, min_page_views=2)
        elif ev == "apply_coupon":
            trigger.update(page_type="cart", has_event="add_to_cart", after_seconds=20)
        else:
            trigger.update(page_type="product", after_seconds=15)  # mismo tiempo: decide la prioridad
        tool = {
            "id": f"{ttype}-{ev}",
            "type": ttype,
            "journey_stage": stage,
            "trigger": trigger,
            "content": _copy(ev, facts),
            "action": _action(ev, profile),
            "evidence": {k: row[k] for k in ("stage", "sessions_with", "cr_with", "cr_without", "lift", "odds_ratio", "adj_odds_ratio", "p_value") if k in row},
            "hypothesis": f"Empujar a más visitantes a '{ev}' aumentará su probabilidad de comprar.",
            "est_extra_conversions_per_1000_sessions": _estimate(row),
            "claims_verified": facts["verified"],
        }
        if ev == "apply_coupon":
            tool["requires_approval"] = "Sin evidencia sólida de efecto causal; solo recordatorio, no ofrece descuento."
        tools.append(tool)

    # Visitantes que exploran varios productos convierten más: ayúdales a comparar.
    eng = {e["product_views"]: e for e in report.get("engagement", [])}
    base = eng.get("1", {}).get("conversion_rate", 0)
    for k in ("3", "4", "5+"):
        if base and eng.get(k, {}).get("conversion_rate", 0) >= 1.4 * base and eng[k]["sessions"] >= 100:
            tools.append({
                "id": "recently_viewed-compare",
                "type": "recently_viewed",
                "journey_stage": "evaluar",
                "trigger": {"page_type": "product", "min_product_views": max(int(k.rstrip("+")) - 1, 2),
                            "missing_event": "add_to_cart", "after_seconds": 10},
                "content": {"title": "Lo que has visto", "body": "Compara los productos que has mirado y decide con calma.", "cta": "Ver"},
                "action": {"type": "none"},
                "evidence": {"conversion_rate_1_view": base, f"conversion_rate_{k}_views": eng[k]["conversion_rate"]},
                "hypothesis": "Facilitar la comparación mantiene a los visitantes explorando donde la conversión sube.",
                "est_extra_conversions_per_1000_sessions": 0.0,
                "claims_verified": True,
            })
            break

    tools += _funnel_tools(report, profile, facts)
    tools.sort(key=lambda t: -t["est_extra_conversions_per_1000_sessions"])
    for i, t in enumerate(tools):
        t["priority"] = i + 1

    event_selectors = {ev: _selector(ev, profile) for ev in DEFAULT_EVENT_SELECTORS}
    return {
        "version": 1,
        "generated_at": int(time.time()),
        "experiment": {"holdout_pct": holdout_pct, "max_nudges_per_session": 2, "min_seconds_between_nudges": 20},
        "page_events": PAGE_EVENTS,
        "page_type_patterns": _page_patterns(),
        "event_selectors": event_selectors,
        "journey": [s["event"] for s in report.get("ideal_journey", [])],
        "tools": tools,
        "gaps": gaps,
    }


def _action(event: str, profile: dict | None) -> dict:
    spec = _selector(event, profile)
    if event in ("view_reviews", "view_shipping_info", "view_size_guide"):
        return {"type": "scroll" if spec["on"] == "visible" else "click", "selector": spec["selector"]}
    if event == "search":
        return {"type": "focus", "selector": spec["selector"]}
    if event == "add_to_wishlist":
        return {"type": "click", "selector": spec["selector"]}
    return {"type": "scroll", "selector": spec["selector"]}


def _funnel_tools(report: dict, profile: dict | None, facts: dict) -> list[dict]:
    steps = {s["step"]: s for s in report["funnel"]}
    out = []
    cart_to_checkout = steps.get("begin_checkout")
    if cart_to_checkout and cart_to_checkout["pct_of_previous"] < 0.85 and cart_to_checkout["dropoff_from_previous"] >= 30:
        lost = cart_to_checkout["dropoff_from_previous"]
        body = "Tu carrito sigue guardado. " + (facts["free_shipping_text"] or "Termina tu compra en un par de minutos.")
        out.append({
            "id": "cart_recovery-exit",
            "type": "cart_recovery",
            "journey_stage": "decidir",
            "trigger": {"page_type": "cart", "has_event": "add_to_cart", "missing_event": "begin_checkout",
                        "exit_intent": True, "mobile_after_seconds": 45},
            "content": {"title": "¿Te lo guardamos?", "body": body, "cta": "Continuar al pago"},
            "action": {"type": "link", "href": _sample(profile, "checkout", "/checkout")},
            "evidence": {"funnel_step": "view_cart→begin_checkout", "sessions_lost": lost,
                         "pct_continue": cart_to_checkout["pct_of_previous"]},
            "hypothesis": "Un recordatorio en el momento de abandono recupera parte de las sesiones que se pierden en el carrito.",
            "est_extra_conversions_per_1000_sessions": round(1000 * lost / report["summary"]["sessions"] * 0.05 * 0.3, 2),
            "claims_verified": facts["verified"],
        })
    last = steps.get("purchase")
    pay = steps.get("add_payment_info")
    if last and pay and pay["sessions"] and last["sessions"] / pay["sessions"] < 0.85:
        lost = pay["sessions"] - last["sessions"]
        bits = [b for b, ok in (("pago seguro", facts["trust_badges"]), ("devoluciones", facts["returns"])) if ok]
        body = ("Tu compra está protegida: " + " y ".join(bits) + ".") if bits else "Revisa tu pedido y completa el pago cuando quieras."
        out.append({
            "id": "checkout_reassurance-idle",
            "type": "checkout_reassurance",
            "journey_stage": "pagar",
            "trigger": {"page_type": "checkout", "missing_event": "purchase", "after_seconds": 40},
            "content": {"title": "¿Alguna duda para finalizar?", "body": body, "cta": "Entendido"},
            "action": {"type": "none"},
            "evidence": {"funnel_step": "add_payment_info→purchase", "sessions_lost": lost},
            "hypothesis": "Reducir la incertidumbre en el pago reduce el abandono final.",
            "est_extra_conversions_per_1000_sessions": round(1000 * lost / report["summary"]["sessions"] * 0.05 * 0.3, 2),
            "claims_verified": facts["verified"],
        })
    return out


def _sample(profile: dict | None, ptype: str, default: str) -> str:
    urls = (profile or {}).get("sample_urls", {}).get(ptype)
    if urls:
        from urllib.parse import urlparse
        return urlparse(urls[0]).path or default
    return default


def _page_patterns() -> dict:
    from .crawler import PAGE_TYPE_PATTERNS
    return {name: pat for name, pat in PAGE_TYPE_PATTERNS}
