# TrustAgent

AI investigation platform for supplier invoice fraud (business email compromise, changed bank
details, duplicate billing, threshold splitting). **Rules decide the facts; the LLM gives
judgement; people make the payment decision.**

> Work in progress, built in phases. See [DECISIONS.md](DECISIONS.md) for the reasoning behind
> each design choice. The full README (architecture, evaluation results, security, limitations)
> arrives in phase 8.

## Run it (Windows, PowerShell or Git Bash)

Prerequisites: [uv](https://docs.astral.sh/uv/) and Docker Desktop (running).

```bash
uv python install 3.12
uv sync
cp .env.example .env            # then add GOOGLE_API_KEY and GROQ_API_KEY
docker compose up -d            # Postgres 16 + pgvector on localhost:5433
uv run alembic upgrade head
uv run python -m trustagent.db.seed
uv run pytest
```

### Tests

```bash
uv run pytest            # offline: rules, extraction (LLM faked), database (needs Docker)
uv run pytest -m live    # real model calls: the 11 sample invoices vs hand-labelled ground truth
uv run python scripts/mutation_check.py   # confirms the tests catch deliberate rule breakages
```

### Notebooks

```bash
uv run jupyter lab notebooks/
```

| Notebook | Shows | Model |
|---|---|---|
| `01_extraction.ipynb` | PDF → text → as-printed fields → validated invoice; the last-4-digit trap; OCR honesty; redaction; accuracy on all samples | real (Gemini) |
| `02_rules_and_scoring.ipynb` | Rule checks and scores for all 11 samples; why LOW, why CRITICAL; duplicates vs recurring invoices; minimum action | real extraction; rules offline. Runs in a rolled-back transaction |
| `04_langgraph_investigation.ipynb` | The graph (Mermaid), a live BEC investigation streamed step by step, pause at `interrupt()`, the bank-change hold, supplier verification by phone and email, resume and approve, checkpoint history, audit trail, dual approval, graceful degradation, token usage | real (Gemini); scratch database |
