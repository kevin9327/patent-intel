# Domain query packs

Curated query sets for technology areas people actually ask about. The
canonical list lives in the CLI (`patent_search.py domains`); this file
explains how to use and extend them.

## Using a pack

```
python patent_search.py report --domain agentic-ai --out landscape.md
```

produces a full markdown landscape report: query volumes, top filers,
yearly filing trend, and the freshest filings with links.

## Current packs

| Slug | Area | Flagship query |
|---|---|---|
| `agentic-ai` | AI agents, tool use, multi-agent LLM systems | `"agentic AI"` |
| `llm-core` | LLM architecture, training, fine-tuning | `"large language model"` |
| `rag` | Retrieval-augmented generation, vector search | `"retrieval augmented generation"` |
| `ai-inference` | Serving, quantization, KV cache | `"KV cache"` |
| `ai-chips` | NPUs, AI accelerators | `"neural processing unit"` |
| `humanoid-robotics` | Humanoids, imitation learning | `"humanoid robot"` |
| `autonomous-driving` | Perception, planning, end-to-end driving | `"autonomous driving" perception` |
| `ev-battery` | Cells, materials, manufacturing | `"solid state battery"` |
| `industrial-inspection` | Machine-vision defect detection | `"defect detection" "deep learning"` |
| `digital-health` | AI diagnosis, medical imaging, DTx | `"medical imaging" "deep learning" diagnosis` |
| `quantum-computing` | Qubits, error correction | `"quantum computing" qubit` |
| `space-tech` | Constellations, launch | `"satellite constellation"` |

## Designing a good pack

A pack is 2-4 queries that together bound an area:

1. **Flagship**: the phrase practitioners actually use (`"agentic AI"`), used
   for the trend chart — pick one whose usage is stable over years, or note
   that terminology adoption inflates the trend.
2. **Coverage queries**: adjacent phrasings that catch filings the flagship
   misses (`"AI agent" "large language model"`).
3. Avoid one-word queries (drowned in noise) and over-narrow phrases
   (trends of zeros).

Terminology drift is the main trap: "agentic AI" barely existed before 2023,
so its trend partly measures the phrase, not the technology. Pair it with a
technology-stable query when drawing conclusions.

To add a pack, edit `DOMAINS` in `scripts/patent_search.py` and add a row
here — PRs welcome.
