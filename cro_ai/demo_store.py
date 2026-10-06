"""Tienda demo mínima (HTML generado) con el widget ya integrado, para probar el sistema.

Rutas: /, /category/zapatillas, /product/1..6, /cart, /checkout, /thank-you
Usa los mismos selectores/ids que espera cro-config.json por defecto.
"""
from __future__ import annotations

from html import escape

PRODUCTS = {
    1: ("Runner Pro", 89.9), 2: ("Urban Classic", 64.9), 3: ("Trail Max", 109.0),
    4: ("Casual Lite", 49.9), 5: ("Sprint Air", 79.5), 6: ("Walker Comfort", 59.0),
}

CSS = """body{font:16px/1.5 system-ui,sans-serif;margin:0;color:#1a1a1a}header{background:#111;color:#fff;padding:12px 24px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
header a{color:#fff;text-decoration:none}header form{margin-left:auto}.promo{background:#e8f3ff;text-align:center;padding:6px;font-size:14px}
main{max-width:900px;margin:24px auto;padding:0 16px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px}
.card{border:1px solid #e3e3e3;border-radius:10px;padding:14px}.card a{color:inherit;text-decoration:none}.img{background:#f1f1f1;height:120px;border-radius:8px;margin-bottom:8px}
button,.btn{font:inherit;background:#111;color:#fff;border:0;border-radius:8px;padding:10px 16px;cursor:pointer;text-decoration:none;display:inline-block}
.sec{border-top:1px solid #eee;margin-top:28px;padding-top:12px}input{font:inherit;padding:8px;border:1px solid #ccc;border-radius:6px}.sub{color:#666;font-size:14px}"""


def _page(title: str, ptype: str, body: str, extra_meta: str = "", widget_src: str = "/cro-widget.js") -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Tienda Demo</title><meta name="cro-page-type" content="{ptype}">{extra_meta}<style>{CSS}</style></head><body>
<div class="promo">Envío gratis en pedidos de más de 50 € · Devoluciones gratuitas en 30 días</div>
<header><a href="/"><b>Tienda Demo</b></a><a href="/category/zapatillas">Zapatillas</a><a href="/cart">Carrito</a>
<form action="/category/zapatillas" method="get"><input type="search" name="q" placeholder="Buscar…"></form></header>
<main>{body}</main><script src="{widget_src}" async></script></body></html>"""


def _cards() -> str:
    return "".join(
        f'<div class="card"><a href="/product/{i}"><div class="img"></div><b>{escape(n)}</b><div class="sub">{p:.2f} €</div></a></div>'
        for i, (n, p) in PRODUCTS.items())


def render(path: str) -> str | None:
    path = path.split("?")[0].rstrip("/") or "/"
    if path == "/":
        return _page("Inicio", "home", f"<h1>Zapatillas para todos</h1><p>Descubre nuestra colección.</p><div class='grid'>{_cards()}</div>")
    if path == "/category/zapatillas":
        return _page("Zapatillas", "category", f"<h1>Zapatillas</h1><div class='grid'>{_cards()}</div>")
    if path.startswith("/product/") and path[9:].isdigit() and int(path[9:]) in PRODUCTS:
        name, price = PRODUCTS[int(path[9:])]
        body = f"""<h1>{escape(name)}</h1><div class="img" style="height:220px"></div><p><b>{price:.2f} €</b></p>
<button class="add-to-cart" data-cro="add-to-cart">Añadir al carrito</button>
<button data-cro="wishlist" style="background:#555">♡ Favoritos</button>
<button data-cro="size-guide" style="background:#555">Guía de tallas</button>
<div style="height:600px" class="sub">(Descripción del producto…)</div>
<section id="shipping" class="shipping-info sec"><h3>Envío y devoluciones</h3><p>Envío gratis en pedidos de más de 50 €. Devoluciones gratuitas en 30 días.</p></section>
<section id="reviews" class="reviews sec"><h3>Opiniones de clientes ★★★★☆ 4,6</h3><p>«Muy cómodas, talla perfecta.» · «Llegaron en 48 h.»</p></section>"""
        return _page(name, "product", body, f'<meta name="cro-product-price" content="{price}">')
    if path == "/cart":
        body = """<h1>Tu carrito</h1><p>1 × Producto — resumen del pedido</p>
<div data-cro="coupon"><form class="coupon" onsubmit="return false"><input placeholder="Código de descuento"> <button>Aplicar</button></form></div>
<p><a class="btn" href="/checkout">Ir al pago</a></p>"""
        return _page("Carrito", "cart", body)
    if path == "/checkout":
        body = """<h1>Pago</h1><p class="sub">Pago seguro.</p><p><input autocomplete="cc-number" placeholder="Número de tarjeta"></p>
<a class="btn" href="/thank-you">Pagar ahora</a>"""
        return _page("Pago", "checkout", body)
    if path == "/thank-you":
        return _page("Gracias", "confirmation", "<h1>¡Gracias por tu compra!</h1>", '<meta name="cro-order-value" content="89.9">')
    return None
