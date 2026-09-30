# Prompt: Rebuild TrustAgent in Python

> Paste everything below the line into your coding assistant (e.g. Claude Code), started in a new, empty project folder.

---

## Who you're working with and why

You're pairing with me, Bennet, a developer preparing for an interview for an **Intermediate AI Solution Engineer** role at Haibot. Haibot is an agentic AI and intelligent-automation partner that sells reusable AI modules to enterprise clients (Finance, HR, Operations and more). Its positioning is **governance-first**.

The job description asks for:

- Multi-step agentic workflows with **LangGraph / LangChain**.
- **RAG pipelines**.
- Integrating **OpenAI and Claude (Anthropic)** models into production-aligned solutions.
- **Python and SQL**.
- Testing and QA of AI components: functional validation, edge-case analysis and performance evaluation.
- Working with BAs to turn business requirements into solution architectures.

Haibot's own agent challenge judges on four things: problem relevance and business impact, agent design and reasoning, creativity and execution, and real-world applicability. Their language: *"Most AI demos look impressive. Few survive real-world problems."* and *"Developers who think in systems, not just prompts."*

**Your task:** rebuild my project **TrustAgent** as a single Python project that shows all of the above in one system. I have to explain every part of it to a panel that includes an experienced developer, so **teach as you build**: explain each design decision and trade-off briefly as you make it, and record it in `DECISIONS.md` (see *Working rules*).

## What TrustAgent is

An AI investigation platform for **supplier invoice fraud** (business email compromise, changed bank details, duplicate billing, invoices split to stay under approval thresholds). A finance user uploads an invoice. The system investigates it, gathers evidence, scores the risk, recommends an action, and **a human makes the payment decision**. Every step is recorded in an audit trail.

A working TypeScript/Next.js version already exists and is the **reference implementation for behaviour**. Read it before designing. Match its behaviour, not its code structure.

- Rule checks: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\risk\checks.ts`
- Scoring: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\risk\calculator.ts`
- Minimum-action rule: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\risk\recommendation.ts`
- Dual authorisation: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\approvals.ts`
- Verification propagation: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\data\verification.ts`
- Agent loop, tools, prompts, LLM layer: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\agent\`
- Human actions API: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\app\api\actions\route.ts`
- Seed suppliers, transactions and policies: `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\src\lib\data\`
- Sample invoices (Markdown, JSON, PDF): `C:\2026-Projects\TrustAgent_aws_hackathon\trustagent\sample-invoices\`

## The core design principle (non-negotiable)

**Rules decide the facts; the LLM gives judgement; people make decisions.**

- Anything verifiable is decided in **deterministic Python**, never by the model:
  - Bank account vs the verified record, account holder name, email domain.
  - Amount vs the expected range, history and threshold.
  - Duplicates, urgency and supplier verification status.
- The LLM is used only for:
  1. Reading invoices in any layout (structured extraction).
  2. Qualitative signals rules can't see (pressure tactics, secrecy, "don't call us", claimed executive approval, document inconsistencies).
  3. Comparing an invoice against contract terms retrieved by RAG (see *RAG*).
  4. Writing the explanation.
- The model may contribute **only** these indicator types: `SOCIAL_ENGINEERING`, `DOCUMENT_ANOMALY`, `CONTRACT_DEVIATION`, `OTHER`. Anything else it reports is ignored.
- The **risk score always comes from the calculator**, never the model.
- **Minimum action.** The model's recommended action may be *more* cautious than the risk level requires, never less. Caution order: `APPROVE_PAYMENT < REQUEST_VERIFICATION < ESCALATE < HOLD_PAYMENT`. Minimums:

  | Risk level | Minimum action |
  |---|---|
  | HIGH or CRITICAL | `HOLD_PAYMENT` |
  | MEDIUM | `REQUEST_VERIFICATION` |
  | LOW, verified supplier | `APPROVE_PAYMENT` |
  | LOW, unverified supplier | `REQUEST_VERIFICATION` |
  | No score | `ESCALATE` |

- **The agent has no payment tool at all.** Money only moves through a human action.
- **Graceful degradation:** if the LLM provider fails, the investigation still completes using the rules alone, with a cautious recommendation and a clear note that the AI review was unavailable.

### Business rules to carry over

**Rules and weights.** Each indicator type counts **once** (repeats get weight 0 but are kept as evidence). The total is capped at 100.

| Indicator | Weight | When it fires |
|---|---|---|
| `BANK_DETAILS_CHANGED` | 30 | Verified supplier, and the invoice's account last-4 digits differ from the record (POL-001) |
| `DUPLICATE_INVOICE` | 35 | Same supplier, and either identical line-item descriptions or the same amount on the same date. Recurring monthly invoices name the month, so they don't match |
| `ACCOUNT_HOLDER_MISMATCH` | 25 | Account holder name vs supplier name similarity < 0.5 (normalised tokens, entity words like Pty/Ltd removed) |
| `EMAIL_DOMAIN_MISMATCH` | 25 | Verified supplier, and the invoice's contact domain differs from the record's domain (lookalike domains) |
| `UNUSUAL_AMOUNT` | 20 | Verified supplier with an expected range, and the amount is above its maximum |
| `PERSONAL_EMAIL_DOMAIN` | 15 | Contact email uses gmail, outlook, yahoo, etc. |
| `SUPPLIER_NOT_VERIFIED` | 15 | Unknown or unverified supplier. This is onboarding, **not** a bank change |
| `AMOUNT_EXCEEDS_THRESHOLD` | 15 | Over R100,000 with no supplier range on file (POL-002) |
| `THRESHOLD_AVOIDANCE` | 15 | Within 5% below R100,000 |
| `PATTERN_ANOMALY` | 10 | At least 3 completed payments in history, and amount > 3× their average (POL-003) |
| `URGENCY_INDICATOR` | 10 | Invoice marked IMMEDIATE / urgent (POL-004) |
| `SOCIAL_ENGINEERING` | 20 | From the AI |
| `CONTRACT_DEVIATION` | 20 | From the AI, via RAG against the supplier's contract |
| `DOCUMENT_ANOMALY` | 10 | From the AI |
| `OTHER` | 5 | From the AI |
| `CONFIRMED_MATCH` | 0 | A check that passed, recorded so the evidence explains why an invoice is safe |

**Amount checks.** If the supplier is verified and has an expected range, check only against that range. Otherwise apply the history check and the threshold checks.

**Risk levels:** CRITICAL 80–100, HIGH 60–79, MEDIUM 30–59, LOW 0–29.

**Human workflow:**
- **Evidence before decisions.** Decision options appear only after the investigation completes.
- **Dual authorisation (POL-002).** Payments over R100,000 need a `FINANCE_MANAGER` **and** a `DEPARTMENT_HEAD`, who must be two different people. A `FINANCE_ANALYST` can't approve them. At or below R100,000, one approval from any finance role is enough. Holding a payment needs one person from any role.
- **Supplier verification.** "Request verification" marks the case as pending. When a supplier is verified, and only if the invoice's bank account matches the verified record, every open case for that supplier is marked verified. A mismatch is logged and never clears the case. A verified case can be **re-run**: clear the findings, keep the audit log.
- **Approvals feed history.** An approved payment becomes a transaction in the supplier's history.

**Seed data.** Copy from the reference implementation:
- Three verified suppliers: ABC Office Solutions ****4821 FNB, range R15k–R40k; Metro Cleaning ****7733 Standard Bank, range R10k–R20k; Digital Print Co ****2190 Absa, no range.
- Their transactions.
- Policies POL-001 to POL-004.

### Known bugs from my other projects: do NOT repeat these

The same mistakes kept appearing across my previous projects. Guard against each explicitly:

1. **Extraction.** Never ask the model for derived values like "the last 4 digits". Extract the full account number as printed, and derive the last 4 in code (the model miscounted digits).
2. **Library APIs.** Check current LangGraph/LangChain APIs against the **installed versions** and their docs; don't write them from memory. For example, `create_react_agent(..., state_modifier=...)` was removed and crashed a previous project on start-up.
3. **Initialisation order.** An object attribute used before it was assigned in `__init__` caused a crash. Write a test that constructs every top-level object.
4. **Embedding dimensions** must match the embedding model. A `Vector(1536)` column with a 768-dimension model broke all ingestion before.
5. **RAG context assembly.** Test it: the right chunks must reach the prompt. An indentation bug once sent only the last chunk. Citations must actually be populated; code after `continue` once made them always empty.
6. **Same model everywhere.** Use one settings module for paths and one embedding model for both ingestion and querying. A typo once split the index into two directories.
7. **Hybrid search.** Merge with **Reciprocal Rank Fusion** (k=60). Never add raw keyword and cosine scores that are on different scales.
8. **Identity.** Never trust a user or tenant ID sent by the client for authorisation. Identity comes from one place (here, a simulated "acting as" user; document that production would use SSO).
9. **No blocking in async.** Don't call synchronous LLM or database code inside an `async def` FastAPI endpoint without a thread pool.
10. **No invented services.** Only use services that exist; ask me if unsure. A previous project called a made-up OCR API.
11. **No mock LLM in demos.** Mocks belong in tests only. Notebooks must run against a real model (a free Groq model is fine), or clearly say they're offline.
12. **State guards.** An investigation can only start from `PENDING`; actions only on `ACTION_REQUIRED`; re-runs only on open cases. Starting a run must be atomic (a conditional update) so two clicks can't start two runs.

## Recommended tech stack

Use this stack unless you have a strong reason not to. If you deviate, explain why in `DECISIONS.md`.

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Every dependency ships wheels for it; 3.14 caused install problems in my other repos |
| Environment | **uv** (`pyproject.toml`, `uv.lock`) | Fast, reproducible |
| Agent orchestration | **LangGraph** (current version) + `langchain-core` | Explicit graph, checkpointing, `interrupt()` for human approval, streaming |
| Models | `langchain-openai`, `langchain-anthropic`, `langchain-groq` behind one factory (`init_chat_model` or a small wrapper), chosen by config | The job description names OpenAI and Claude; Groq is free for development. Look up current model names in each provider's docs; don't hard-code from memory |
| Structured output | **Pydantic v2** models with `.with_structured_output()` | Typed, validated LLM output |
| Database | **PostgreSQL 16 + pgvector** via Docker Compose; **SQLAlchemy 2** + **Alembic** migrations | One database for business data, vectors and checkpoints; shows SQL skills |
| Checkpoints | LangGraph **Postgres checkpointer** | Survives restarts; lets a run paused for approval resume days later; fixes "stuck in progress after a crash" |
| Keyword search | Postgres full-text search (`tsvector`) | Persistent, unlike an in-memory index that's empty after a restart |
| Embeddings | One model, set in config (e.g. OpenAI `text-embedding-3-small`, or a local sentence-transformers model); column dimension read from config | Consistency |
| Reranker (optional) | `sentence-transformers` cross-encoder | Better top-K precision; measure whether it helps |
| PDF text | `pypdf` or `pdfplumber`; detect when there's no text layer and report "OCR needed" | Honest handling of scanned PDFs |
| API | **FastAPI**, SSE progress streaming (`StreamingResponse` from LangGraph `stream`) | Contract for an ERP bot, n8n or a UI |
| UI | **Streamlit** | Python-only, quick to build: upload, live progress, evidence, then decision, "Acting as" switcher, supplier verification page |
| Notebooks | **Jupyter**. They import from the package, never copy its logic | Teaching and demo layer (see below) |
| Tests | **pytest** (+ `pytest-asyncio`), with the LLM mocked **only** in unit tests | Deterministic core fully covered |
| Tracing | **LangSmith** (optional, via env vars) | Trace every step when debugging |
| Tool interop (stretch) | **MCP** server (`mcp` / FastMCP) exposing read-only investigation tools | Fits Haibot's "reusable modules" marketplace model |
| Quality | `ruff` for linting and formatting | Clean code for reviewers |

## Architecture: mostly a workflow, with a small bounded agent

This split is the main system-design point I'll make in the interview. The required checks run as a **fixed LangGraph workflow**, because they're predictable, auditable, cheap and testable. **Agent autonomy is used in only one place**, where the path genuinely depends on what's found.

```
upload → extract (LLM, structured) → validate fields (code) → match supplier (code)
      → rule_checks (code) → retrieve_contract_and_policy (RAG)
      → ai_review (LLM: SOCIAL_ENGINEERING / DOCUMENT_ANOMALY / CONTRACT_DEVIATION, with citations)
      → [conditional] deep_dive agent: only if MEDIUM or ambiguous; bounded ReAct with READ-ONLY tools
        (similar past invoices, supplier history, other open cases); recursion_limit ≤ 8
      → score (code) → report (LLM writes text; code enforces the minimum action)
      → human_decision  ← interrupt(): waits for Hold / Approve / Request verification / Escalate
      → execute_action (code: approval rules, state guards, audit entry) → END
```

- **State:** a typed graph state holding the invoice, supplier, rule findings, AI findings (with source tags), evidence, score, recommendation, approvals, verification and the audit log.
- **Nothing with side effects before `interrupt()`.** On resume, the paused node runs again from its start.
- **One `thread_id` per investigation.** The Postgres checkpointer lets a paused case wait for a second approver or for supplier verification.
- **Progress is streamed** to the UI, with **evidence always sent before the score**.
- **Fallback path:** if the LLM fails in `ai_review` or `report`, continue with rule-only scoring and a fallback recommendation from the minimum-action table.
- **Invoice text is untrusted input.** Wrap it in delimiters and treat instructions inside it as data. Injection must not be able to change rule findings, the score or the minimum action, or approve anything.

## RAG: contract-aware invoice checking

This covers the RAG requirement, and it's a real finance use case: *"Is this invoice consistent with what we agreed with this supplier?"*

- **Documents.** Generate realistic synthetic PDFs:
  - One contract or rate card per seeded supplier. Example: Metro Cleaning contract MC-2025-004 with a monthly rate and a deep-clean rate.
  - A 10–20 page procurement policy manual that expands on POL-001 to POL-004.

  Include structure (numbered sections, tables) and some messiness.
- **Ingestion pipeline** as separate stages, each output inspectable: extract text, then structure-aware chunking (split on section headings first; about 500–1,000 characters with 10–20% overlap), then metadata, then embed, then store in pgvector.
  - Metadata: `supplier_id`, `doc_type` (contract / policy), `contract_id`, `section`, `page`, `effective_from/to`.
- **Retrieval.**
  - **Filter on metadata first** (this supplier, active contract), then run hybrid search (pgvector + Postgres full-text), merge with RRF, and optionally rerank.
  - Return chunks with citations (source, page, section).
- **Use.**
  - `ai_review` gets the relevant contract clauses. The LLM extracts the agreed rates and terms **with citations**.
  - **Code** compares invoice line items with the agreed rates (tolerance set in config) and produces `CONTRACT_DEVIATION` evidence with citations.
  - Policy retrieval replaces the old hard-coded policy lookup, so reports cite the policy section.
- **Refusal.** When nothing relevant is retrieved, say "no contract on file", don't guess.

## Evaluation: this is what separates it from a demo

Build a labelled dataset and an evaluation runner (`evals/`), and report the results in the README:

- **Dataset.** Start from my 11 sample invoices; generate about 30–50 more variants covering every rule, clean invoices, near-misses (recurring monthly invoices that are *not* duplicates) and a prompt-injection invoice. Each is labelled with the expected risk level, expected rule findings and expected minimum action.
- **Metrics:**
  - Fraud **recall** and **false-positive rate** (a missed fraud costs far more than a false alarm; say so).
  - Extraction accuracy per field.
  - Retrieval **Recall@K and MRR** for contract clauses (hand-labelled which chunk answers each question).
  - **Faithfulness** of reports via an LLM-as-judge, using a *different* provider from the generator, with a rubric, the reason given before the verdict, and a pass/fail result. Validate the judge against 10–15 cases I label by hand.
  - **Latency and cost per investigation** (tokens × price).
- **Comparison.** Run the evaluation for at least two model configurations (e.g. an OpenAI model vs a Claude model, or a small vs large model) and one retrieval change (e.g. hybrid vs vector-only, or with vs without reranker). Show the table.
- **Regression.** The evaluation must be runnable in one command, so it can serve as a regression test after every prompt or model change.

## Jupyter notebooks: teaching and demo layer

All real logic lives in the package (`src/trustagent/`). Notebooks **import** it and show each stage with real outputs, so I can walk the panel through them in order:

1. `01_extraction.ipynb`: PDF → text → structured extraction → validation. Show the last-4-digit lesson.
2. `02_rules_and_scoring.ipynb`: run the rule checks and calculator on sample invoices; show why a result is LOW or CRITICAL.
3. `03_rag_ingestion_and_retrieval.ipynb`: chunking choices, embeddings, hybrid vs vector-only side by side, RRF, metadata filtering, citations.
4. `04_langgraph_investigation.ipynb`: build the graph, print its Mermaid diagram, run an investigation, pause at `interrupt()`, resume with an approval, inspect the checkpoint history.
5. `05_evaluation.ipynb`: run the evaluation, show the metric tables and one failure analysed with the "retrieval or generation?" debugging tree.
6. `06_security.ipynb`: prompt-injection invoice, least privilege (no payment tool), authorisation checks, dual-approval enforcement.
7. `07_mcp.ipynb` (stretch): expose the read-only tools as an MCP server and call them from a client.

## Repository layout (suggested)

```
trustagent-py/
├── src/trustagent/
│   ├── config.py            # all settings (pydantic-settings): models, paths, thresholds, embedding dims
│   ├── db/                  # SQLAlchemy models, session, Alembic migrations, seed
│   ├── extraction/          # PDF text, LLM structured extraction, validation
│   ├── rules/               # checks.py, scoring.py, recommendation.py  (pure functions)
│   ├── rag/                 # ingestion, chunking, retrieval (hybrid + RRF), citations
│   ├── graph/               # state, nodes, graph builder, deep-dive agent, prompts
│   ├── workflow/            # approvals, verification propagation, state guards, audit log
│   ├── llm/                 # provider factory, retries/backoff, token + cost accounting
│   ├── api/                 # FastAPI app (SSE streaming, actions, suppliers, upload)
│   ├── ui/                  # Streamlit app
│   └── mcp_server.py        # (stretch)
├── notebooks/  evals/  data/{contracts,policies,invoices}/  tests/
├── docker-compose.yml  pyproject.toml  README.md  DECISIONS.md  .env.example
```

## Build order: each phase must be demoable before the next starts

| Phase | Build | Done when |
|---|---|---|
| 0 | Setup: uv, Docker Postgres + pgvector, config, Alembic, seed data, `.env.example` | `pytest` runs; the database seeds |
| 1 | Rules, scoring, minimum action, approvals as pure functions + thorough pytest | All rules tested, including edge cases (recurring invoices aren't duplicates; unverified ≠ bank change) |
| 2 | Extraction (PDF/Markdown/JSON) + validation + supplier matching; notebooks 01–02 | All 11 sample invoices extract correctly |
| 3 | LangGraph workflow with Postgres checkpointer, `interrupt()` approval, fallback path; notebook 04 | End-to-end run pauses for approval and resumes; LLM-outage test passes |
| 4 | RAG: synthetic contracts/policy, ingestion, hybrid + RRF retrieval, contract-deviation check; notebook 03 | Retrieval tests pass; citations populated |
| 5 | FastAPI + SSE + Streamlit UI (evidence before decision, "acting as", verification + re-run) | Full demo in the browser |
| 6 | Evaluation dataset + runner + model/retrieval comparison; notebook 05 | Results table in the README |
| 7 | Security tests (injection, authorisation, state guards); notebook 06; MCP (stretch) | Tests pass |
| 8 | README (problem → architecture diagram → how to run → evaluation results → security → limitations → next steps) | A stranger can run it in 10 minutes |

Commit at the end of every phase with a clear message.

## Working rules

- **Teach as you go.** For every significant decision, give me a 2–4 sentence explanation of *what* and *why*, including the alternative you rejected. Append it to `DECISIONS.md` as a short Architecture Decision Record (context, decision, trade-off). I'll use this to prepare for the interview.
- **Verify, don't assume.** Check library APIs against installed versions and model names against provider docs. If something can't be verified, say so.
- **Tell me before adding dependencies** not listed above.
- **Tests first for the deterministic core** (phase 1). Never mock the LLM outside unit tests.
- **Be honest in the README** about what's simulated (identity, the ERP) and what's missing.
- **Keep it small and correct over big and broken.** If time runs short, finish phases 0–5 properly before starting 6–8.
- **Windows is my development machine**; make sure commands and paths work there (Docker Desktop for Postgres).
- **Secrets only in `.env`.** Never in code, URLs or logs. Mask bank account numbers in logs (POPIA).

Start by reading the reference implementation files listed above. Then give me a short plan: the confirmed stack, the graph diagram, and any questions. Wait for my go-ahead before writing code.
