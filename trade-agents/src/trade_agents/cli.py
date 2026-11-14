"""Command line: `uv run tta <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from .clock import current_time, demo_time
from .config import TZ, get_settings


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


def cmd_drift(args) -> None:
    from pathlib import Path

    from .drift import detect_drift

    _print(detect_drift(json.loads(Path(args.fixture).read_text(encoding="utf-8"))))


def cmd_ingest(args) -> None:
    from .catalog import build_catalog
    from .valio_client import ValioClient

    s = get_settings()
    raw = s.raw_dir
    files = {
        "products": raw / "product_data.json",
        "delivery-reliability": raw / "delivery_reliability.csv",
        "carbon-footprint": raw / "carbon_footprint.csv",
    }
    if args.refresh or not all(p.exists() for p in files.values()):
        client = ValioClient(s)
        for kind, path in files.items():
            print(f"downloading {kind} -> {path}")
            client.download(kind, path)  # type: ignore[arg-type]
    n = build_catalog(s.catalog_db, files["products"], files["delivery-reliability"],
                      files["carbon-footprint"])
    print(f"catalogue ready: {n} products in {s.catalog_db}")


def cmd_search(args) -> None:
    from .catalog import Catalog

    cat = Catalog(get_settings().catalog_db)
    res = cat.search(args.query, require_claims=args.claim or [],
                     exclude_allergens=args.exclude or [], limit=args.limit)
    for p in res:
        size = f"{p.name_size * p.base_per_sales:g} {p.name_size_unit}" if p.name_size else "?"
        print(f"{p.id}  {p.sales_unit:4} {size:>9}  {p.name}")


def cmd_plan(args) -> None:
    from .service import Gateway, GatewayError

    try:
        _print(Gateway().plan(args.query, args.amount, args.unit, date.fromisoformat(args.date),
                              require_claims=args.claim or [],
                              exclude_allergens=args.exclude or [],
                              optimize=args.optimize, max_premium_pct=args.max_premium))
    except GatewayError as e:
        raise SystemExit(f"error: {e}") from None


def cmd_substitutes(args) -> None:
    from .service import Gateway, GatewayError

    try:
        _print(Gateway().substitutes(
            args.product_id, {"exclude_allergens": args.exclude or [],
                              "exclude_may_contain": args.may_contain,
                              "require_claims": args.claim or []},
            requested_date=date.fromisoformat(args.date) if args.date else None,
            amount=args.amount, unit=args.unit, limit=args.limit, agent_id="cli"))
    except GatewayError as e:
        raise SystemExit(f"error: {e}") from None


def cmd_quote(args) -> None:
    from .service import Gateway, GatewayError

    try:
        _print(Gateway().quote(args.ids, date.fromisoformat(args.date), args.amount, args.unit))
    except GatewayError as e:
        raise SystemExit(f"error: {e}") from None


def cmd_buyer_keys(args) -> None:
    from .signing import BuyerRegistry

    reg = BuyerRegistry(get_settings().buyers_dir)
    reg.demo_key(args.buyer_id)
    print(f"demo key pair for {args.buyer_id}: private key in "
          f"{reg.dir / (args.buyer_id + '.key.pem')} (gitignored), public JWKS registered:",
          file=sys.stderr)
    _print(reg.jwks(args.buyer_id))


def cmd_cart_confirmation(args) -> None:
    from .service import Gateway

    print(Gateway().demo_cart_confirmation(args.order_id, args.by,
                                           "decline" if args.decline else "confirm", args.note))


def cmd_passport_issue(args) -> None:
    from .service import Gateway

    print(Gateway().issue_passport(args.agent, args.model, args.harness, args.max_mode,
                                   args.bench_score, valid_days=args.days))


def cmd_schemas(args) -> None:
    from pathlib import Path

    from .protocol import SCHEMAS_DIR, write_schemas

    for path in write_schemas(Path(args.out) if args.out else SCHEMAS_DIR):
        print(path)


def cmd_mandate(args) -> None:
    from .policy import Mandate
    from .signing import BuyerRegistry

    m = Mandate(
        mandate_id=args.id, buyer_id=args.buyer, buyer_name=args.buyer_name,
        agent_id=args.agent, valid_until=current_time(datetime.now(TZ)) + timedelta(days=args.days),
        auto_order_limit_eur=args.limit, hard_limit_eur=args.hard_limit,
        excluded_allergens=args.exclude or [], required_claims=args.claim or [],
        substitution_policy=args.substitution, min_delivery_reliability=args.min_reliability,
        approvers=args.approver or [], autonomy_mode=args.mode,
    )
    key = BuyerRegistry(get_settings().buyers_dir).demo_key(args.buyer)
    _print(m.sign(key).model_dump(mode="json"))


def _print_result(r: dict) -> None:
    print(f"\n[{r['buyer']}] {r['request']}")
    for s in r["trace"]:
        flag = " !" if s["error"] else ""
        print(f"  {s['step']:>2}. {s['tool']:<16}{flag} {s['result']}")
    print(f"=> {r['status'].upper()}"
          + (f"  order {r['order_id']}" if r["order_id"] else "")
          + (f"  {r['total_eur']:.2f} EUR" if r["total_eur"] is not None else ""))
    for reason in r["reasons"]:
        print(f"   - {reason}")
    if r["flagged_products"]:
        print(f"   ! ignored instruction-like text in: {', '.join(r['flagged_products'])}")
    print(f"   {r['explanation']}")
    print(f"   ({r['model']}, {r['steps']} step(s), {r['tokens']} tokens)")


def cmd_agent(args) -> None:
    import os

    from .buyer_agent import BuyerAgent, get_profile
    from .llm import get_llm

    # A short-lived CLI process would exit before an async review finishes.
    os.environ.setdefault("TTA_VALIO_REVIEW", "sync")

    profile = get_profile(args.buyer)
    llm = get_llm(args.provider, args.model)
    agent = BuyerAgent(profile, llm, max_steps=args.max_steps)
    if args.menu:
        from .buyer_agent.menu import run_menu

        vision = get_llm(args.vision_provider, None) if args.vision_provider else llm
        res = run_menu(args.menu, args.guests, profile, llm=vision, agent=agent,
                       max_needs=args.max_needs)
        out = res.to_dict()
        if args.json:
            _print(out)
            return
        print(f"menu: {len(res.needs)} need(s) for {res.guests} guests. {res.notes}")
        for r in out["results"]:
            _print_result(r)
        return
    if not args.request:
        raise SystemExit("give a request text or --menu PATH")
    r = agent.run(args.request).to_dict()
    _print(r) if args.json else _print_result(r)


def cmd_bench(args) -> None:
    from .bench import load_scenarios, run_bench
    from .llm import get_llm

    try:
        scenarios = load_scenarios(only=args.only or None, tier=args.tier)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    for name in ("min_accuracy", "min_coverage"):
        threshold = getattr(args, name)
        if threshold is not None and not 0 <= threshold <= 1:
            raise SystemExit(f"--{name.replace('_', '-')} must be between 0 and 1")
    if not scenarios:
        raise SystemExit("no scenarios match")
    factory = lambda: get_llm(args.provider, args.model)  # noqa: E731
    progress = None if args.json else (
        lambda r: print(f"  {'skip' if r.skipped else 'pass' if r.passed else 'FAIL'} "
                        f"{r.id}", file=sys.stderr))
    report = run_bench(scenarios, factory, replay=args.replay, record_api=args.record,
                       save=not args.no_save, progress=progress)
    if args.json:
        _print(report.to_dict())
    else:
        print(report.render(verbose=args.verbose))
    coverage = len(report.scored) / len(report.results) if report.results else 0.0
    if ((args.min_accuracy is not None and report.accuracy < args.min_accuracy)
            or (args.min_coverage is not None and coverage < args.min_coverage)):
        raise SystemExit(1)


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("trade_agents.api:app", host=args.host, port=args.port, reload=args.reload)


def cmd_valio_review(args) -> None:
    from .service import Gateway
    from .valio_agent import review_order

    r = review_order(Gateway(), args.order_id, use_llm=not args.no_llm, trigger="manual")
    if args.json:
        _print(r)
        return
    print(f"risk: {r['risk']}   checks: {r['counts']}   llm: {r['llm']}")
    print(f"summary: {r['summary']}")
    for f in r["flags"]:
        print(f"  ! {f}")
    print(f"suggested action: {r['suggested_action']}")
    if r.get("suggestion"):
        print(f"suggestion (not ordered): {r['suggestion']['name']} - {r['suggestion']['why']}")
    for c in r["checks"]:
        print(f"  [{c['status']:>4}] {c['check']}: {c['evidence']}")


def cmd_mcp(args) -> None:
    from .mcp_server import main

    main()


def main(argv: list[str] | None = None) -> None:
    runtime = argparse.ArgumentParser(add_help=False)
    runtime.add_argument("--local", action="store_true",
                         help="use isolated local SQLite state and signing keys (no Postgres)")
    runtime.add_argument("--demo-clock", metavar="ISO_DATETIME",
                         help="freeze demo time with timezone; implies isolated local mode")
    flags, remaining = runtime.parse_known_args(argv)
    if flags.local:
        os.environ["TTA_LOCAL"] = "1"
    if flags.demo_clock is not None:
        os.environ["TTA_DEMO_CLOCK"] = flags.demo_clock
    try:
        at = demo_time()
    except ValueError as error:
        runtime.error(str(error))
    if at:
        print(f"DEMO CLOCK: {at.isoformat()} — isolated local state, no Postgres", file=sys.stderr)
    p = argparse.ArgumentParser(prog="tta", description="Valio Aimo agent gateway", parents=[runtime])
    sub = p.add_subparsers(required=True)

    a = sub.add_parser("drift", help="check weekly order history before renewal")
    a.add_argument("fixture")
    a.set_defaults(fn=cmd_drift)

    a = sub.add_parser("ingest", help="download data files and build the catalogue DB")
    a.add_argument("--refresh", action="store_true", help="re-download even if files exist")
    a.set_defaults(fn=cmd_ingest)

    a = sub.add_parser("search", help="search the catalogue")
    a.add_argument("query")
    a.add_argument("--claim", action="append", help="e.g. FREE_FROM_LACTOSE")
    a.add_argument("--exclude", action="append", help="allergen code or name")
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(fn=cmd_search)

    today = current_time(datetime.now(TZ)).date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    a = sub.add_parser("plan", help="cover a need, e.g. 1000 L of lactose-free milk")
    a.add_argument("query")
    a.add_argument("amount", type=float)
    a.add_argument("unit")
    a.add_argument("--date", default=monday.isoformat())
    a.add_argument("--claim", action="append")
    a.add_argument("--exclude", action="append")
    a.add_argument("--optimize", default="price",
                   choices=["price", "reliability", "co2", "balanced"])
    a.add_argument("--max-premium", type=float, help="max extra cost over the cheapest, %%")
    a.set_defaults(fn=cmd_plan)

    a = sub.add_parser("substitutes", help="allergen-safe substitutes for a product")
    a.add_argument("product_id")
    a.add_argument("--date", help="delivery date: adds live price-locked offers")
    a.add_argument("--amount", type=float)
    a.add_argument("--unit")
    a.add_argument("--exclude", action="append", help="allergen code or name, e.g. milk")
    a.add_argument("--may-contain", action="store_true",
                   help="also exclude 'may contain' traces")
    a.add_argument("--claim", action="append", help="required claim, e.g. FREE_FROM_LACTOSE")
    a.add_argument("--limit", type=int, default=10)
    a.set_defaults(fn=cmd_substitutes)

    a = sub.add_parser("quote", help="live offers for product ids")
    a.add_argument("ids", nargs="+")
    a.add_argument("--date", default=monday.isoformat())
    a.add_argument("--amount", type=float)
    a.add_argument("--unit")
    a.set_defaults(fn=cmd_quote)

    a = sub.add_parser("mandate", help="print a signed demo mandate (JSON)")
    a.add_argument("--id", default="mandate-hotel-demo")
    a.add_argument("--buyer", default="hotel-chain-001")
    a.add_argument("--buyer-name", default="Demo Hotel Chain")
    a.add_argument("--agent", default="buyer-agent")
    a.add_argument("--days", type=int, default=30)
    a.add_argument("--limit", type=float, default=1500.0, help="autonomous limit, EUR excl. VAT")
    a.add_argument("--hard-limit", type=float, default=20000.0)
    a.add_argument("--exclude", action="append")
    a.add_argument("--claim", action="append")
    a.add_argument("--substitution", default="approval",
                   choices=["never", "approval", "official_auto"])
    a.add_argument("--min-reliability", type=float, default=0.9)
    a.add_argument("--approver", action="append")
    a.add_argument("--mode", default="autonomous",
                   choices=["advise", "confirm_cart", "autonomous"], help="autonomy mode")
    a.set_defaults(fn=cmd_mandate)

    a = sub.add_parser("buyer-keys", help="create a demo Ed25519 key pair for a buyer and "
                       "register its public key")
    a.add_argument("buyer_id")
    a.set_defaults(fn=cmd_buyer_keys)

    a = sub.add_parser("passport", help="Agent Passport (Valio-signed max autonomy mode)")
    psub = a.add_subparsers(required=True)
    a = psub.add_parser("issue", help="issue a passport; prints the JWS")
    a.add_argument("--agent", default="buyer-agent", help="agent_id, must match the mandate")
    a.add_argument("--model", required=True, help="e.g. gemini-2.5-flash")
    a.add_argument("--harness", default="trade_agents.buyer_agent")
    a.add_argument("--max-mode", default="autonomous",
                   choices=["advise", "confirm_cart", "autonomous"])
    a.add_argument("--bench-score", type=float)
    a.add_argument("--days", type=float, default=7, help="validity in days (claim exp)")
    a.set_defaults(fn=cmd_passport_issue)

    a = sub.add_parser("schemas", help="write JSON Schemas of the protocol documents")
    a.add_argument("--out", help="directory (default trade-agents/schemas)")
    a.set_defaults(fn=cmd_schemas)

    a = sub.add_parser("cart-confirmation", help="sign a cart confirmation with the demo "
                       "buyer key (stand-in for the buyer's wallet); prints the JWS")
    a.add_argument("order_id")
    a.add_argument("--by", required=True, help="approver confirming, e.g. chef@hotel.example")
    a.add_argument("--decline", action="store_true")
    a.add_argument("--note", default="")
    a.set_defaults(fn=cmd_cart_confirmation)

    a = sub.add_parser("agent", help="kitchen purchasing agent: request text or menu photo")
    a.add_argument("request", nargs="?", help='e.g. "1000 litres of lactose-free milk by Monday"')
    a.add_argument("--buyer", default="hotel", help="buyer: hotel (lenient) | school | hospital (strict)")
    a.add_argument("--menu", help="photo of a weekly menu or handwritten list")
    a.add_argument("--guests", type=int, default=80)
    a.add_argument("--max-needs", type=int, default=8)
    a.add_argument("--provider", help="gemini | openai | verda | nvidia | offline "
                                      "(default TTA_LLM_PROVIDER, else first key found)")
    a.add_argument("--model", help="model id (default TTA_LLM_MODEL)")
    a.add_argument("--vision-provider", help="provider for reading the menu photo")
    a.add_argument("--max-steps", type=int, default=14)
    a.add_argument("--json", action="store_true", help="print the full result with trace")
    a.set_defaults(fn=cmd_agent)

    a = sub.add_parser("bench", help="agent bench: fixed scenarios, deterministic checks, "
                       "accuracy per category")
    a.add_argument("only", nargs="*", help="scenario ids or categories (intersect with tier)")
    a.add_argument("--tier", choices=["smoke", "core", "full"], default="full",
                   help="fixed nested scenario suite (default: full)")
    a.add_argument("--provider", help="gemini | openai | verda | nvidia | offline "
                                      "(default TTA_LLM_PROVIDER, else first key found)")
    a.add_argument("--model", help="model id (default TTA_LLM_MODEL)")
    a.add_argument("--replay", default="auto", choices=["auto", "replay", "record"],
                   help="model replies: auto = recorded first, model on a miss; replay = "
                        "recorded only; record = ask the model again")
    a.add_argument("--record", action="store_true",
                   help="live-world scenarios: call the Valio API now and record a cassette")
    a.add_argument("--min-accuracy", type=float, help="exit 1 below this (0..1), for CI")
    a.add_argument("--min-coverage", type=float,
                   help="exit 1 if scored/selected is below this (0..1); use 1 for complete replay")
    a.add_argument("--no-save", action="store_true", help="do not write bench/results/*.json")
    a.add_argument("-v", "--verbose", action="store_true", help="show every check")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_bench)

    a = sub.add_parser("serve", help="run the REST API")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    a.add_argument("--reload", action="store_true")
    a.set_defaults(fn=cmd_serve)

    a = sub.add_parser("valio-review", help="Valio's review agent: advisory review of one "
                       "order (checks + model explanation), stored on the order")
    a.add_argument("order_id")
    a.add_argument("--no-llm", action="store_true", help="deterministic checks only")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_valio_review)

    a = sub.add_parser("mcp", help="run the MCP server on stdio")
    a.set_defaults(fn=cmd_mcp)

    args = p.parse_args(remaining)
    args.fn(args)


if __name__ == "__main__":
    main()
