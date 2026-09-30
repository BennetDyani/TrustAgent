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
