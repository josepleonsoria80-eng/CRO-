"""Generador de tráfico sintético de un e-commerce, con "causas" ocultas conocidas.

Sirve para probar el pipeline sin datos reales y para validar que el análisis es capaz de
redescubrir lo que se sabe que influye en la compra.
"""
from __future__ import annotations

import math
import random

from .events import Event

BASE_TS = 1_760_000_000.0

# Efecto (en logit) de cada comportamiento sobre la probabilidad de querer comprar.
HIDDEN_EFFECTS = {
    "view_reviews": 0.9,
    "view_shipping_info": 0.7,
    "search": 0.7,
    "multi_product(>=3)": 0.5,
    "view_size_guide": 0.4,
    "add_to_wishlist": 0.3,
}


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def simulate(n_sessions: int = 5000, seed: int = 7) -> list[Event]:
    rng = random.Random(seed)
    events: list[Event] = []

    for i in range(n_sessions):
        sid = f"s{i:06d}"
        device = rng.choices(["mobile", "desktop", "tablet"], [0.6, 0.33, 0.07])[0]
        source = rng.choices(["organic", "paid", "email", "direct", "social"], [0.3, 0.25, 0.1, 0.2, 0.15])[0]
        t = BASE_TS + i * 11 + rng.random() * 5
        first = True

        def emit(name: str, page_type: str = "", url: str = "", **props):
            nonlocal t, first
            t += rng.uniform(3, 40)
            p = dict(props)
            if first:
                p.update(device=device, source=source)
                first = False
            events.append(Event(sid, round(t, 2), name, page_type, url, p))

        entry = rng.choices(["home", "category", "product"], [0.35, 0.35, 0.30])[0]
        if entry == "product":
            pass
        else:
            emit(f"page_view_{entry}", entry, "/" if entry == "home" else "/c/shoes")

        if rng.random() < 0.28:  # rebote
            if entry == "product":
                emit("page_view_product", "product", "/p/1")
            continue

        used_search = rng.random() < (0.25 if device == "desktop" else 0.16)
        if used_search:
            emit("search", "category", "/search?q=zapatillas")

        n_products = 1
        while n_products < 8 and rng.random() < 0.52:
            n_products += 1

        flags = {"view_reviews": False, "view_shipping_info": False, "view_size_guide": False, "add_to_wishlist": False}
        probs = {
            "view_reviews": 0.22 if device != "mobile" else 0.16,
            "view_shipping_info": 0.13,
            "view_size_guide": 0.09,
            "add_to_wishlist": 0.07,
        }
        for k in range(n_products):
            emit("page_view_product", "product", f"/p/{rng.randint(1, 60)}")
            for name, p in probs.items():
                if rng.random() < p / math.sqrt(n_products):
                    flags[name] = True
                    emit(name, "product", "")

        logit = -3.0
        logit += sum(HIDDEN_EFFECTS[k] for k, v in flags.items() if v)
        logit += HIDDEN_EFFECTS["search"] if used_search else 0
        logit += HIDDEN_EFFECTS["multi_product(>=3)"] if n_products >= 3 else 0
        logit += -0.35 if device == "mobile" else 0.0
        logit += 0.4 if source == "email" else 0.0

        if rng.random() > _sigmoid(logit + 1.6):
            continue
        emit("add_to_cart", "product", "", value=round(rng.uniform(25, 180), 2))
        if rng.random() < 0.85:
            emit("view_cart", "cart", "/cart")
        else:
            continue
        if rng.random() < 0.12:
            emit("apply_coupon", "cart", "/cart")
        if rng.random() > _sigmoid(logit + 2.4):
            continue
        emit("begin_checkout", "checkout", "/checkout")
        if rng.random() > 0.82:
            continue
        emit("add_payment_info", "checkout", "/checkout")
        if rng.random() > 0.78:
            continue
        emit("purchase", "confirmation", "/thank-you", value=round(rng.uniform(25, 260), 2))

    return events
