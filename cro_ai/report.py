"""Informe legible (markdown) con los hallazgos y las herramientas generadas."""
from __future__ import annotations

LABELS = {
    "page_view_product": "ver ficha de producto", "add_to_cart": "añadir al carrito", "view_cart": "ver carrito",
    "begin_checkout": "iniciar checkout", "add_payment_info": "introducir pago", "purchase": "comprar",
    "view_reviews": "leer reseñas", "view_shipping_info": "consultar envío", "view_size_guide": "ver guía de tallas",
    "add_to_wishlist": "guardar en favoritos", "search": "usar el buscador", "apply_coupon": "aplicar cupón",
    "page_view_home": "ver home", "page_view_category": "ver categoría",
}


def label(e: str) -> str:
    return LABELS.get(e, e)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(report: dict, config: dict, narrative: str | None = None, experiment: dict | None = None) -> str:
    s = report["summary"]
    out = ["# Informe CRO", "",
           f"**{s['sessions']:,} sesiones · {s['buyers']:,} compras · conversión {pct(s['conversion_rate'])} · ingresos {s['revenue']:,.0f}**", ""]
    if narrative:
        out += ["## Resumen (IA)", "", narrative, ""]

    out += ["## Viaje ideal detectado", "",
            " → ".join(f"**{label(j['event'])}**" if j["kind"] == "lever" else label(j["event"]) for j in report["ideal_journey"]),
            "", "_En negrita, los hitos de comportamiento (palancas) que distinguen a quien compra._", ""]

    out += ["## Qué hace la gente que compra", "",
            "| Comportamiento | Etapa | Conv. con | Conv. sin | Odds ratio ajustado | Significativo |", "|---|---|---|---|---|---|"]
    for d in report["drivers"]:
        out.append(f"| {label(d['event'])} | {'antes del carrito' if d['stage'] == 'browse' else 'en el carrito'} | {pct(d['cr_with'])} | "
                   f"{pct(d['cr_without'])} | {d.get('adj_odds_ratio', d['odds_ratio'])} | {'sí' if d['significant'] else 'no'} |")
    out += ["", "_El odds ratio ajustado controla por el resto de comportamientos y por dispositivo. "
            "Asociación ≠ causalidad: por eso las herramientas llevan grupo de control._", ""]

    if report["patterns"]:
        out += ["## Secuencias con más conversión", ""]
        for p in report["patterns"][:6]:
            out.append(f"- {' → '.join(label(x) for x in p['pattern'])}: {pct(p['conversion_rate'])} de conversión "
                       f"(x{p['lift_vs_average']} la media, {p['sessions']} sesiones)")
        out.append("")

    out += ["## Dónde se pierde la gente", "", "| Paso | Sesiones | % del paso anterior |", "|---|---|---|"]
    for f in report["funnel"]:
        out.append(f"| {label(f['step'])} | {f['sessions']:,} | {pct(f['pct_of_previous'])} |")
    out.append("")

    out += ["## Herramientas CRO generadas", "", "| # | Herramienta | Etapa | Conv. extra / 1000 sesiones (hipótesis) | Copy verificado |", "|---|---|---|---|---|"]
    for t in config["tools"]:
        out.append(f"| {t['priority']} | `{t['id']}` — {t['content']['title']} | {t['journey_stage']} | "
                   f"{t['est_extra_conversions_per_1000_sessions']} | {'sí' if t['claims_verified'] else 'no (revisar)'} |")
    out.append("")
    if config["gaps"]:
        out += ["## Oportunidades que la tienda aún no cubre", ""] + [f"- {g['message']}" for g in config["gaps"]] + [""]

    if experiment:
        o = experiment["overall"]
        out += ["## Resultado del experimento", "",
                f"Tratamiento: {pct(o['treatment']['conversion_rate'])} ({o['treatment']['sessions']} sesiones) · "
                f"Control: {pct(o['control']['conversion_rate'])} ({o['control']['sessions']} sesiones) · "
                f"p={o['p_value']} · **{o['verdict']}**", ""]
    return "\n".join(out)
