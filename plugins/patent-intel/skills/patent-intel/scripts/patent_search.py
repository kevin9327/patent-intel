#!/usr/bin/env python3
"""patent-intel — patent research CLI over public patent data.

Zero-setup: uses Google Patents' public search endpoint (no API key).
Stdlib only (Python 3.8+), so it runs anywhere Python runs.

Be polite: requests are cached on disk and paced (2s+ between calls).
Heavy scripted use will get you temporarily blocked by Google — that is
your problem, not your users'. For bulk work, use official APIs
(see references/sources.md) or BigQuery public patent datasets.

Usage examples:
  python patent_search.py count '"agentic AI"'
  python patent_search.py leaderboard '"retrieval augmented generation"'
  python patent_search.py trend '"large language model"' --from 2020 --to 2026
  python patent_search.py search '"KV cache"' --num 10 --sort new
  python patent_search.py portfolio "Anthropic"
  python patent_search.py compare '"AI agent"' --assignees "OpenAI,Microsoft,Salesforce"
  python patent_search.py report --domain agentic-ai --out report.md
  python patent_search.py domains
  python patent_search.py snapshot --repo path/to/repo
  python patent_search.py selftest [--live]

All commands accept --json for machine-readable output.
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "0.1.0"
BASE = "https://patents.google.com/xhr/query"
HEADERS = {
    # A browser-like UA is required; generic client UAs are refused outright.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://patents.google.com/",
    "Accept": "application/json",
}
MIN_INTERVAL = float(os.environ.get("PATENT_INTEL_DELAY", "2.0"))
CACHE_DIR = Path(
    os.environ.get("PATENT_INTEL_CACHE", str(Path.home() / ".cache" / "patent-intel"))
)
CACHE_TTL_HOURS = 24.0

_last_request_ts = 0.0


class RateLimitedError(RuntimeError):
    """Google's abuse detection kicked in (HTTP 429/503 'Sorry' page)."""


# --------------------------------------------------------------------------
# Domain packs — curated query sets per technology area.
# Queries use Google Patents syntax (see references/search-syntax.md).
# The first query of each domain is its "flagship" (used for trends).
# --------------------------------------------------------------------------
DOMAINS = {
    "agentic-ai": {
        "name": "Agentic AI",
        "description": "AI agents, tool use, orchestration, multi-agent systems built on LLMs.",
        "queries": [
            '"agentic AI"',
            '"AI agent" "large language model"',
            '("tool calling" OR "function calling") "language model"',
            '"multi-agent" "large language model"',
        ],
    },
    "llm-core": {
        "name": "LLM Core",
        "description": "Large language model architecture, training, and fine-tuning.",
        "queries": [
            '"large language model"',
            '"transformer model" attention',
            '"fine-tuning" "language model"',
        ],
    },
    "rag": {
        "name": "Retrieval-Augmented Generation",
        "description": "RAG pipelines, vector search, embeddings, grounding.",
        "queries": [
            '"retrieval augmented generation"',
            '"vector database" embedding',
            '"semantic search" "language model"',
        ],
    },
    "ai-inference": {
        "name": "AI Inference & Serving",
        "description": "Model serving, quantization, KV cache, inference acceleration.",
        "queries": [
            '"KV cache"',
            '"model quantization"',
            'inference acceleration "language model"',
        ],
    },
    "ai-chips": {
        "name": "AI Chips",
        "description": "NPUs, AI accelerators, datacenter AI silicon.",
        "queries": [
            '"neural processing unit"',
            '"AI accelerator" chip',
            '"tensor processing"',
        ],
    },
    "humanoid-robotics": {
        "name": "Humanoid Robotics",
        "description": "Humanoid robots, imitation learning, robot foundation models.",
        "queries": [
            '"humanoid robot"',
            'robot "imitation learning"',
            'robot "foundation model"',
        ],
    },
    "autonomous-driving": {
        "name": "Autonomous Driving",
        "description": "Self-driving perception, planning, end-to-end driving models.",
        "queries": [
            '"autonomous driving" perception',
            '"end-to-end" "autonomous driving"',
            '"occupancy network"',
        ],
    },
    "ev-battery": {
        "name": "EV Battery",
        "description": "Lithium-ion and next-gen battery cells, materials, manufacturing.",
        "queries": [
            '"solid state battery"',
            '"lithium ion battery" electrode',
            '"silicon anode"',
        ],
    },
    "industrial-inspection": {
        "name": "Industrial Inspection AI",
        "description": "Machine vision defect detection for manufacturing (wafers, cells, displays).",
        "queries": [
            '"defect detection" "deep learning"',
            '"wafer defect" inspection',
            '"machine vision" "defect detection"',
        ],
    },
    "digital-health": {
        "name": "Digital Health AI",
        "description": "AI diagnosis, medical imaging, digital therapeutics.",
        "queries": [
            '"medical imaging" "deep learning" diagnosis',
            '"digital therapeutic"',
            '"clinical decision support" "machine learning"',
        ],
    },
    "quantum-computing": {
        "name": "Quantum Computing",
        "description": "Qubits, error correction, quantum-classical hybrid systems.",
        "queries": [
            '"quantum computing" qubit',
            '"quantum error correction"',
        ],
    },
    "space-tech": {
        "name": "Space Tech",
        "description": "Satellite constellations, launch systems, in-orbit servicing.",
        "queries": [
            '"satellite constellation"',
            '"reusable launch vehicle"',
        ],
    },
}

# Frontier-lab / big-tech watchlist for portfolio snapshots.
# Values are assignee search strings (legal-entity spellings matter).
WATCHLIST = [
    "OpenAI",
    "Anthropic",
    "DeepMind Technologies",
    "NVIDIA",
    "Microsoft Technology Licensing",
    "Meta Platforms",
    "Apple",
    "Samsung Electronics",
    "NAVER",
    "Tesla",
    "xAI",
    "Mistral AI",
]


# --------------------------------------------------------------------------
# HTTP layer: pacing, cache, retry
# --------------------------------------------------------------------------
def _cache_path(inner_query: str) -> Path:
    digest = hashlib.md5(inner_query.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _cache_get(inner_query: str, ttl_hours: float):
    p = _cache_path(inner_query)
    if not p.exists():
        return None
    age_h = (time.time() - p.stat().st_mtime) / 3600.0
    if age_h > ttl_hours:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(inner_query: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(inner_query).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # cache is best-effort


def _throttle() -> None:
    global _last_request_ts
    wait = MIN_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def _http_get_json(inner_query: str) -> dict:
    url = BASE + "?url=" + urllib.parse.quote(inner_query, safe="")
    req = urllib.request.Request(url, headers=HEADERS)
    _throttle()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            raise RateLimitedError(
                "Google Patents temporarily blocked this client (HTTP %d). "
                "This happens after bursts of queries. Wait 10-30 minutes, rely on "
                "cached results, or raise PATENT_INTEL_DELAY (current: %.1fs). "
                "For bulk work use official APIs — see references/sources.md."
                % (e.code, MIN_INTERVAL)
            ) from e
        raise
    if body.lstrip().startswith("<"):
        raise RateLimitedError(
            "Google Patents returned an HTML block page instead of JSON. "
            "Wait 10-30 minutes and try again, or use cached results."
        )
    return json.loads(body)


def fetch(inner_query: str, fresh: bool = False, ttl_hours: float = CACHE_TTL_HOURS) -> dict:
    """Fetch one query (inner querystring like 'q=...&assignee=...'), with cache."""
    if not fresh:
        cached = _cache_get(inner_query, ttl_hours)
        if cached is not None:
            return cached
    payload = _http_get_json(inner_query)
    _cache_put(inner_query, payload)
    return payload


# --------------------------------------------------------------------------
# Query building & parsing
# --------------------------------------------------------------------------
def build_inner(
    query: str = "",
    assignee: str = "",
    inventor: str = "",
    country: str = "",
    status: str = "",
    after: str = "",
    before: str = "",
    date_field: str = "filing",
    num: int = 0,
    sort: str = "",
    page: int = 0,
) -> str:
    parts = []
    if query:
        parts.append("q=" + query)
    if assignee:
        parts.append("assignee=" + assignee)
    if inventor:
        parts.append("inventor=" + inventor)
    if country:
        parts.append("country=" + country)
    if status:
        parts.append("status=" + status)
    if after:
        parts.append("after=%s:%s" % (date_field, _norm_date(after)))
    if before:
        parts.append("before=%s:%s" % (date_field, _norm_date(before)))
    if num:
        parts.append("num=%d" % num)
    if sort:
        parts.append("sort=" + sort)
    if page:
        parts.append("page=%d" % page)
    if not parts:
        raise ValueError("empty query: provide a search query or an --assignee")
    return "&".join(parts)


def _norm_date(d: str) -> str:
    d = d.strip().replace("-", "")
    if len(d) == 4:
        return d + "0101"
    if len(d) == 6:
        return d + "01"
    if len(d) == 8:
        return d
    raise ValueError("dates must be YYYY, YYYYMM, or YYYYMMDD (got %r)" % d)


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def parse_total(payload: dict) -> int:
    return int(payload.get("results", {}).get("total_num_results", 0))


def parse_results(payload: dict) -> list:
    """Flatten result items into dicts with clean fields and links."""
    out = []
    for cluster in payload.get("results", {}).get("cluster", []) or []:
        for item in cluster.get("result", []) or []:
            if not isinstance(item, dict):
                continue
            p = item.get("patent") or {}
            pid = item.get("id", "")  # e.g. "patent/KR20260050351A/en"
            out.append(
                {
                    "publication_number": p.get("publication_number", ""),
                    "title": _clean(p.get("title", "")),
                    "assignee": _clean(p.get("assignee", "")),
                    "inventor": _clean(p.get("inventor", "")),
                    "filing_date": p.get("filing_date", ""),
                    "publication_date": p.get("publication_date", ""),
                    "priority_date": p.get("priority_date", ""),
                    "snippet": _clean(p.get("snippet", "")),
                    "link": ("https://patents.google.com/" + pid) if pid else "",
                }
            )
    return out


def parse_assignee_facet(payload: dict) -> list:
    """Top assignees for a query.

    Note: this facet is computed by Google over a sampled/expanded result set
    (its 'Total' bucket is capped at 1000 and bucket sums can exceed
    total_num_results). Treat values as relative shares, not exact counts.
    """
    facet = payload.get("results", {}).get("summary", {}).get("assignee", []) or []
    out = []
    for entry in facet:
        key = entry.get("key", "")
        if key == "Total":
            continue
        out.append({"assignee": key, "count": int(entry.get("value", 0))})
    return out


# --------------------------------------------------------------------------
# High-level operations
# --------------------------------------------------------------------------
def op_count(args) -> dict:
    inner = build_inner(
        query=args.query, assignee=args.assignee, inventor=args.inventor,
        country=args.country, status=args.status, after=getattr(args, "after", ""),
        before=getattr(args, "before", ""), date_field=args.date_field,
    )
    total = parse_total(fetch(inner, fresh=args.fresh))
    return {"query": inner, "total": total}


def op_search(args) -> dict:
    inner = build_inner(
        query=args.query, assignee=args.assignee, inventor=args.inventor,
        country=args.country, status=args.status, after=getattr(args, "after", ""),
        before=getattr(args, "before", ""), date_field=args.date_field,
        num=min(args.num, 100), sort=args.sort,
    )
    payload = fetch(inner, fresh=args.fresh)
    return {
        "query": inner,
        "total": parse_total(payload),
        "results": parse_results(payload)[: args.num],
    }


def op_leaderboard(args) -> dict:
    inner = build_inner(
        query=args.query, country=args.country, status=args.status,
        after=getattr(args, "after", ""), before=getattr(args, "before", ""),
        date_field=args.date_field,
    )
    payload = fetch(inner, fresh=args.fresh)
    return {
        "query": inner,
        "total": parse_total(payload),
        "note": "facet counts are sampled by the source; read as relative shares",
        "leaderboard": parse_assignee_facet(payload)[: args.top],
    }


def op_trend(args) -> dict:
    years = list(range(args.year_from, args.year_to + 1))
    series = []
    for y in years:
        inner = build_inner(
            query=args.query, assignee=args.assignee, country=args.country,
            after=str(y), before=str(y + 1), date_field=args.date_field,
        )
        series.append({"year": y, "count": parse_total(fetch(inner, fresh=args.fresh))})
    return {
        "query": args.query,
        "date_field": args.date_field,
        "note": "recent years are undercounted: applications publish ~18 months after filing",
        "series": series,
    }


def op_portfolio(args) -> dict:
    inner_total = build_inner(assignee=args.company)
    total = parse_total(fetch(inner_total, fresh=args.fresh))
    inner_recent = build_inner(assignee=args.company, num=min(args.num, 100), sort="new")
    recent = parse_results(fetch(inner_recent, fresh=args.fresh))[: args.num]
    return {"company": args.company, "total_publications": total, "recent": recent}


def op_compare(args) -> dict:
    rows = []
    for company in [c.strip() for c in args.assignees.split(",") if c.strip()]:
        inner = build_inner(query=args.query, assignee=company)
        rows.append(
            {"assignee": company, "count": parse_total(fetch(inner, fresh=args.fresh))}
        )
    rows.sort(key=lambda r: -r["count"])
    return {"query": args.query or "(all publications)", "comparison": rows}


def op_domains(args) -> dict:
    return {
        "domains": [
            {"slug": slug, "name": d["name"], "description": d["description"],
             "queries": d["queries"]}
            for slug, d in DOMAINS.items()
        ]
    }


def build_domain_report(slug: str, fresh: bool = False, trend_from: int = 2019,
                        include_trend: bool = True) -> dict:
    """Collect all data for one domain (several network calls, cached).

    include_trend=False keeps the request count low (~5 instead of ~13) —
    datacenter IPs get roughly a 10-request budget before Google blocks them.
    """
    domain = DOMAINS[slug]
    flagship = domain["queries"][0]
    this_year = date.today().year

    volumes = []
    for q in domain["queries"]:
        volumes.append({"query": q, "total": parse_total(fetch(build_inner(query=q), fresh=fresh))})

    # same inner query as the flagship volume fetch -> served from cache
    lb_payload = fetch(build_inner(query=flagship))
    leaderboard = parse_assignee_facet(lb_payload)[:15]

    series = None
    if include_trend:
        series = []
        for y in range(trend_from, this_year + 1):
            inner = build_inner(query=flagship, after=str(y), before=str(y + 1))
            series.append({"year": y, "count": parse_total(fetch(inner, fresh=fresh))})

    recent_payload = fetch(build_inner(query=flagship, num=10, sort="new"), fresh=fresh)
    recent = parse_results(recent_payload)[:10]

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "domain": slug,
        "name": domain["name"],
        "description": domain["description"],
        "flagship_query": flagship,
        "query_volumes": volumes,
        "top_filers_sampled": leaderboard,
        "filing_trend": series,
        "fresh_filings": recent,
    }


def render_domain_report_md(data: dict) -> str:
    lines = []
    lines.append("# %s — Patent Landscape (%s)" % (data["name"], data["generated"]))
    lines.append("")
    lines.append("> %s" % data["description"])
    lines.append("> Source: Google Patents public search. Facet counts are sampled;")
    lines.append("> recent-year filing counts grow for ~18 months as applications publish.")
    lines.append("")
    lines.append("## Query volumes")
    lines.append("")
    lines.append("| Query | Publications |")
    lines.append("|---|---|")
    for v in data["query_volumes"]:
        lines.append("| `%s` | %s |" % (v["query"], format(v["total"], ",")))
    lines.append("")
    lines.append("## Top filers — `%s` (sampled)" % data["flagship_query"])
    lines.append("")
    lines.append("| # | Assignee | Count |")
    lines.append("|---|---|---|")
    for i, row in enumerate(data["top_filers_sampled"], 1):
        lines.append("| %d | %s | %d |" % (i, row["assignee"], row["count"]))
    lines.append("")
    if data.get("filing_trend"):
        lines.append("## Filing trend — `%s`" % data["flagship_query"])
        lines.append("")
        lines.append("| Year | Filings | |")
        lines.append("|---|---|---|")
        peak = max((r["count"] for r in data["filing_trend"]), default=1) or 1
        for r in data["filing_trend"]:
            bar = "█" * max(1, round(r["count"] / peak * 30)) if r["count"] else ""
            lines.append("| %d | %s | %s |" % (r["year"], format(r["count"], ","), bar))
        lines.append("")
    lines.append("## Fresh filings")
    lines.append("")
    for r in data["fresh_filings"]:
        lines.append(
            "- [%s](%s) — %s (%s, filed %s)"
            % (r["publication_number"], r["link"],
               r["title"][:110], r["assignee"] or "n/a", r["filing_date"] or "n/a")
        )
    lines.append("")
    lines.append("---")
    lines.append("*Generated by [patent-intel](https://github.com/kevin9327/patent-intel). "
                 "Public bibliographic data; not legal advice.*")
    lines.append("")
    return "\n".join(lines)


def op_report(args) -> dict:
    if args.domain not in DOMAINS:
        raise SystemExit(
            "unknown domain %r — run `domains` to list available packs" % args.domain
        )
    data = build_domain_report(args.domain, fresh=args.fresh)
    md = render_domain_report_md(data)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        return {"written": args.out, "domain": args.domain}
    return {"markdown": md, "data": data}


def op_snapshot(args) -> dict:
    """Write dated snapshot JSONs + reports into a repo layout.

    Used by the weekly GitHub Action to make this repo self-accumulating.
    Degrades gracefully: when the source rate-limits mid-run, everything
    collected so far is still written and the run reports status "partial"
    (exit code stays 0 — a partial snapshot is still an accumulation).
    """
    repo = Path(args.repo)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap_dir = repo / "data" / "snapshots" / today
    snap_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = repo / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    slugs = [s.strip() for s in args.domains.split(",") if s.strip()]
    written = []
    status, note = "complete", ""

    for slug in slugs:
        try:
            data = build_domain_report(slug, fresh=args.fresh,
                                       include_trend=not args.lite)
        except RateLimitedError as e:
            status, note = "partial", str(e)
            break
        out = snap_dir / ("domain-%s.json" % slug)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(str(out))
        md_path = reports_dir / ("%s-%s.md" % (today, slug))
        md_path.write_text(render_domain_report_md(data), encoding="utf-8")
        written.append(str(md_path))

    watchlist = []
    if status == "complete":
        for company in WATCHLIST[: args.watchlist_top]:
            try:
                total = parse_total(fetch(build_inner(assignee=company), fresh=args.fresh))
            except RateLimitedError as e:
                status, note = "partial", str(e)
                break
            watchlist.append({"assignee": company, "total_publications": total})
    if watchlist:
        wl_path = snap_dir / "watchlist.json"
        wl_path.write_text(
            json.dumps({"generated": today, "watchlist": watchlist},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        written.append(str(wl_path))

    run_info = {"snapshot_date": today, "status": status, "note": note,
                "written": written}
    if written:
        (snap_dir / "run.json").write_text(
            json.dumps(run_info, ensure_ascii=False, indent=1), encoding="utf-8")
    return run_info


# --------------------------------------------------------------------------
# Self-test: offline parse checks against a real captured payload
# --------------------------------------------------------------------------
_FIXTURE = {
    "results": {
        "total_num_results": 207,
        "cluster": [{"result": [{
            "id": "patent/KR20260050351A/en",
            "patent": {
                "title": " Wafer-level Deterministic AI Processor &hellip;",
                "snippet": "&hellip; such as <b>Agentic AI</b> applications.",
                "priority_date": "2026-03-28",
                "filing_date": "2026-03-28",
                "publication_date": "2026-04-14",
                "inventor": "안범주",
                "assignee": "안범주",
                "publication_number": "KR20260050351A",
            },
        }]}],
        "summary": {"assignee": [
            {"key": "Total", "value": 1000},
            {"key": "Salesforce, Inc.", "value": 57},
        ]},
    }
}


def op_selftest(args) -> dict:
    checks = []

    def check(name, ok):
        checks.append({"check": name, "ok": bool(ok)})

    check("parse_total", parse_total(_FIXTURE) == 207)
    rows = parse_results(_FIXTURE)
    check("parse_results_count", len(rows) == 1)
    r = rows[0]
    check("html_stripped", "hellip" not in r["title"] and "<b>" not in r["snippet"])
    check("link_built", r["link"] == "https://patents.google.com/patent/KR20260050351A/en")
    lb = parse_assignee_facet(_FIXTURE)
    check("facet_skips_total", len(lb) == 1 and lb[0]["assignee"].startswith("Salesforce"))
    check("date_norm", _norm_date("2024") == "20240101" and _norm_date("2024-06-15") == "20240615")
    inner = build_inner(query='"x y"', assignee="A B", after="2024", num=10)
    check("inner_build", inner == 'q="x y"&assignee=A B&after=filing:20240101&num=10')

    if args.live:
        payload = _http_get_json('q="agentic AI"&num=3')
        check("live_total_positive", parse_total(payload) > 0)
        check("live_items_parse", len(parse_results(payload)) > 0)

    passed = sum(1 for c in checks if c["ok"])
    return {"passed": passed, "failed": len(checks) - passed, "checks": checks}


# --------------------------------------------------------------------------
# Output rendering
# --------------------------------------------------------------------------
def _print_human(cmd: str, data: dict) -> None:
    if cmd == "count":
        print("%s  →  %s publications" % (data["query"], format(data["total"], ",")))
    elif cmd == "search":
        print("total: %s\n" % format(data["total"], ","))
        for r in data["results"]:
            print("%-18s %-12s %s" % (r["publication_number"], r["filing_date"],
                                      (r["assignee"] or "?")[:34]))
            print("    %s" % r["title"][:120])
            print("    %s" % r["link"])
    elif cmd == "leaderboard":
        print("query: %s   (total %s; sampled facet)\n"
              % (data["query"], format(data["total"], ",")))
        for i, row in enumerate(data["leaderboard"], 1):
            print("%3d. %-45s %d" % (i, row["assignee"][:45], row["count"]))
    elif cmd == "trend":
        peak = max((r["count"] for r in data["series"]), default=1) or 1
        for r in data["series"]:
            bar = "█" * max(1, round(r["count"] / peak * 40)) if r["count"] else ""
            print("%d  %10s  %s" % (r["year"], format(r["count"], ","), bar))
        print("\nnote: %s" % data["note"])
    elif cmd == "portfolio":
        print("%s — %s publications total\n"
              % (data["company"], format(data["total_publications"], ",")))
        for r in data["recent"]:
            print("%-18s %-12s %s" % (r["publication_number"], r["filing_date"],
                                      r["title"][:95]))
    elif cmd == "compare":
        print("query: %s\n" % data["query"])
        for row in data["comparison"]:
            print("%-40s %s" % (row["assignee"], format(row["count"], ",")))
    elif cmd == "domains":
        for d in data["domains"]:
            print("%-24s %s" % (d["slug"], d["name"]))
            print("%-24s %s" % ("", d["description"]))
    elif cmd == "report" and "markdown" in data:
        print(data["markdown"])
    elif cmd == "selftest":
        for c in data["checks"]:
            print("[%s] %s" % ("ok" if c["ok"] else "FAIL", c["check"]))
        print("\n%d passed, %d failed" % (data["passed"], data["failed"]))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(prog="patent_search.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, query=True):
        if query:
            p.add_argument("query", nargs="?", default="",
                           help="search query (Google Patents syntax)")
        p.add_argument("--assignee", default="")
        p.add_argument("--inventor", default="")
        p.add_argument("--country", default="", help="e.g. US, KR, EP, WO")
        p.add_argument("--status", default="", help="GRANT or APPLICATION")
        p.add_argument("--after", default="", help="YYYY[MM[DD]]")
        p.add_argument("--before", default="", help="YYYY[MM[DD]]")
        p.add_argument("--date-field", default="filing",
                       choices=["filing", "priority", "publication"])
        p.add_argument("--fresh", action="store_true", help="bypass the 24h cache")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("count", help="publication count for a query"); common(p)
    p = sub.add_parser("search", help="list matching publications"); common(p)
    p.add_argument("--num", type=int, default=10)
    p.add_argument("--sort", default="", help="'new' for most recent first")
    p = sub.add_parser("leaderboard", help="top assignees for a query"); common(p)
    p.add_argument("--top", type=int, default=15)
    p = sub.add_parser("trend", help="yearly counts for a query"); common(p)
    p.add_argument("--from", dest="year_from", type=int, default=2019)
    p.add_argument("--to", dest="year_to", type=int, default=date.today().year)
    p = sub.add_parser("portfolio", help="a company's publications")
    p.add_argument("company")
    p.add_argument("--num", type=int, default=10)
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("compare", help="one query across several companies"); common(p)
    p.add_argument("--assignees", required=True, help="comma-separated company names")
    p = sub.add_parser("domains", help="list curated domain query packs")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("report", help="full landscape report for a domain")
    p.add_argument("--domain", required=True)
    p.add_argument("--out", default="", help="write markdown to this path")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("snapshot", help="write dated snapshot data+reports into a repo")
    p.add_argument("--repo", required=True)
    p.add_argument("--domains", default="agentic-ai")
    p.add_argument("--lite", action="store_true",
                   help="skip trends (~5 requests/domain instead of ~13)")
    p.add_argument("--watchlist-top", type=int, default=len(WATCHLIST),
                   help="only the first N watchlist companies")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("selftest", help="offline parser checks (+ --live smoke test)")
    p.add_argument("--live", action="store_true")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    ops = {
        "count": op_count, "search": op_search, "leaderboard": op_leaderboard,
        "trend": op_trend, "portfolio": op_portfolio, "compare": op_compare,
        "domains": op_domains, "report": op_report, "snapshot": op_snapshot,
        "selftest": op_selftest,
    }
    try:
        data = ops[args.cmd](args)
    except RateLimitedError as e:
        print("rate-limited: %s" % e, file=sys.stderr)
        return 2
    except (urllib.error.URLError, TimeoutError) as e:
        print("network error: %s" % e, file=sys.stderr)
        return 3

    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        _print_human(args.cmd, data)
    if args.cmd == "selftest" and data["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
