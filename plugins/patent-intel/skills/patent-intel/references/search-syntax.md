# Google Patents query syntax

The CLI passes your query through to Google Patents search. What works in the
search box at patents.google.com works here.

## Text queries

| Pattern | Meaning |
|---|---|
| `"solid state battery"` | exact phrase |
| `battery electrode` | both words, loosely related |
| `("tool calling" OR "function calling")` | either phrase |
| `"language model" -translation` | exclude a term |
| `G06N10/00` | CPC classification code as a term |

Always shell-quote queries containing spaces or quotes:
`count '"agentic AI"'` (single quotes outside, double quotes inside).

## Field filters (CLI flags)

| Flag | Values | Notes |
|---|---|---|
| `--assignee` | company name | legal entity spelling; try variants |
| `--inventor` | person name | |
| `--country` | `US`, `KR`, `EP`, `WO`, `JP`, `CN`, ... | publication office |
| `--status` | `GRANT`, `APPLICATION` | grants only vs applications |
| `--after` / `--before` | `YYYY`, `YYYYMM`, `YYYYMMDD` | date range |
| `--date-field` | `filing`, `priority`, `publication` | which date the range applies to |
| `--sort` (search) | `new` | most recent first; default is relevance |
| `--num` (search) | 1-100 | results per call |

## Practical recipes

- Recent Korean filings on a topic:
  `search '"defect detection"' --country KR --after 2024 --sort new`
- Granted US patents only:
  `count '"retrieval augmented generation"' --country US --status GRANT`
- One company's activity in one area:
  `count '"language model"' --assignee "Salesforce"`
- Everything a startup has published:
  `portfolio "Mistral AI"`

## Known quirks

- Assignee matching is a substring-ish match on the recorded legal name.
  "DeepMind Technologies" works; "Google DeepMind" mostly doesn't.
- The assignee leaderboard facet is sampled (capped around 1000); treat the
  numbers as shares, not exact counts.
- `num` caps at 100 per request; use `--sort new` + date filters to page
  through time windows instead of deep pagination.
