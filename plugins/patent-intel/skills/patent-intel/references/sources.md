# Data sources: the zero-key default, and the official upgrade path

## Default backend (no key): Google Patents public search

The CLI uses the same public JSON endpoint the patents.google.com search page
uses. Coverage is worldwide (US, EP, WO, KR, JP, CN, ...), bibliographic
fields + snippets, updated continuously.

Being a guest on an unofficial endpoint comes with duties, which the CLI
enforces by default:

- **Cache first.** Every response is cached 24h (`~/.cache/patent-intel/`);
  repeated questions cost zero requests. `--fresh` bypasses when needed.
- **Pace.** Minimum 2s between requests (`PATENT_INTEL_DELAY` env raises it).
- **Back off.** Bursts trigger Google's abuse detection (HTTP 503 "Sorry"
  page) for the whole IP, typically for tens of minutes. The CLI reports this
  clearly instead of retry-hammering. Datacenter IPs (CI runners) are blocked
  more aggressively than residential ones.

Rule of thumb: interactive research (a handful of queries per session) is
fine; scripted bulk collection is not what this backend is for.

## Official APIs (free keys) — for bulk, production, or CI use

| Source | What | Key | Notes |
|---|---|---|---|
| USPTO Open Data Portal | US applications/grants, full-text search | free API key (api.uspto.gov) | modern JSON API |
| EPO OPS | worldwide bibliographic families (DOCDB) | free key, ~4GB/month (developers.epo.org) | OAuth2 client-credentials |
| KIPRIS Plus | Korean patents/trademarks/designs (특허청) | free tier key (plus.kipris.or.kr) | Korean-language fields |
| Google Patents Public Datasets | full corpus on BigQuery | GCP account | SQL at scale; ideal for serious analytics |

The CLI is structured so official backends can slot in behind environment
keys (`USPTO_API_KEY`, `EPO_KEY`/`EPO_SECRET`, `KIPRIS_API_KEY`) — this is the
roadmap for v0.2. Contributions welcome.

## Legality, in one paragraph

Patent publications are public documents; bibliographic patent data is not
copyrightable subject matter in most jurisdictions (and factual data carries
no copyright). This tool queries a public endpoint politely, caches to
minimize load, stores no personal data, and redistributes only small
aggregate snapshots (counts, rankings, links). It is a research tool; nothing
it outputs is legal advice.
