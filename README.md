# CRO-AI

Software que **aprende qué hace la gente que compra en un e-commerce** y **genera herramientas CRO** para llevar al resto de visitantes por ese camino.

```
 rastreo del sitio ─┐
                    ├─► análisis de comportamiento ─► viaje ideal + palancas ─► herramientas CRO ─► widget en la tienda
 eventos de usuarios┘        (estadística)               (qué empuja a comprar)    (nudges + holdout)         │
        ▲                                                                                                    │
        └────────────────────── /collect: el widget devuelve eventos y mide el efecto real ◄─────────────────┘
```

## Qué hace cada pieza

| Módulo | Función |
|---|---|
| `crawler.py` | Recorre la tienda (mismo dominio, respeta `robots.txt`), clasifica páginas y detecta qué señales de conversión tiene o le faltan (reseñas, envío, guía de tallas, sellos de confianza…) y selectores CSS. |
| `static/cro-widget.js` | **Tracker + motor de nudges** (una línea `<script>`). Registra el comportamiento real de los visitantes y muestra las herramientas según disparadores. |
| `analysis.py` | Embudo, **palancas** (regresión logística: efecto de cada comportamiento controlando por los demás y por dispositivo), secuencias A→B→C con alta conversión y el **viaje ideal** (orden típico de hitos de quien compra). |
| `tools.py` | Traduce los hallazgos en herramientas con disparador, copy, evidencia, prioridad e impacto estimado. |
| `ai.py` | *Opcional.* Claude redacta el resumen ejecutivo y mejora el copy. **No calcula nada** y no puede afirmar lo que no esté verificado. |
| `experiments.py` | Compara tratamiento vs. control (z-test) para saber si cada herramienta funciona de verdad. |
| `server.py` | Sirve el widget/config y recibe eventos en `/collect`. |

### Herramientas CRO que genera

`social_proof_prompt` (reseñas) · `shipping_reassurance` · `size_guide_prompt` · `search_prompt` · `recently_viewed` (comparar lo visto) · `cart_recovery` (exit-intent en el carrito) · `checkout_reassurance` · `coupon_reminder` (solo si hay evidencia).
Solo se crea una herramienta si el comportamiento tiene efecto **estadísticamente significativo**. Si la mejor palanca no existe en la tienda, se reporta como *gap* en el informe (p. ej. "tu mejor palanca son las reseñas y no tienes").

## Uso rápido (sin dependencias, Python ≥ 3.10)

```bash
python -m cro_ai demo                     # simula 5000 sesiones → analiza → genera. Lee data/demo/out/informe.md
```

Flujo real:

```bash
python -m cro_ai crawl https://mi-tienda.com            # 1. perfil del sitio  → data/site_profile.json
#   instala <script src="https://TU-SERVIDOR/cro-widget.js" async></script> y deja el widget solo recoger datos
python -m cro_ai serve --events data/live_events.jsonl --port 8000 --allow-origin https://mi-tienda.com
python -m cro_ai analyze --events data/live_events.jsonl # 2. patrones (necesita miles de sesiones)
python -m cro_ai generate --out out                      # 3. cro-config.json + cro-widget.js + informe.md
python -m cro_ai serve --dir out                         # 4. el widget empieza a guiar a los usuarios
python -m cro_ai experiment --events data/live_events.jsonl   # 5. ¿funcionan? tratamiento vs control
```

Con `ANTHROPIC_API_KEY` definida, `generate` usa Claude para el resumen y el copy (`CRO_AI_MODEL` para cambiar de modelo). Sin ella funciona igual con texto determinista.

### Integrar con tu tienda

El widget detecta el tipo de página por `<meta name="cro-page-type" content="product|cart|checkout|confirmation|category|home">` (o por URL) y engancha eventos por selectores CSS editables en `cro-config.json` (`event_selectors`). Para eventos propios: `croTrack('add_to_cart', {value: 59.9})`. En la página de confirmación añade `<meta name="cro-order-value" content="129.90">`.

## Rigor: lo que hay que saber

- **Correlación ≠ causalidad.** Que quien lee reseñas compre más no prueba que obligar a leerlas venda más (los compradores ya eran más propensos). Por eso: (1) el odds ratio se ajusta por el resto de comportamientos, (2) la estimación de impacto lleva un descuento explícito y es solo una *hipótesis*, (3) el widget reserva un **grupo de control** (10 % por defecto) y `experiment` mide el efecto real. Escala solo lo que gane.
- **Marcadores de etapa.** El análisis separa lo que ocurre *antes* y *después* de añadir al carrito; si no, eventos como "aplicar cupón" (solo posible con carrito lleno) parecerían la causa de comprar.
- **Tamaño de muestra.** Con < 100 sesiones se niega a concluir; el veredicto de un experimento exige ≥ 200 sesiones por grupo.
- **Copy honesto.** Las herramientas no prometen envío gratis, devoluciones ni escasez que el rastreo no haya verificado en la tienda; si no hay perfil, el informe marca el copy como "revisar".
- **Privacidad.** Sin cookies de terceros ni datos personales (solo ID de sesión aleatorio en `sessionStorage`). El widget no actúa con `window.croConsent === false`, Do-Not-Track o `localStorage.cro_consent = 'denied'`: conecta tu CMP de cookies a ese flag si lo exige tu jurisdicción (RGPD/ePrivacy).
- **Rastreo.** Úsalo solo en sitios propios o con permiso. Lee HTML estático; las tiendas 100 % JS necesitan el tracker para ver el DOM real.
- La simulación (`simulator.py`) tiene causas ocultas conocidas y los tests comprueban que el análisis las redescubre y que no se deja engañar por el cupón.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Siguientes pasos naturales

Segmentación por dispositivo/origen en las herramientas, UI de revisión/aprobación del copy, almacenamiento en base de datos en vez de JSONL, y bandit multi-brazo en vez de A/B fijo.
