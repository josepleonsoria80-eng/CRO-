/*!
 * CRO-AI widget: tracker de comportamiento + motor de herramientas CRO.
 *
 * Instalación (una línea en la tienda):
 *   <script src="https://TU-SERVIDOR/cro-widget.js" async></script>
 *
 * - Registra eventos de comportamiento (mismo vocabulario que usa el análisis).
 * - Lee cro-config.json (generado por `cro-ai generate`) y muestra nudges según disparadores.
 * - Reparte las sesiones entre `treatment` y `control` (holdout) para medir el efecto real.
 * - Respeta consentimiento: no hace nada si window.croConsent === false, si el navegador
 *   envía Do-Not-Track o si localStorage.cro_consent === 'denied'. No guarda datos personales.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  var BASE = script && script.src ? script.src.replace(/[^\/]*$/, '') : '/';
  var STORE_KEY = 'cro_state_v1';

  function denied() {
    try {
      return window.croConsent === false || navigator.doNotTrack === '1' ||
        localStorage.getItem('cro_consent') === 'denied';
    } catch (e) { return window.croConsent === false; }
  }
  if (denied()) return;

  // ------------------------------------------------------------------ estado de sesión
  var state;
  function load() {
    try { state = JSON.parse(sessionStorage.getItem(STORE_KEY)); } catch (e) { state = null; }
    if (!state || !state.sid) {
      state = {
        sid: 's' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36),
        seen: {}, shown: [], dismissed: [], products: [], pageViews: 0, bucket: Math.random(), first: true
      };
    }
  }
  function save() { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* modo privado */ } }
  load();
  // Solo para pruebas manuales en TU navegador: ?cro_variant=treatment|control y ?cro_speed=5 (temporizadores x5 más rápidos).
  try {
    var qp = new URLSearchParams(location.search);
    if (qp.get('cro_variant')) state.force = qp.get('cro_variant');
    if (qp.get('cro_speed')) state.speed = Math.max(1, parseFloat(qp.get('cro_speed')) || 1);
    save();
  } catch (e) { /* sin URLSearchParams */ }
  function speed() { return state.speed || 1; }

  var cfg = null, pageType = 'other', pageStart = Date.now(), exitFlag = false, timers = [];
  var visibleNow = null; // nudge en pantalla
  var lastNudgeAt = 0;
  var isTouch = ('ontouchstart' in window) || navigator.maxTouchPoints > 0;

  // ------------------------------------------------------------------ envío de eventos
  function device() {
    var w = window.innerWidth;
    return w < 768 ? 'mobile' : (w < 1100 ? 'tablet' : 'desktop');
  }
  function source() {
    try {
      var q = new URLSearchParams(location.search).get('utm_source');
      if (q) return q.slice(0, 30);
      if (!document.referrer) return 'direct';
      var h = new URL(document.referrer).hostname;
      if (h === location.hostname) return state.src || 'direct';
      return /google|bing|duckduckgo|yahoo/.test(h) ? 'organic' : (/facebook|instagram|tiktok|twitter|t\.co|pinterest/.test(h) ? 'social' : 'referral');
    } catch (e) { return 'direct'; }
  }
  function variant() {
    if (state.force === 'treatment' || state.force === 'control') return state.force;
    var pct = (cfg && cfg.experiment && cfg.experiment.holdout_pct) || 0;
    return state.bucket * 100 < pct ? 'control' : 'treatment';
  }
  function send(name, extra) {
    var props = Object.assign({}, extra || {});
    if (cfg) props.variant = variant();
    if (state.first) { props.device = device(); props.source = source(); state.src = props.source; state.first = false; }
    var ev = { session_id: state.sid, ts: Date.now() / 1000, name: name, page_type: pageType, url: location.pathname, props: props };
    var body = JSON.stringify([ev]);
    try {
      if (!(navigator.sendBeacon && navigator.sendBeacon(BASE + 'collect', new Blob([body], { type: 'text/plain' })))) throw 0;
    } catch (e) {
      try { fetch(BASE + 'collect', { method: 'POST', body: body, keepalive: true, headers: { 'content-type': 'text/plain' } }); } catch (e2) { /* sin red */ }
    }
  }

  function track(name, extra) {
    var firstTime = !state.seen[name];
    // Los eventos de página y de conversión se registran siempre; el resto, una vez por sesión.
    if (firstTime || /^page_view_|^cro_/.test(name)) send(name, extra);
    state.seen[name] = state.seen[name] || Date.now();
    save();
    evaluate('event');
  }
  window.croTrack = track; // API manual: croTrack('add_to_cart', {value: 59.9})

  // ------------------------------------------------------------------ detección de página
  function detectPageType() {
    var m = document.querySelector('meta[name="cro-page-type"]');
    if (m && m.content) return m.content;
    var pats = (cfg && cfg.page_type_patterns) || {};
    var probe = (location.pathname + location.search).toLowerCase();
    if (location.pathname === '/' || location.pathname === '') return 'home';
    var order = ['confirmation', 'checkout', 'cart', 'search', 'product', 'category'];
    for (var i = 0; i < order.length; i++) {
      if (pats[order[i]] && new RegExp(pats[order[i]], 'i').test(probe)) return order[i];
    }
    return 'other';
  }

  // ------------------------------------------------------------------ auto-tracking por selectores
  function wireSelectors() {
    var map = (cfg && cfg.event_selectors) || {};
    Object.keys(map).forEach(function (ev) {
      var spec = map[ev];
      if (spec.on === 'visible') {
        var els = safeAll(spec.selector);
        if (!els.length || !('IntersectionObserver' in window)) return;
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) { track(ev); io.disconnect(); }
          });
        }, { threshold: 0.4 });
        els.forEach(function (el) { io.observe(el); });
      } else {
        var domEvent = { click: 'click', submit: 'submit', focus: 'focusin' }[spec.on] || 'click';
        document.addEventListener(domEvent, function (e) {
          if (e.target && e.target.closest && safeClosest(e.target, spec.selector)) track(ev, ev === 'add_to_cart' ? cartProps() : null);
        }, true);
      }
    });
  }
  function cartProps() {
    var m = document.querySelector('meta[name="cro-product-price"]');
    return m && m.content ? { value: parseFloat(m.content) || 0 } : null;
  }
  function safeAll(sel) { try { return Array.prototype.slice.call(document.querySelectorAll(sel)); } catch (e) { return []; } }
  function safeClosest(el, sel) { try { return el.closest(sel); } catch (e) { return null; } }

  // ------------------------------------------------------------------ motor de herramientas
  function triggerOk(t, reason) {
    var g = t.trigger || {};
    var types = [].concat(g.page_type || []);
    if (types.length && types.indexOf(pageType) < 0) return false;
    if (g.missing_event && state.seen[g.missing_event]) return false;
    if (g.has_event && !state.seen[g.has_event]) return false;
    if (g.min_product_views && state.products.length < g.min_product_views) return false;
    if (g.min_page_views && state.pageViews < g.min_page_views) return false;
    if (g.exit_intent) {
      if (isTouch) {
        if (!g.mobile_after_seconds || Date.now() - pageStart < g.mobile_after_seconds * 1000 / speed()) return false;
      } else if (!exitFlag) { return false; }
    } else if (g.after_seconds && Date.now() - pageStart < g.after_seconds * 1000 / speed()) {
      return false;
    }
    return true;
  }

  function evaluate(reason) {
    if (!cfg || visibleNow) return;
    var exp = cfg.experiment || {};
    if (state.shown.length >= (exp.max_nudges_per_session || 2)) return;
    if (Date.now() - lastNudgeAt < (exp.min_seconds_between_nudges || 20) * 1000 / speed()) return;
    var tools = (cfg.tools || []).slice().sort(function (a, b) { return a.priority - b.priority; });
    for (var i = 0; i < tools.length; i++) {
      var t = tools[i];
      if (t.enabled === false) continue;                       // desactivada desde el backoffice
      if (state.shown.indexOf(t.id) >= 0 || state.dismissed.indexOf(t.id) >= 0) continue;
      if (!triggerOk(t, reason)) continue;
      fire(t);
      return;
    }
  }

  function fire(t) {
    state.shown.push(t.id); save();
    send('cro_eligible', { tool_id: t.id });          // se registra en control Y tratamiento
    lastNudgeAt = Date.now();                         // mismas reglas de frecuencia en ambos grupos
    if (variant() === 'control') return;              // el control no ve nada
    render(t);
    send('cro_impression', { tool_id: t.id });
  }

  function armTimers() {
    timers.forEach(clearTimeout); timers = [];
    (cfg.tools || []).forEach(function (t) {
      var g = t.trigger || {};
      var s = g.exit_intent ? (isTouch ? g.mobile_after_seconds : 0) : g.after_seconds;
      if (s) timers.push(setTimeout(function () { evaluate('timer'); }, s * 1000 / speed() + 50));
    });
  }

  // ------------------------------------------------------------------ UI
  var CSS = '.cro-nudge{position:fixed;z-index:2147483000;right:16px;bottom:16px;max-width:340px;background:#fff;color:#1a1a1a;' +
    'border:1px solid #e3e3e3;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.18);padding:14px 16px;font:14px/1.45 system-ui,sans-serif;' +
    'animation:cro-in .25s ease-out}@media(max-width:600px){.cro-nudge{left:12px;right:12px;bottom:12px;max-width:none}}' +
    '.cro-nudge h4{margin:0 24px 4px 0;font-size:15px}.cro-nudge p{margin:0 0 10px}.cro-nudge button{font:inherit;cursor:pointer}' +
    '.cro-cta{background:#1a1a1a;color:#fff;border:0;border-radius:8px;padding:8px 14px}.cro-x{position:absolute;top:6px;right:8px;' +
    'background:none;border:0;font-size:18px;color:#777}.cro-list{list-style:none;margin:0 0 10px;padding:0}' +
    '.cro-list a{color:#0b57d0;text-decoration:none}@keyframes cro-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text; // textContent: el copy nunca se interpreta como HTML
    return n;
  }

  function render(t) {
    if (!document.getElementById('cro-style')) {
      var st = el('style'); st.id = 'cro-style'; st.textContent = CSS; document.head.appendChild(st);
    }
    var box = el('div', 'cro-nudge'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', t.content.title);
    var x = el('button', 'cro-x', '×'); x.setAttribute('aria-label', 'Cerrar');
    box.appendChild(x);
    box.appendChild(el('h4', '', t.content.title));
    box.appendChild(el('p', '', t.content.body));
    if (t.type === 'recently_viewed') {
      var ul = el('ul', 'cro-list');
      state.products.slice(-4).reverse().forEach(function (p) {
        var li = el('li'); var a = el('a', '', p.title || p.url);
        if (/^\//.test(p.url)) a.href = p.url;
        li.appendChild(a); ul.appendChild(li);
      });
      box.appendChild(ul);
    } else if (t.action && t.action.type !== 'none' && t.content.cta) {
      var b = el('button', 'cro-cta', t.content.cta);
      b.addEventListener('click', function () { send('cro_click', { tool_id: t.id }); runAction(t.action); close(false); });
      box.appendChild(b);
    }
    function close(user) {
      if (user) { state.dismissed.push(t.id); save(); send('cro_dismiss', { tool_id: t.id }); }
      if (box.parentNode) box.parentNode.removeChild(box);
      visibleNow = null;
    }
    x.addEventListener('click', function () { close(true); });
    document.body.appendChild(box);
    visibleNow = box;
    setTimeout(function () { if (visibleNow === box) close(false); }, 20000);
  }

  function runAction(a) {
    var node = a.selector ? safeAll(a.selector)[0] : null;
    if (a.type === 'scroll' && node) node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    else if (a.type === 'focus' && node) node.focus();
    else if (a.type === 'click' && node) node.click();
    else if (a.type === 'link' && a.href && /^\/(?!\/)/.test(a.href)) location.href = a.href; // solo rutas del propio sitio
  }

  // ------------------------------------------------------------------ arranque
  function start() {
    pageType = detectPageType();
    state.pageViews++;
    if (pageType === 'product') {
      var last = state.products[state.products.length - 1];
      if (!last || last.url !== location.pathname) state.products.push({ url: location.pathname, title: document.title.slice(0, 80) });
    }
    save();
    track('page_view_' + pageType);
    ((cfg.page_events || {})[pageType] || []).forEach(function (ev) {
      track(ev, ev === 'purchase' ? purchaseProps() : null);
    });
    wireSelectors();
    armTimers();
    document.addEventListener('mouseout', function (e) {
      if (!e.relatedTarget && e.clientY <= 0) { exitFlag = true; evaluate('exit'); }
    });
  }
  function purchaseProps() {
    var m = document.querySelector('meta[name="cro-order-value"]');
    return m && m.content ? { value: parseFloat(m.content) || 0 } : null;
  }

  function init() {
    fetch(BASE + 'cro-config.json', { cache: 'no-cache' })
      .then(function (r) { return r.json(); })
      .then(function (c) { cfg = c; start(); })
      .catch(function () { cfg = { tools: [], event_selectors: {}, page_events: {}, experiment: {} }; start(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
