"""Línea de comandos: crawl → (eventos) → analyze → generate → serve → experiment."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import ai, analysis, crawler, experiments, report as report_md, server, simulator, tools
from .events import read_jsonl, sessionize, write_jsonl

STATIC = Path(__file__).parent / "static"


def _dump(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(path: str | None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else None


def cmd_crawl(a) -> None:
    profile = crawler.crawl(a.url, max_pages=a.max_pages, delay=a.delay)
    _dump(profile, Path(a.out))
    print(f"Rastreadas {profile['pages_crawled']} páginas → {a.out}")
    for sig, v in profile["signals"].items():
        print(f"  {'✔' if v['present'] else '✘'} {sig}")


def cmd_simulate(a) -> None:
    n = write_jsonl(simulator.simulate(a.sessions, a.seed), a.out)
    print(f"{n} eventos de {a.sessions} sesiones sintéticas → {a.out}")


def cmd_analyze(a) -> None:
    rep = analysis.analyze(sessionize(read_jsonl(a.events)))
    _dump(rep, Path(a.out))
    s = rep["summary"]
    print(f"{s['sessions']} sesiones, conversión {s['conversion_rate']:.1%}. Informe → {a.out}")
    for d in rep["drivers"]:
        if d["significant"]:
            print(f"  ★ {d['event']}: conv {d['cr_with']:.1%} vs {d['cr_without']:.1%} (OR ajustado {d.get('adj_odds_ratio')})")


def cmd_generate(a) -> None:
    rep = _load(a.report)
    if rep is None:
        sys.exit(f"No existe {a.report}: ejecuta primero `analyze`.")
    profile = _load(a.profile)
    out = Path(a.out)
    config = tools.generate_tools(rep, profile, holdout_pct=a.holdout)
    enhanced = ai.enhance(rep, config, profile) if not a.no_ai else None
    if enhanced is None and not a.no_ai and not ai.available():
        print("(sin ANTHROPIC_API_KEY: copy y resumen deterministas)")
    ai.apply_copy(config, enhanced)
    _dump(config, out / "cro-config.json")
    shutil.copy(STATIC / "cro-widget.js", out / "cro-widget.js")
    exp = None
    if a.events and Path(a.events).exists():
        res = experiments.evaluate(sessionize(read_jsonl(a.events)))
        exp = res if res["overall"]["treatment"]["sessions"] else None
    (out / "informe.md").write_text(report_md.render(rep, config, enhanced and enhanced["narrative"], exp), encoding="utf-8")
    print(f"{len(config['tools'])} herramientas, {len(config['gaps'])} gaps → {out}/ (cro-config.json, cro-widget.js, informe.md)")


def cmd_serve(a) -> None:
    srv = server.serve(a.dir, a.events, a.port, a.allow_origin)
    print(f"Sirviendo en http://localhost:{a.port}  (widget: /cro-widget.js · eventos → {a.events})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_experiment(a) -> None:
    res = experiments.evaluate(sessionize(read_jsonl(a.events)))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_demo(a) -> None:
    d = Path(a.dir)
    write_jsonl(simulator.simulate(a.sessions, a.seed), d / "events.jsonl")
    cmd_analyze(argparse.Namespace(events=str(d / "events.jsonl"), out=str(d / "report.json")))
    cmd_generate(argparse.Namespace(report=str(d / "report.json"), profile=None, out=str(d / "out"),
                                    holdout=10, events=None, no_ai=a.no_ai))
    print(f"\nListo. Lee {d}/out/informe.md")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cro-ai", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="rastrea la tienda y perfila sus páginas/señales")
    c.add_argument("url"); c.add_argument("--max-pages", type=int, default=40)
    c.add_argument("--delay", type=float, default=0.5); c.add_argument("--out", default="data/site_profile.json")
    c.set_defaults(f=cmd_crawl)

    c = sub.add_parser("simulate", help="genera eventos sintéticos para probar el pipeline")
    c.add_argument("--sessions", type=int, default=5000); c.add_argument("--seed", type=int, default=7)
    c.add_argument("--out", default="data/events.jsonl"); c.set_defaults(f=cmd_simulate)

    c = sub.add_parser("analyze", help="detecta patrones que llevan a comprar")
    c.add_argument("--events", default="data/events.jsonl"); c.add_argument("--out", default="data/report.json")
    c.set_defaults(f=cmd_analyze)

    c = sub.add_parser("generate", help="genera herramientas CRO (config + widget + informe)")
    c.add_argument("--report", default="data/report.json"); c.add_argument("--profile", default="data/site_profile.json")
    c.add_argument("--out", default="out"); c.add_argument("--holdout", type=int, default=10, help="%% de sesiones de control")
    c.add_argument("--events", help="eventos en vivo para incluir el resultado del experimento")
    c.add_argument("--no-ai", action="store_true"); c.set_defaults(f=cmd_generate)

    c = sub.add_parser("serve", help="sirve el widget y recoge eventos")
    c.add_argument("--dir", default="out"); c.add_argument("--events", default="data/live_events.jsonl")
    c.add_argument("--port", type=int, default=8000); c.add_argument("--allow-origin", default="*")
    c.set_defaults(f=cmd_serve)

    c = sub.add_parser("experiment", help="mide tratamiento vs control")
    c.add_argument("--events", default="data/live_events.jsonl"); c.set_defaults(f=cmd_experiment)

    c = sub.add_parser("demo", help="simula → analiza → genera, de extremo a extremo")
    c.add_argument("--dir", default="data/demo"); c.add_argument("--sessions", type=int, default=5000)
    c.add_argument("--seed", type=int, default=7); c.add_argument("--no-ai", action="store_true")
    c.set_defaults(f=cmd_demo)

    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
