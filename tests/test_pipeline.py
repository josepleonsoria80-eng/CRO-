import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cro_ai import ai, analysis, crawler, experiments, server, tools
from cro_ai.events import Event, read_jsonl, sessionize, write_jsonl
from cro_ai.simulator import simulate


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = sessionize(simulate(5000, seed=7))
        cls.report = analysis.analyze(cls.sessions)

    def test_rediscovers_hidden_drivers(self):
        sig = {d["event"] for d in self.report["drivers"] if d["significant"]}
        self.assertEqual(sig, {"view_reviews", "view_shipping_info", "search", "view_size_guide"})

    def test_cart_stage_marker_is_not_a_driver(self):
        # El cupón solo existe con el carrito lleno y no tiene efecto causal en la simulación.
        coupon = next(d for d in self.report["drivers"] if d["event"] == "apply_coupon")
        self.assertEqual(coupon["stage"], "cart")
        self.assertFalse(coupon["significant"])

    def test_ideal_journey_is_ordered_and_ends_in_purchase(self):
        j = [s["event"] for s in self.report["ideal_journey"]]
        self.assertEqual(j[-1], "purchase")
        self.assertLess(j.index("view_reviews"), j.index("add_to_cart"))
        self.assertLess(j.index("add_to_cart"), j.index("begin_checkout"))

    def test_funnel_is_monotonic_after_cart(self):
        steps = {f["step"]: f["sessions"] for f in self.report["funnel"]}
        self.assertGreaterEqual(steps["add_to_cart"], steps["begin_checkout"])
        self.assertGreaterEqual(steps["begin_checkout"], steps["purchase"])

    def test_too_few_sessions_raises(self):
        with self.assertRaises(ValueError):
            analysis.analyze(sessionize(simulate(50)))

    def test_logistic_fit_recovers_coefficient(self):
        import random
        import math
        rng = random.Random(1)
        rows, y = [], []
        for _ in range(4000):
            x = rng.random() < 0.5
            p = 1 / (1 + math.exp(-(-1.0 + 1.5 * x)))
            rows.append([0] if x else [])
            y.append(int(rng.random() < p))
        w, _ = analysis.logistic_fit(rows, y, l2=0.01)
        self.assertAlmostEqual(w[1], 1.5, delta=0.25)

    def test_jsonl_roundtrip(self):
        evs = simulate(120, seed=3)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.jsonl"
            write_jsonl(evs, p)
            self.assertEqual(len(read_jsonl(p)), len(evs))


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analysis.analyze(sessionize(simulate(5000, seed=7)))

    def test_tools_only_for_significant_levers(self):
        cfg = tools.generate_tools(self.report)
        ids = {t["id"] for t in cfg["tools"]}
        self.assertIn("social_proof_prompt-view_reviews", ids)
        self.assertNotIn("coupon_reminder-apply_coupon", ids)
        self.assertEqual([t["priority"] for t in cfg["tools"]], list(range(1, len(cfg["tools"]) + 1)))

    def test_gap_when_store_lacks_the_lever(self):
        profile = crawler.build_profile("http://x", [
            {"url": "http://x/p/1", "page_type": "product", "title": "", "signals": {"shipping_info": True, "search": True, "size_guide": True}},
        ], {})
        cfg = tools.generate_tools(self.report, profile)
        self.assertTrue(any(g["event"] == "view_reviews" for g in cfg["gaps"]))
        self.assertFalse(any(t["id"].endswith("view_reviews") for t in cfg["tools"]))
        self.assertTrue(all(t["claims_verified"] for t in cfg["tools"]))

    def test_copy_does_not_claim_unverified_shipping_terms(self):
        cfg = tools.generate_tools(self.report, None)
        ship = next(t for t in cfg["tools"] if t["type"] == "shipping_reassurance")
        self.assertNotIn("gratis", ship["content"]["body"].lower())
        self.assertFalse(ship["claims_verified"])


class AITest(unittest.TestCase):
    def test_enhance_validates_and_clips(self):
        report = analysis.analyze(sessionize(simulate(3000, seed=7)))
        cfg = tools.generate_tools(report)
        first = cfg["tools"][0]["id"]
        reply = json.dumps({"narrative": "Resumen", "copy": {
            first: {"title": "T" * 200, "body": "ok", "cta": 5},
            "no-existe": {"title": "x"}}})
        out = ai.enhance(report, cfg, None, call=lambda prompt: "```json\n" + reply + "\n```")
        self.assertEqual(set(out["copy"]), {first})
        self.assertEqual(len(out["copy"][first]["title"]), 45)
        self.assertNotIn("cta", out["copy"][first])
        ai.apply_copy(cfg, out)
        self.assertEqual(cfg["tools"][0]["copy_source"], "ai")

    def test_enhance_falls_back_on_garbage(self):
        report = analysis.analyze(sessionize(simulate(3000, seed=7)))
        cfg = tools.generate_tools(report)
        self.assertIsNone(ai.enhance(report, cfg, None, call=lambda p: "no es json"))


class ExperimentTest(unittest.TestCase):
    def test_detects_uplift_among_eligible_sessions_only(self):
        import random
        rng = random.Random(5)
        evs = []
        for i in range(2000):
            variant = "control" if i % 2 else "treatment"
            sid = f"s{i}"
            evs.append(Event(sid, i, "page_view_product", props={"variant": variant}))
            evs.append(Event(sid, i + .1, "cro_eligible", props={"variant": variant, "tool_id": "t1"}))
            if rng.random() < (0.12 if variant == "treatment" else 0.06):
                evs.append(Event(sid, i + .2, "purchase", props={"variant": variant, "value": 10}))
        for i in range(500):  # sesiones nunca elegibles: no deben contar
            evs.append(Event(f"n{i}", i, "page_view_home", props={"variant": "treatment"}))
        res = experiments.evaluate(sessionize(evs))
        o = res["overall"]
        self.assertEqual(o["treatment"]["sessions"] + o["control"]["sessions"], 2000)
        self.assertEqual(o["verdict"], "mejora significativa")
        self.assertIn("t1", res["by_tool"])


class ServerTest(unittest.TestCase):
    def test_clean_event_rejects_bad_input(self):
        self.assertIsNone(server.clean_event({"name": "Bad Name!", "session_id": "a", "ts": 1}))
        self.assertIsNone(server.clean_event({"name": "ok"}))
        e = server.clean_event({"name": "ok", "session_id": "a", "ts": 1, "props": {"k": "v" * 500}})
        self.assertEqual(len(e["props"]["k"]), 80)

    def test_collect_endpoint_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            ev = Path(d) / "ev.jsonl"
            srv = server.serve(d, str(ev), port=0)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                body = json.dumps([{"name": "view_reviews", "session_id": "s1", "ts": 1.0}, {"bad": 1}]).encode()
                r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/collect", data=body, method="POST"))
                self.assertEqual(r.status, 204)
                lines = ev.read_text().strip().splitlines()
                self.assertEqual(len(lines), 1)
                js = urllib.request.urlopen(f"http://127.0.0.1:{port}/cro-widget.js").read()
                self.assertIn(b"croTrack", js)
            finally:
                srv.shutdown()
                srv.server_close()


SHOP = {
    "/": '<html><title>Tienda</title><body><form><input type="search" name="q"></form>'
         '<a href="/category/zapatillas">Zapatillas</a><a href="/cart">Carrito</a><p>Envío gratis a partir de 50 €</p></body></html>',
    "/category/zapatillas": '<html><body><a href="/product/1">Z1</a><a href="/product/2">Z2</a></body></html>',
    "/product/1": '<html><head><script type="application/ld+json">{"@type":"Product"}</script></head><body>'
                  '<div id="reviews">Opiniones de clientes ★★★★★</div><button class="add-to-cart">Añadir al carrito</button>'
                  '<a href="/product/2">otro</a></body></html>',
    "/product/2": '<html><body><p>Guía de tallas</p><button name="add-to-cart">Añadir al carrito</button></body></html>',
    "/cart": '<html><body><p>Tu carrito</p></body></html>',
    "/private/secret": '<html><body>no</body></html>',
}


class _Shop(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, ctype = "User-agent: *\nDisallow: /private/\n", "text/plain"
        elif self.path in SHOP:
            body, ctype = SHOP[self.path], "text/html"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


class CrawlerTest(unittest.TestCase):
    def test_crawl_profiles_store(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Shop)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}/"
            SHOP["/"] = SHOP["/"].replace("</body>", '<a href="/private/secret">x</a></body>')
            prof = crawler.crawl(base, max_pages=20, delay=0)
        finally:
            srv.shutdown(); srv.server_close()
        self.assertEqual(prof["page_types"].get("product"), 2)
        self.assertEqual(prof["page_types"].get("cart"), 1)
        self.assertTrue(prof["signals"]["reviews"]["present"])
        self.assertTrue(prof["signals"]["size_guide"]["present"])
        self.assertFalse(prof["signals"]["live_chat"]["present"])
        self.assertIn("envío gratis", prof["free_shipping_text"].lower())
        self.assertEqual(prof["selectors"]["reviews"], "#reviews")
        self.assertFalse(any("/private/" in p["url"] for p in prof["pages"]))  # robots.txt respetado

    def test_classify(self):
        p = crawler._Page()
        self.assertEqual(crawler.classify("http://a/checkout", p), "checkout")
        self.assertEqual(crawler.classify("http://a/", p), "home")
        self.assertEqual(crawler.classify("http://a/order-received/12", p), "confirmation")


if __name__ == "__main__":
    unittest.main()
