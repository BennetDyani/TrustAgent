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
uv run python -m trustagent.rag.ingest   # contracts + policy manual -> pgvector (~1 min, free-tier paced)
uv run pytest
```

### Run the demo

```bash
uv run python scripts/reset_demo.py --with-samples                     # clean demo data, 5 JSON cases ready
uv run uvicorn trustagent.api.app:app --port 8000                      # API (docs at http://localhost:8000/docs)
uv run streamlit run src/trustagent/ui/app.py                          # UI at http://localhost:8501
```

In the UI, choose who you are **acting as** (Finance Analyst, Finance Manager or Department Head). Upload a PDF
or Markdown invoice (read by the AI) or JSON (read by code), open the case, and **run** the investigation to
watch the evidence stream in. Decision buttons appear only afterwards, and a disabled one shows why. Verify a
supplier's new bank account on the **Suppliers** page (phone and email, from trusted sources), re-run, and
approve as a different person.

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
| `03_rag_ingestion_and_retrieval.ipynb` | Manifest metadata, boilerplate removal, structure-aware chunks, normalised embeddings, RRF, vector vs keyword vs hybrid side by side, the expired-contract filter, citations, "no contract on file", and the live contract check on INV-1048 | real (Gemini embeddings + chat); read-only |
| `04_langgraph_investigation.ipynb` | The graph (Mermaid), a live BEC investigation streamed step by step, pause at `interrupt()`, the bank-change hold, supplier verification by phone and email, resume and approve, checkpoint history, audit trail, dual approval, graceful degradation, token usage | real (Gemini); scratch database |
