---
name: patent-intel
description: Patent research and landscape analysis over public patent data, with zero API keys. Use this skill whenever the user asks about patents or IP in any form — prior art, "has anyone patented X?", who is patenting a technology, a company's patent portfolio, patent counts, trends, landscapes, competitor IP watch, or Korean 특허 questions — even if they never say the words "patent search". Also use it when the user wants a written patent-landscape report for a technology area.
---

# patent-intel

Turn patent questions into measured answers. This skill wraps a zero-dependency
CLI (`scripts/patent_search.py`, Python 3.8+, stdlib only) around Google Patents'
public search endpoint, so you can count, list, rank, and trend patent
publications worldwide without any API key.

## The tool

```
python "${CLAUDE_SKILL_DIR}/scripts/patent_search.py" <command> ... [--json]
```

(Windows PowerShell: `python "$env:CLAUDE_SKILL_DIR\scripts\patent_search.py" ...`)

| Command | What it answers | Example |
|---|---|---|
| `count QUERY` | "How many patents mention X?" | `count '"agentic AI"'` |
| `search QUERY --num 10 --sort new` | "Show me recent filings about X" | `search '"KV cache"' --sort new` |
| `leaderboard QUERY` | "Who files the most patents about X?" | `leaderboard '"retrieval augmented generation"'` |
| `trend QUERY --from 2019 --to 2026` | "Is patenting in X growing?" | `trend '"large language model"'` |
| `portfolio COMPANY` | "What does company Y patent?" | `portfolio "Anthropic"` |
| `compare QUERY --assignees "A,B,C"` | "Who leads between A, B, C?" | `compare '"AI agent"' --assignees "OpenAI,Microsoft,Salesforce"` |
| `report --domain SLUG --out F.md` | "Give me a landscape report on X" | `report --domain agentic-ai --out report.md` |
| `domains` | list curated domain query packs | |
| `selftest [--live]` | verify the tool works | |

Every command takes `--json` for machine-readable output — prefer it when you
need to post-process results. Shared filters: `--assignee`, `--inventor`,
`--country US|KR|EP|WO|...`, `--status GRANT|APPLICATION`, `--after YYYY`,
`--before YYYY`, `--date-field filing|priority|publication`.

## Workflow

1. **Map the question to a command.** Vague area question ("what's happening in
   AI inference patents?") → check `domains` for a matching pack, then
   `report --domain ai-inference`. Specific phrase → `count`/`leaderboard`/
   `search`. Company question → `portfolio` or `compare`.
2. **Quote multi-word phrases** inside the query: `'"solid state battery"'`,
   not `solid state battery` (unquoted words are ANDed loosely). Full syntax:
   [references/search-syntax.md](references/search-syntax.md).
3. **Company names are legal entities.** "Google DeepMind" finds little — use
   "DeepMind Technologies". Microsoft files as "Microsoft Technology Licensing".
   If a portfolio looks suspiciously small, try name variants before concluding.
4. **Present numbers with their meaning attached** (see below), and link the
   publications you cite — every search result includes a Google Patents URL.

## Interpreting the data honestly

Get these right or the answer is misleading:

- **Counts are patent *publications*, not granted patents.** One invention can
  appear as several publications (application + grant + family members across
  countries). Say "patent publications" or "families", filter
  `--status GRANT` when the user asks about granted patents only.
- **Recent years always look smaller than they are.** Applications publish
  ~18 months after filing, so the last ~2 years of any trend are undercounted.
  Never report "filings dropped in the latest year" without this caveat.
- **Leaderboard facets are sampled** by the source (bucket sums can exceed the
  total). Read them as relative shares — "Salesforce leads" is safe,
  "Salesforce has exactly 57" is not.
- **A rate-limit error is not a bug.** Results are cached 24h; if the tool
  reports being rate-limited, tell the user to retry in ~10-30 minutes instead
  of hammering. Keep sessions under ~10 fresh (uncached) queries.

## Boundaries

This is bibliographic research, not legal work. Never conclude "this doesn't
infringe", "you are free to operate", or "this idea is unpatentable" from these
results — say what the data shows (similar prior filings exist / few hits
found) and recommend a patent attorney (변리사) for legal judgments.

## Going deeper

- Query syntax details: [references/search-syntax.md](references/search-syntax.md)
- Domain packs and how to extend them: [references/domains.md](references/domains.md)
- Official APIs (USPTO/EPO/KIPRIS/BigQuery) for bulk or production use:
  [references/sources.md](references/sources.md)
