"""Rastreador ligero (solo stdlib) que perfila un e-commerce.

Qué hace: recorre páginas del mismo dominio, clasifica su tipo (home, categoría, producto,
carrito, checkout...), detecta señales de conversión presentes o ausentes (reseñas, info de
envío, sellos de confianza, guía de tallas...) y propone selectores CSS para el widget.

Límites: lee HTML servido por el servidor; las tiendas que pintan todo con JavaScript
necesitan además el tracker (cro-widget.js), que sí ve el DOM real.
Úsalo solo en sitios propios o con permiso. Respeta robots.txt.
"""
from __future__ import annotations

import re
import time
import urllib.request
import urllib.robotparser
from collections import Counter, deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag

UA = "CRO-AI-Crawler/0.1 (+analisis de conversion; respeta robots.txt)"
MAX_BYTES = 2_000_000

PAGE_TYPE_PATTERNS = [
    ("confirmation", r"thank|gracias|order-confirm|confirmacion|pedido-completado|order-received"),
    ("checkout", r"/checkout|/pago|/caja|/payment"),
    ("cart", r"/cart|/carrito|/basket|/cesta"),
    ("search", r"/search|/buscar|[?&](q|s|query)="),
    ("product", r"/product|/producto|/p/|/item|/dp/|/articulo"),
    ("category", r"/category|/categoria|/collections?|/c/|/shop|/tienda|/catalog"),
]

SIGNAL_KEYWORDS = {
    "reviews": r"reviews?|reseñas?|opiniones|valoraciones|aggregaterating|estrellas|trustpilot",
    "shipping_info": r"env[ií]o|shipping|delivery|entrega",
    "free_shipping": r"env[ií]o gratis|env[ií]o gratuito|free shipping|portes gratis",
    "returns": r"devoluci[oó]n|devoluciones|returns?|reembolso",
    "trust_badges": r"pago seguro|secure (payment|checkout)|ssl|garant[ií]a|guarantee|verified|certificad",
    "urgency": r"[uú]ltimas unidades|only \d+ left|quedan \d+|stock limitado|low stock",
    "size_guide": r"gu[ií]a de tallas|size guide|size chart|tabla de tallas",
    "wishlist": r"favoritos|wishlist|lista de deseos",
    "coupon": r"cup[oó]n|c[oó]digo (promocional|descuento)|promo code|discount code|coupon",
    "live_chat": r"live chat|chat en vivo|whatsapp|chatea",
    "guest_checkout": r"invitado|guest checkout|sin registrarte|checkout as guest",
}

SELECTOR_HINTS = {
    "reviews": ("review", "resena", "reseña", "opinion", "rating"),
    "shipping_info": ("shipping", "envio", "envío", "delivery"),
    "size_guide": ("size-guide", "sizeguide", "size_chart", "tallas"),
    "add_to_cart": ("add-to-cart", "add_to_cart", "addtocart", "anadir", "añadir", "btn-cart", "comprar"),
    "search": ("search", "buscar", "buscador"),
    "wishlist": ("wishlist", "favorit"),
    "coupon": ("coupon", "cupon", "promo"),
}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self.text: list[str] = []
        self.jsonld: list[str] = []
        self.selectors: dict[str, str] = {}
        self.has_search_input = False
        self._in_title = False
        self._in_ld = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            if tag == "script" and a.get("type") == "application/ld+json":
                self._in_ld = True
            else:
                self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        if tag == "input" and (a.get("type") == "search" or a.get("name") in ("q", "s", "query")):
            self.has_search_input = True
            self.selectors.setdefault("search", _css(tag, a))
        ident = f"{a.get('id', '')} {a.get('class', '')}".lower()
        if ident.strip():
            for sig, hints in SELECTOR_HINTS.items():
                if sig not in self.selectors and any(h in ident for h in hints):
                    self.selectors[sig] = _css(tag, a)
        if tag in ("button", "input") and "add_to_cart" not in self.selectors:
            blob = f"{a.get('name', '')} {a.get('value', '')} {a.get('aria-label', '')}".lower()
            if "cart" in blob or "carrito" in blob:
                self.selectors["add_to_cart"] = _css(tag, a)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_ld:
            self._in_ld = False
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self.jsonld.append(data)
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def _css(tag: str, a: dict) -> str:
    if a.get("id") and re.fullmatch(r"[\w-]+", a["id"]):
        return f"#{a['id']}"
    first = a.get("class", "").split()
    if first and re.fullmatch(r"[\w-]+", first[0]):
        return f"{tag}.{first[0]}"
    return tag


def classify(url: str, page: _Page) -> str:
    blob = " ".join(page.jsonld)
    if '"Product"' in blob or page.meta.get("og:type") == "product":
        return "product"
    parsed = urlparse(url)
    if parsed.path in ("", "/"):
        return "home"
    probe = (parsed.path + ("?" + parsed.query if parsed.query else "")).lower()
    for ptype, pat in PAGE_TYPE_PATTERNS:
        if re.search(pat, probe):
            return ptype
    return "other"


def detect_signals(page: _Page, html_lower: str) -> dict[str, bool]:
    text = " ".join(page.text).lower() + " " + html_lower[:200_000]
    sig = {name: bool(re.search(pat, text)) for name, pat in SIGNAL_KEYWORDS.items()}
    sig["search"] = page.has_search_input
    sig["add_to_cart"] = "add_to_cart" in page.selectors
    return sig


class _Fetcher:
    def __init__(self, base: str, delay: float, respect_robots: bool = True) -> None:
        self.delay = delay
        self.last = 0.0
        self.robots = None
        if respect_robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(urljoin(base, "/robots.txt"), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
                self.robots = rp
            except Exception:
                self.robots = None  # sin robots.txt accesible = sin restricciones declaradas

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(UA, url) if self.robots else True

    def get(self, url: str) -> str | None:
        wait = self.delay - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if "html" not in r.headers.get("Content-Type", ""):
                    return None
                return r.read(MAX_BYTES).decode("utf-8", "replace")
        except Exception:
            return None


def crawl(base_url: str, max_pages: int = 40, per_type: int = 8, delay: float = 0.5,
          respect_robots: bool = True) -> dict:
    base = urlparse(base_url)
    host = base.netloc
    fetcher = _Fetcher(base_url, delay, respect_robots)
    queue: deque[str] = deque([base_url])
    seen: set[str] = set()
    pages: list[dict] = []
    per_type_count: Counter = Counter()
    selectors: dict[str, str] = {}
    free_shipping_text = ""

    while queue and len(pages) < max_pages:
        url = urldefrag(queue.popleft())[0]
        if url in seen:
            continue
        seen.add(url)
        if not fetcher.allowed(url):
            continue
        html = fetcher.get(url)
        if html is None:
            continue
        page = _Page()
        page.feed(html)
        ptype = classify(url, page)
        if per_type_count[ptype] >= per_type:
            continue  # ya hay muestra suficiente de este tipo; prioriza variedad
        per_type_count[ptype] += 1
        signals = detect_signals(page, html.lower())
        if not free_shipping_text:
            m = re.search(r"[^.]{0,60}(env[ií]o (gratis|gratuito)|free shipping)[^.]{0,60}", " ".join(page.text), re.I)
            free_shipping_text = m.group(0).strip() if m else ""
        for k, v in page.selectors.items():
            selectors.setdefault(f"{ptype}:{k}", v)
        pages.append({"url": url, "page_type": ptype, "title": page.title.strip()[:120], "signals": signals})
        for href in page.links:
            nxt = urldefrag(urljoin(url, href))[0]
            p = urlparse(nxt)
            if p.scheme in ("http", "https") and p.netloc == host and nxt not in seen:
                queue.append(nxt)

    return build_profile(base_url, pages, selectors, free_shipping_text)


def build_profile(base_url: str, pages: list[dict], selectors: dict[str, str], free_shipping_text: str = "") -> dict:
    types = Counter(p["page_type"] for p in pages)
    agg: dict[str, dict] = {}
    for sig in list(SIGNAL_KEYWORDS) + ["search", "add_to_cart"]:
        where = sorted({p["page_type"] for p in pages if p["signals"].get(sig)})
        agg[sig] = {"present": bool(where), "page_types": where}
    # Selector por señal: se prefiere el de la página donde tiene sentido (producto/carrito...).
    flat: dict[str, str] = {}
    prefer = {"reviews": "product", "shipping_info": "product", "size_guide": "product",
              "add_to_cart": "product", "search": "home", "wishlist": "product", "coupon": "cart"}
    for key in ("reviews", "shipping_info", "size_guide", "add_to_cart", "search", "wishlist", "coupon"):
        cands = [(k, v) for k, v in selectors.items() if k.endswith(f":{key}")]
        cands.sort(key=lambda kv: kv[0] != f"{prefer[key]}:{key}")
        if cands:
            flat[key] = cands[0][1]
    return {
        "base_url": base_url,
        "pages_crawled": len(pages),
        "page_types": dict(types),
        "signals": agg,
        "selectors": flat,
        "free_shipping_text": free_shipping_text,
        "sample_urls": {t: [p["url"] for p in pages if p["page_type"] == t][:3] for t in types},
        "pages": pages,
    }
