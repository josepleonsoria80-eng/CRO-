"""Detección de patrones de comportamiento que preceden a la compra.

Todo es estadística descriptiva/inferencial determinista (sin LLM): el LLM solo interpreta
y redacta, nunca calcula.

Salida (`analyze`):
  funnel          pasos del embudo y abandono entre pasos
  drivers         comportamientos asociados a comprar (lift, chi², odds ratio ajustado)
  patterns        secuencias ordenadas (A -> B [-> C]) con conversión alta
  ideal_journey   orden típico de los hitos de las sesiones que compran
  engagement      conversión según nº de productos vistos
  segments        conversión por dispositivo / origen
  top_paths       caminos de compra más frecuentes
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from statistics import mean

from .events import FUNNEL_STEPS, PURCHASE, Session

MIN_SUPPORT_ABS = 30


# ------------------------------------------------------------------ estadística básica
def chi2_p(a: int, b: int, c: int, d: int) -> float:
    """p-valor del chi² 2x2. a=con&compra b=con&no c=sin&compra d=sin&no."""
    n = a + b + c + d
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom == 0:
        return 1.0
    chi2 = n * (a * d - b * c) ** 2 / denom
    return math.erfc(math.sqrt(chi2 / 2))


def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        p = M[col][col]
        if abs(p) < 1e-12:
            raise ValueError("matriz singular")
        M[col] = [v / p for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [rv - f * cv for rv, cv in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


def _invert(A: list[list[float]]) -> list[list[float]]:
    n = len(A)
    cols = [_solve(A, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def logistic_fit(rows: list[list[int]], y: list[int], l2: float = 1.0, iters: int = 30):
    """Regresión logística con features binarias dispersas (Newton-Raphson + L2).

    `rows[i]` = índices de features activas. Devuelve (coeficientes, errores estándar);
    el índice 0 es el intercepto.
    """
    k = (max((max(r) for r in rows if r), default=-1) + 2)
    w = [0.0] * k
    H: list[list[float]] = []
    for _ in range(iters):
        grad = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for active, yi in zip(rows, y):
            idx = [0] + [j + 1 for j in active]
            z = sum(w[j] for j in idx)
            p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            r, s = p - yi, p * (1 - p)
            for a in idx:
                grad[a] += r
                for b in idx:
                    H[a][b] += s
        for j in range(1, k):
            grad[j] += l2 * w[j]
            H[j][j] += l2
        step = _solve(H, grad)
        w = [wj - sj for wj, sj in zip(w, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    cov = _invert(H)
    se = [math.sqrt(max(cov[j][j], 0.0)) for j in range(k)]
    return w, se


# ------------------------------------------------------------------ piezas del análisis
def _cr(conv: int, n: int) -> float:
    return conv / n if n else 0.0


def funnel(sessions: list[Session]) -> list[dict]:
    total = len(sessions)
    out = []
    prev = total
    for step in FUNNEL_STEPS:
        reached = sum(1 for s in sessions if step in s.names)
        out.append({
            "step": step,
            "sessions": reached,
            "pct_of_all": round(_cr(reached, total), 4),
            "pct_of_previous": round(_cr(reached, prev), 4),
            "dropoff_from_previous": prev - reached,
        })
        prev = reached or prev
    return out


def _stage_names(session: Session, stage: str) -> set[str] | None:
    """Eventos de una sesión que cuentan para una etapa.

    browse: lo ocurrido ANTES de añadir al carrito (todas las sesiones).
    cart:   lo ocurrido DESPUÉS de añadir al carrito (solo sesiones que llegan al carrito).
    Separar etapas evita confundir "marcadores de etapa" (p. ej. usar un cupón, que solo
    se puede hacer con el carrito lleno) con palancas que realmente empujan a comprar.
    """
    names = session.names
    if "add_to_cart" in names:
        i = names.index("add_to_cart")
        return set(names[:i]) if stage == "browse" else set(names[i + 1:])
    return set(names) if stage == "browse" else None


def drivers(sessions: list[Session], exclude: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for stage in ("browse", "cart"):
        rows += _drivers_stage(sessions, stage, set(FUNNEL_STEPS) | (exclude or set()))
    rows.sort(key=lambda r: (r["significant"], r.get("adj_odds_ratio", r["odds_ratio"])), reverse=True)
    return rows


def _drivers_stage(sessions: list[Session], stage: str, exclude: set[str]) -> list[dict]:
    pairs = [(sp, s) for s in sessions if (sp := _stage_names(s, stage)) is not None]
    if not pairs:
        return []
    presence = [p for p, _ in pairs]
    sess = [s for _, s in pairs]
    n = len(sess)
    y = [int(s.converted) for s in sess]
    conv_total = sum(y)
    names = sorted({e for p in presence for e in p} - exclude)

    rows = []
    for name in names:
        a = sum(1 for p, yi in zip(presence, y) if name in p and yi)
        b = sum(1 for p, yi in zip(presence, y) if name in p and not yi)
        c = conv_total - a
        d = (n - conv_total) - b
        n_with, n_without = a + b, c + d
        if n_with < MIN_SUPPORT_ABS or n_without < MIN_SUPPORT_ABS:
            continue
        cr_with, cr_without = _cr(a, n_with), _cr(c, n_without)
        or_raw = ((a + 0.5) * (d + 0.5)) / ((b + 0.5) * (c + 0.5))
        rows.append({
            "event": name,
            "stage": stage,
            "sessions_with": n_with,
            "share_of_sessions": round(n_with / n, 4),
            "cr_with": round(cr_with, 4),
            "cr_without": round(cr_without, 4),
            "lift": round(cr_with / cr_without, 2) if cr_without else None,
            "odds_ratio": round(or_raw, 2),
            "p_value": chi2_p(a, b, c, d),
        })

    # Odds ratio ajustado: cada evento controlando por los demás (y por ser móvil).
    if rows:
        feat = [r["event"] for r in rows]
        rws = []
        for p, s in zip(presence, sess):
            act = [i for i, f in enumerate(feat) if f in p]
            if s.device == "mobile":
                act.append(len(feat))
            rws.append(act)
        try:
            w, se = logistic_fit(rws, y)
            for i, r in enumerate(rows):
                coef, s_e = w[i + 1], se[i + 1]
                z = coef / s_e if s_e else 0.0
                r["adj_odds_ratio"] = round(math.exp(coef), 2)
                r["adj_p_value"] = math.erfc(abs(z) / math.sqrt(2))
        except ValueError:
            pass
    for r in rows:
        r["significant"] = bool(
            r["p_value"] < 0.01 and r["odds_ratio"] > 1 and r.get("adj_odds_ratio", r["odds_ratio"]) > 1.1
            and r.get("adj_p_value", r["p_value"]) < 0.05
        )
        r["p_value"] = float(f"{r['p_value']:.3g}")
        if "adj_p_value" in r:
            r["adj_p_value"] = float(f"{r['adj_p_value']:.3g}")
    return rows


def _first_order(session: Session, skip: set[str]) -> list[str]:
    seen, out = set(), []
    for name in session.names:
        if name not in seen and name not in skip:
            seen.add(name)
            out.append(name)
    return out


def sequence_patterns(sessions: list[Session], min_support: float = 0.03, top: int = 12) -> list[dict]:
    """Subsecuencias ordenadas de longitud 2-3 (A antes que B) con su conversión."""
    skip = set(FUNNEL_STEPS) - {"page_view_product"}
    seqs = [_first_order(s, skip) for s in sessions]
    conv = [s.converted for s in sessions]
    n, n_conv = len(sessions), sum(conv)
    base = _cr(n_conv, n)
    counts: Counter = Counter()
    counts_conv: Counter = Counter()
    for seq, c in zip(seqs, conv):
        pats = set(combinations(seq, 2)) | set(combinations(seq, 3))
        for pat in pats:
            counts[pat] += 1
            if c:
                counts_conv[pat] += 1
    out = []
    for pat, k in counts.items():
        if k < max(MIN_SUPPORT_ABS, min_support * n):
            continue
        kc = counts_conv[pat]
        a, b = kc, k - kc
        c_, d = n_conv - kc, (n - n_conv) - b
        p = chi2_p(a, b, c_, d)
        cr = _cr(kc, k)
        if p < 0.01 and cr > base * 1.2:
            out.append({"pattern": list(pat), "sessions": k, "conversion_rate": round(cr, 4),
                        "lift_vs_average": round(cr / base, 2) if base else None, "p_value": float(f"{p:.3g}")})
    # Preferir patrones más largos solo si mejoran claramente a sus sub-patrones.
    out.sort(key=lambda r: (r["lift_vs_average"], r["sessions"]), reverse=True)
    pruned: list[dict] = []
    for r in out:
        pat = tuple(r["pattern"])
        dominated = any(
            len(pat) > len(tuple(q["pattern"])) and set(q["pattern"]) <= set(pat) and r["lift_vs_average"] < q["lift_vs_average"] * 1.1
            for q in out
        )
        if not dominated:
            pruned.append(r)
    return pruned[:top]


def ideal_journey(sessions: list[Session], driver_rows: list[dict], min_support: float = 0.20) -> list[dict]:
    """Hitos que suelen darse en las sesiones que compran, ordenados por su posición típica."""
    good = {r["event"] for r in driver_rows if r["significant"]}
    converters = [s for s in sessions if s.converted]
    non_conv = [s for s in sessions if not s.converted]
    if not converters:
        return []
    pos: dict[str, list[float]] = {}
    for s in converters:
        names = s.names
        for name in dict.fromkeys(names):
            pos.setdefault(name, []).append(names.index(name) / max(len(names) - 1, 1))
    steps = []
    for name, ps in pos.items():
        support = len(ps) / len(converters)
        is_funnel = name in FUNNEL_STEPS
        if support < min_support or not (is_funnel or name in good):
            continue
        nc_support = sum(1 for s in non_conv if name in s.names) / len(non_conv) if non_conv else 0
        steps.append({
            "event": name,
            "kind": "funnel" if is_funnel else "lever",
            "support_among_buyers": round(support, 3),
            "support_among_non_buyers": round(nc_support, 3),
            "typical_position": round(mean(ps), 3),
        })
    # El funnel manda su propio orden; las palancas se sitúan por posición típica.
    order = {e: i for i, e in enumerate(FUNNEL_STEPS)}
    steps.sort(key=lambda r: (r["typical_position"], order.get(r["event"], 0)))
    if steps and steps[-1]["event"] != PURCHASE:
        steps = [s for s in steps if s["event"] != PURCHASE] + [s for s in steps if s["event"] == PURCHASE]
    return steps


def engagement(sessions: list[Session]) -> list[dict]:
    buckets: dict[str, list[Session]] = {}
    for s in sessions:
        k = sum(1 for nme in s.names if nme == "page_view_product")
        label = "0" if k == 0 else (str(k) if k < 5 else "5+")
        buckets.setdefault(label, []).append(s)
    return [{"product_views": k, "sessions": len(v), "conversion_rate": round(_cr(sum(x.converted for x in v), len(v)), 4)}
            for k, v in sorted(buckets.items(), key=lambda kv: (kv[0] == "5+", int(kv[0].rstrip("+"))))]


def segments(sessions: list[Session]) -> dict:
    out = {}
    for key in ("device", "source"):
        groups: dict[str, list[Session]] = {}
        for s in sessions:
            groups.setdefault(getattr(s, key), []).append(s)
        out[key] = [{"value": v, "sessions": len(g), "conversion_rate": round(_cr(sum(x.converted for x in g), len(g)), 4)}
                    for v, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return out


def top_paths(sessions: list[Session], top: int = 5) -> list[dict]:
    c: Counter = Counter()
    for s in sessions:
        if s.converted:
            collapsed = [n for i, n in enumerate(s.names) if i == 0 or n != s.names[i - 1]]
            c[tuple(collapsed)] += 1
    return [{"path": list(p), "buyers": k} for p, k in c.most_common(top)]


def last_event_before_exit(sessions: list[Session], top: int = 6) -> list[dict]:
    c = Counter(s.names[-1] for s in sessions if not s.converted and s.names)
    total = sum(c.values()) or 1
    return [{"event": e, "sessions": k, "share_of_abandons": round(k / total, 3)} for e, k in c.most_common(top)]


def analyze(sessions: list[Session]) -> dict:
    sessions = [s for s in sessions if s.names]
    n = len(sessions)
    if n < 100:
        raise ValueError(f"Hacen falta al menos 100 sesiones para sacar conclusiones fiables (hay {n}).")
    drv = drivers(sessions)
    return {
        "summary": {
            "sessions": n,
            "buyers": sum(s.converted for s in sessions),
            "conversion_rate": round(_cr(sum(s.converted for s in sessions), n), 4),
            "revenue": round(sum(s.revenue for s in sessions), 2),
        },
        "funnel": funnel(sessions),
        "drivers": drv,
        "patterns": sequence_patterns(sessions),
        "ideal_journey": ideal_journey(sessions, drv),
        "engagement": engagement(sessions),
        "segments": segments(sessions),
        "top_paths": top_paths(sessions),
        "exit_points": last_event_before_exit(sessions),
    }
