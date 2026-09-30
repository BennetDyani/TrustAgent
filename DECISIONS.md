# Architecture Decision Records

Short records of each significant decision: the context, what was decided, and the trade-off
(including the alternative rejected). Newest decisions are appended at the end of each phase.

---

## Phase 0: Planning and setup

### ADR-001: Mostly a fixed workflow, with one small bounded agent

**Context.** An invoice investigation has checks that must *always* run (bank details, duplicates,
amounts) and a smaller part where what to look at next depends on what was found.
**Decision.** The required checks are a fixed LangGraph workflow. Agent autonomy is used only in
`deep_dive`, a ReAct loop with read-only tools and `recursion_limit ≤ 8`, entered only for MEDIUM or
ambiguous cases.
**Trade-off.** A fully agentic loop (what the TypeScript version did) is more flexible, but it
occasionally skipped steps and needed a "you forgot to call the report tool" nudge plus a fallback.
A fixed graph is predictable, auditable, cheaper and testable node by node. Autonomy is paid for
only where it earns its keep.

### ADR-002: Rules decide the facts, the LLM gives judgement, people make decisions

**Context.** An LLM misreading one digit of a bank account must never be able to change an outcome.
**Decision.** Every verifiable fact is a pure Python function. The model may only contribute
`SOCIAL_ENGINEERING`, `DOCUMENT_ANOMALY` and `OTHER`; any other type it reports is dropped and
logged. The score always comes from the calculator. Code enforces the minimum action: the model can
be more cautious than the risk level, never less. There is no payment tool.
**Trade-off.** We lose any "intuition" the model might have about amounts or accounts. That is
deliberate: those questions have exact answers, and exact answers belong in code.

### ADR-003: `CONTRACT_DEVIATION` is raised by code, not by the model

**Context.** The brief lists `CONTRACT_DEVIATION` among the model's types, but "does R14,000 exceed
the agreed R12,500 ± 2%?" is arithmetic.
**Decision.** The LLM *extracts* agreed rates from retrieved contract clauses, with citations. Code
compares invoice lines against those rates (tolerance in config) and raises `CONTRACT_DEVIATION`
with source `CONTRACT`.
**Trade-off.** Stricter than the brief, and it depends on extraction quality. But a misread rate
shows up as a cited, checkable number, not an unverifiable model opinion.

### ADR-004: One settings module

**Context.** A typo in a previous project split a vector index into two directories, and ingestion
and querying used different embedding models.
**Decision.** `trustagent/config.py` (pydantic-settings) holds every path, model name, threshold
and dimension. Nothing else reads environment variables.
**Trade-off.** One slightly large module rather than settings spread next to their users. Worth
it: there is exactly one place to look, and one place to be wrong.

### ADR-005: Gemini by default, Groq as the second provider and the judge

**Context.** The job description names OpenAI and Claude; the available keys are Gemini and Groq.
**Decision.** One chat-model factory supports `gemini | groq | openai | anthropic`, chosen by
config. Gemini (`gemini-3.5-flash-lite`) is the default generator. Groq is the second model for the
evaluation comparison and the LLM-as-judge, because the judge must use a different provider from
the generator. Model IDs were checked against provider docs on 2026-09-30.
**Trade-off.** The OpenAI and Anthropic paths are wired up but not run end-to-end without keys. The
README says so rather than claiming otherwise.

### ADR-006: Postgres is the system of record; the checkpoint only holds a paused run

**Context.** A case must be updatable from outside the graph (supplier verification happens on a
different page, days later), and a re-run must "clear findings, keep the audit log".
**Decision.** Case status, evidence, approvals, verification and the audit log live in ordinary
tables. The LangGraph Postgres checkpointer stores only the execution state of a run. Each run gets
its own thread, `"{investigation_id}:run-{n}"`, so a re-run starts clean while the audit log (keyed
by investigation) carries across runs.
**Trade-off.** Two places hold related state, so nodes must write business results to the tables
explicitly. The alternative, graph state as the only record, would make the verification page
edit checkpoints directly, and make reporting SQL query serialised blobs.

### ADR-007: The human decision is a loop around `interrupt()`

**Context.** Several human actions leave a case open: the first of two approvals (POL-002),
ESCALATE, and REQUEST_VERIFICATION.
**Decision.** `human_decision` calls `interrupt()`, then `execute_action` applies the action. If the
case is still `ACTION_REQUIRED`, the graph routes back to `human_decision` and pauses again. HOLD
or a completed approval ends the run. Nothing with side effects happens before `interrupt()`,
because on resume the paused node runs again from its start.
**Trade-off.** One long-lived thread per case, rather than a separate action API that bypasses the
graph. That keeps every decision in the same audited, checkpointed flow.

### ADR-008: Embeddings are `gemini-embedding-001` at 768 dimensions

**Context.** There is no OpenAI key. A local sentence-transformers model would work offline but
pulls in PyTorch (~1–2 GB).
**Decision.** Gemini `gemini-embedding-001` with `output_dimensionality=768`, with task types
`RETRIEVAL_DOCUMENT` for ingestion and `RETRIEVAL_QUERY` for queries. Per Google's docs, dimensions
below 3072 are not normalised, so we L2-normalise in code (tested). The column dimension comes from
config, and start-up fails fast if the live column disagrees (`check_embedding_dimension`).
**Trade-off.** Ingestion needs the network and counts against API quota. We chose `-001` over the
newer `gemini-embedding-2` because it supports explicit retrieval task types; `-2` uses prompt
prefixes instead. 768 dimensions keep the HNSW index small with a modest quality cost versus 3072.

### ADR-009: Money is `Decimal` and `NUMERIC(14,2)`, never float

**Context.** The rules compare amounts to the cent (duplicates, thresholds, contract rates).
**Decision.** Python `Decimal` in the domain models, and `NUMERIC(14,2)` in Postgres.
**Trade-off.** Slightly more verbose arithmetic, but `0.1 + 0.2 != 0.3` can't cause a missed
duplicate or a threshold off by a cent.

### ADR-010: The audit log is append-only, enforced by the database

**Context.** A governance-first product has to show the audit trail can't be rewritten.
**Decision.** A Postgres trigger rejects every `UPDATE` and `DELETE` on `audit_log`. There is a
test for it.
**Trade-off.** Genuine corrections have to be new entries, never edits. That's the point.

### ADR-011: Only masked bank account numbers are stored (POPIA)

**Decision.** Extraction reads the full account number (see ADR-013). Code derives the last 4
digits and stores only `****1234`; the full number is never persisted or logged. This also matches
the reference data, where supplier records hold only the last 4.
**Trade-off.** We can't match on the full number. Last-4 plus bank name plus account-holder name is
what the reference used, and it's enough for these checks.

### ADR-012: Local environment

- **Python 3.12** is installed and pinned by uv (`uv python install 3.12`), leaving the system
  3.14 alone, since some dependencies still lack 3.14 wheels.
- **Postgres** runs on host port **5433**: 5432 is taken by a local Postgres install on this machine.
- **A separate `trustagent_test` database** is used by pytest. It's rebuilt with
  `alembic downgrade base → upgrade head` each session, and each test's changes are rolled back.
- **LangGraph checkpoint tables** are created by `PostgresSaver.setup()`, not by Alembic. They
  belong to the library, which migrates them itself.

### ADR-013: Extract the full account number; derive the last 4 in code

**Context.** A previous version asked the model for "the last 4 digits" and it miscounted.
**Decision.** The extraction schema asks for the full number *as printed*. Code strips non-digits
and slices. The same applies to every derived value (totals, dates, domains).

### ADR-014: Reference behaviours kept on purpose

- `URGENCY_INDICATOR` fires only for IMMEDIATE, and "URGENT" is read as IMMEDIATE. HIGH priority
  does not fire it.
- An unknown supplier is registered as **UNVERIFIED** at upload, so it scores
  `SUPPLIER_NOT_VERIFIED` (onboarding), not `BANK_DETAILS_CHANGED`.
- Approving is blocked while supplier verification is pending.
- ESCALATE leaves the case open; HOLD closes it.

### ADR-015: What counts as "ambiguous" for the deep dive

**Decision.** `deep_dive` runs when the provisional score is MEDIUM, or when:
- the score is within 5 points of a level boundary (25–34, 55–64, 75–84), or
- the AI flagged something while every rule passed, or
- the supplier is unverified with no payment history.

The condition is a pure function, so routing is testable and explainable.
**Trade-off.** It's a heuristic. The evaluation reports how often the deep dive changes the final
level, which tells us whether it earns its cost.

---

## Phase 1: Rules, scoring, minimum action, approvals

### ADR-016: The rules are pure functions and take their thresholds as a parameter

**Decision.** Every check has the signature `check(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]`.
- `CheckContext` holds the invoice, the supplier, its history and the other invoices, all loaded
  beforehand. There's no database access in the rules.
- `RulePolicy` is a frozen dataclass built from settings by default. Tests pass their own.

**Trade-off.** The caller has to gather the context first, which is one extra step in the graph
node. In exchange, 130+ tests run in under two seconds with no database or model, and
`calculate_risk(run_rule_checks(ctx))` returns the same result every time for the same input,
which is what an auditor needs.

### ADR-017: Passed checks are evidence too (`CONFIRMED_MATCH`, weight 0)

**Decision.** When a check passes, it emits `CONFIRMED_MATCH` with a description, e.g. "Bank
account ****7733 matches the verified account on file".
**Why.** A LOW score is only convincing if the report can say what was checked and found clean.
Silence doesn't show the check ran.

### ADR-018: Approvals return a result; guards raise an exception

**Decision.**
- `record_approval` returns `ApprovalRecorded | ApprovalError`. A refused approval is a normal
  business outcome with a message for the user (HTTP 403).
- The `workflow/guards.py` functions raise `GuardError`. Acting on a case in the wrong state is a
  conflict the caller shouldn't have attempted (HTTP 409).

**Trade-off.** Two error styles in one package, but each matches how the caller has to react.

### ADR-019: The model's action is parsed strictly

**Decision.** `resolve_recommended_action` accepts only exact enum values. `"approve_payment"`,
`None`, `42` or a dict all fall back to the minimum action and are marked `raised=True`, so the
report can say the model's proposal was replaced.
**Why.** Accepting fuzzy input from the model is exactly how prompt injection gets a foothold.

### ADR-020: A mutation smoke test for the business rules

**Context.** All 133 tests passed on the first implementation. Passing tests prove nothing if they
wouldn't notice a rule changing.
**Decision.** `scripts/mutation_check.py` breaks 12 key rules one at a time and checks that a test
fails each time:
- `>` changed to `>=` at the thresholds,
- count-once scoring removed,
- the unverified-supplier branch removed,
- same-person approval allowed,
- and others.

All 12 were caught.
**Trade-off.** It's a hand-picked list, not a full mutation-testing tool like `mutmut`. It costs
seconds to run and needs no new dependency.

### ADR-021: Known limitation kept from the reference: account-holder similarity at exactly 0.5 passes

**Context.** "Prestige Events Holdings" vs "Prestige Catering & Events" shares 2 of 4 tokens, a
similarity of 0.5. The rule fires only below 0.5, so the INV-1053 holder name is *not* flagged.
**Decision.** We match the reference, with a test that documents it
(`test_account_holder_at_exact_threshold_passes`). That invoice is still caught by other signals:
- unverified supplier,
- amount over the threshold,
- and the AI review, which sees "payment to our holding company account".

The evaluation will show whether the threshold should move. We change it with data, not by feel.

### ADR-022: Re-runs are also allowed on FAILED runs

**Decision.** The reference only re-runs open cases. We also allow a re-run when a run crashed
(`FAILED`), so a crash is never a dead end. `CLOSED` stays final.

---

## Phase 2: Extraction, validation, supplier matching

### ADR-023: Extraction happens at upload, not inside the investigation graph

**Context.** The plan put `extract` as the first graph node.
**Decision.** Intake (`workflow/intake.py`) does extract → validate → match supplier → store invoice → open a
`PENDING` case. The graph starts from the stored invoice at `rule_checks`.
**Why.** The reviewer sees the extracted fields and warnings *before* investigating, as the reference did. A
failed extraction is a clean upload error (422 or 503), never a half-finished case. And "LLM down during an
investigation" has a clear meaning: the facts are already stored, so the rules can still run.
**Trade-off.** Extraction isn't part of the streamed investigation progress or the graph's checkpoint history.
It is in the audit log instead.

### ADR-024: No regex fallback for free-form documents; JSON never uses the LLM

**Decision.**
- JSON invoices are mapped by code into the same `ExtractedInvoice` shape (zero model calls).
- PDF and Markdown need the model. If it's unavailable, the upload is refused with a clear 503 that suggests
  JSON.

**Rejected.** The reference fell back to a Markdown regex parser. Its fields were guesses tuned to one template,
and they would have fed the rules as if they were facts. A wrong "fact" is worse than no case.

### ADR-025: The model copies values as printed; code parses them

**Decision.**
- The extraction schema asks for strings exactly as printed: `'R 185,000.00'`, `'19 August 2026'`, the full
  account number, and the priority label.
- Code parses amounts (including `-R`, decimal commas and space separators), dates (day-first, for South
  Africa) and urgency.
- Every field is required-but-nullable, which Groq's strict JSON-schema mode needs.

**Evidence.** INV-2005's account `51007733829904` *contains* `7733` (Metro Cleaning's verified ending). The model
copies the whole number, and code takes `9904`.
**Finding.** `gemini-3.5-flash-lite` uses fixed sampling and **ignores `temperature`** (the SDK warns). We don't
pass it for Gemini, and the evaluation measures run-to-run variance instead of assuming determinism.

### ADR-026: Duplicate detection only compares against *earlier* uploads (deviation from the reference)

**Context.** The reference compared against all other invoices. Notebook 02 showed the result: once INV-2004 (a
re-issued August invoice) existed, the *original* INV-1049 was flagged as the duplicate. The outcome depended on
when you happened to investigate, and a re-run could flip a genuine, paid case.
**Decision.** A new `invoices.upload_seq` identity column records upload order. `load_check_context` passes only
earlier uploads to the (unchanged, pure) duplicate check. The first submission is the original, and later
copies are duplicates. Timestamps couldn't be used, because `now()` is identical within one transaction.
**Trade-off.** If the fraudulent copy happens to arrive first, the genuine one is flagged. That's still the safe
direction: the pair gets looked at.

### ADR-027: Stored document text is redacted (POPIA)

**Decision.** The model must see the document to read it, but `invoices.raw_text` stores a copy with the full
account number replaced by `****1234`. That copy is what later prompts (AI review) receive. Markdown bold and
spacing inside the number are handled, and there's a test for it.

### ADR-028: Live tests are marked and deselected by default

**Decision.** `uv run pytest` runs everything offline (LLM faked only in unit tests). `uv run pytest -m live`
runs the 11 sample invoices against the real configured model and compares them with hand-labelled ground truth
(`data/invoices/labels.json`). The same comparator (`extraction/labels.py`) is used by notebook 01 and the
evaluation, so all three score extraction the same way.
**Result (2026-09-30).**
- Gemini `gemini-3.5-flash-lite`: 192/192 fields, including the 5 JSON twins; about 2 s per document.
- Groq `openai/gpt-oss-120b`: 132/132 fields on the 11 documents, at 2–25 s each (free-tier rate limits).

### ADR-029: A bank-details change always means HOLD until finance verifies by phone AND email

**Context.** On rules alone, INV-1050 (a verified supplier's new bank account) scored 45, MEDIUM, with a minimum
action of `REQUEST_VERIFICATION`, although POL-001 says "HOLD payment".
**Decision (Bennet, 2026-09-30).** When a supplier's account changes, payment is held, and the finance team must
verify the new account **by email and by phone** before the supplier is marked verified.
- `minimum_action` holds whatever the score when `BANK_DETAILS_CHANGED` is found (`HOLD_REQUIRED_FINDINGS`).
  The model can't lower it, and the rule-only fallback says why.
- The guard refuses `APPROVE_PAYMENT` on such a case until its verification is `VERIFIED`.
- `validate_verification` accepts a verification only with **both** channels confirmed, the phone number and
  email address recorded, **both taken from the original onboarding records**, and the confirmed account
  number. A contact printed on the invoice is exactly what a fraudster controls, so the source is an explicit
  field (`onboarding_record | invoice | other`), not free text.
- After a supplier is verified, a case clears only if its invoice account matches the verified record. If the
  supplier confirmed its *old* account, the case stays held.
- *Amendment (phase 3).* A **brand-new** supplier has no onboarding records, so for them contacts may come from
  another `independent_source` (e.g. the company register), but **never** from the invoice.

**Trade-off.** More work for finance on every bank change, including genuine ones. That's the point: bank-detail
changes are the most common BEC loss, and a phone call costs less than a redirected payment. Covered by tests
and three new mutation-check entries.

### ADR-030: HOLD keeps the case open; a separate Reject action closes confirmed fraud

**Context.** In the reference, HOLD closed the case. Under ADR-029 a held payment is *waiting for verification*.
It has to stay open so it can be re-run and paid once the account is verified.
**Decision (implemented in phase 3).** `HOLD_PAYMENT` puts the invoice `ON_HOLD`, opens a verification request,
and leaves the case `ACTION_REQUIRED`. A new human-only action, **Reject invoice**, closes a case confirmed as
fraud or otherwise not payable. It isn't in the model's action list, so the model can't recommend closing a case.
**Trade-off.** One more button than the brief listed, but "held" and "rejected" are different business outcomes,
and conflating them was a latent bug.

---

## Phase 3: The LangGraph investigation

### ADR-031: The deep dive is a hand-built, bounded ReAct loop with read-only, scoped tools

**Context.** `langgraph.prebuilt.create_react_agent` is **deprecated in LangGraph 1.x** (checked in the installed
source). Its replacement, `create_agent`, lives in the separate `langchain` package.
**Decision.** A small explicit subgraph: `agent` (model with tools bound) → `ToolNode` → `agent` … ending when the
model calls `submit_findings`, a terminal "tool" whose arguments are the structured result.
- **Least privilege.** Three tools: supplier payment history, similar invoices (same supplier, same amount
  elsewhere, *same bank account used by another supplier*), and other open cases. They take **no arguments**:
  they're bound to this investigation, so the model can't point them at another supplier.
- **Read only.** Each tool runs in a `SET TRANSACTION READ ONLY` transaction, with a test proving writes fail.
  There is no payment tool anywhere.
- **Bounded.** `recursion_limit = 8` (config). Hitting it isn't an error: the investigation continues without
  deep-dive findings and says so.
- **Whitelisted.** Deep-dive observations go through the same filter as the AI review, tagged "Deep dive:".

**Trade-off.** About 60 lines we own rather than one library call, with no new dependency, no deprecated API, and
a loop you can draw on a whiteboard.

### ADR-032: Crash safety needs `durability="sync"`, and a correct "is it finished?" check

**Found by the recovery test.** A run abandoned mid-way couldn't be resumed. Two causes:
1. LangGraph's default `durability="async"` saves each checkpoint *in the background* while the next step
   starts, so a crash can lose it. We stream with **`durability="sync"`**: each step is saved before the next
   begins, for a few milliseconds per step.
2. After a crash, `StateSnapshot.next` came back **empty although the run was unfinished**. The step that had just
   completed stored its output as *pending writes*, and `next` only lists tasks that haven't written yet.
   `run_finished()` checks `not state.next and not state.tasks`.

`service.recover(id)` resumes an `IN_PROGRESS` run from its last checkpoint. If there's none, it marks the case
`FAILED` so it can be re-run.

### ADR-033: One service layer owns the state guards

- **Atomic start:** `UPDATE … SET status='IN_PROGRESS' WHERE status='PENDING' RETURNING run_number`. A test fires
  two starts at the same moment from two threads: exactly one runs.
- **Serialised decisions:** every resume takes a per-case Postgres advisory lock (`pg_advisory_xact_lock`), and
  `apply_human_action` locks the case row (`FOR UPDATE`). Two approvers clicking at once can't both resume the
  same checkpoint or both record "the second approval".
- **Unexpected exceptions** mark the case `FAILED` with an audit entry and an `error` event. Model failures
  never get this far, because they degrade inside the nodes.
- **Re-run** starts a new thread (`{id}:run-{n+1}`), clears findings and approvals, keeps the audit log and
  earlier evidence rows, and **deletes the old thread's checkpoints** so a stale run can never be resumed.

### ADR-034: Identity comes from one place

`workflow/identity.py` maps an "acting as" key to a person and role on the server. Nothing trusts a name or role
sent by a client. The resume payload is built by the service from that server-side identity. In production this
module would read verified SSO/OIDC claims instead of a demo table.

### ADR-035: The report writer never sees the invoice; the reviewer sees only the redacted copy

The AI review is the only step that reads document text: the redacted copy, inside delimiters, with instructions
to report embedded instructions as `SOCIAL_ENGINEERING`. The report writer gets structured evidence, the score and
the minimum action, never the document. Injection has one small surface, and even there it can only *add*
whitelisted observations. The schema restricts observation types, and code filters them again (defence in depth).

### ADR-036: Graph state holds only JSON-safe values

Dicts, lists and strings; no Pydantic objects. Checkpoints don't depend on how classes serialise, so a case paused
for days still resumes after a deploy that renamed a class.

### ADR-037: Client-side rate limiting, per provider

**Found in the live run.** The Gemini free tier allows **15 requests per minute** per model. An investigation makes
3–7 calls (review, deep-dive turns, report), so the fourth invoice hit a 429. Graceful degradation handled it
correctly: rule-only score, HOLD, and the "AI review was unavailable" note. But pacing beats failing, so
`langchain-core`'s `InMemoryRateLimiter` is attached to every model, one shared instance per provider, set by
`LLM_REQUESTS_PER_MINUTE` (Gemini 14, Groq 25).
**Limit.** Per process. Several API workers would need a shared limiter (Redis), or a paid tier.

### ADR-038: Token usage is recorded per call

`invoke_structured` uses `with_structured_output(include_raw=True)`, so each call returns the parsed object *and*
the raw message's `usage_metadata`. Tokens per step are stored on the case (`investigations.llm_usage`) for cost
per investigation in the evaluation.

### ADR-039: Retrieval is a pluggable dependency; phase 3 uses the policy table

`retrieve_context` calls `Deps.context_retriever`. In phase 3 it returns POL-001..004 with citations. Phase 4 swaps
in hybrid RAG over contracts and the policy manual without touching the graph.

### ADR-040: Code decides the reviewer's next steps; the model only phrases them

**Found in the live run.** The report writer copied the prompt's bank-change instruction ("confirm the *new account*
by phone and email") into INV-1051/1052/1053, which have **no bank change**. That's an unfaithful report, and
exactly what the evaluation's judge should flag.
**Decision.** `required_next_steps(findings, supplier_verified, amount)` is a pure, tested function:
- bank change → phone AND email verification from onboarding records;
- new supplier → onboarding checks from an independent source;
- mismatched holder or email → confirm with known contacts;
- duplicate → check the earlier invoice wasn't paid;
- unusual amount → confirm with the department;
- over R100k → POL-002 dual approval.

The report prompt receives them as **REQUIRED NEXT STEPS** and may only phrase them. The bank-change sentence was
removed from the prompt. The AI-review prompt was also tightened, because it sometimes re-described rule facts
(e.g. "re-issued invoice, matches the duplicate finding") as `DOCUMENT_ANOMALY`, double-counting 10 points.
**Principle.** The same as the score: anything decidable, including *process*, is decided in code.

### ADR-041: Every AI observation must quote the invoice, and code checks the quote

The AI review must copy the exact words its observation is based on. `quote_supported()` checks those words are
really in the (redacted) invoice. It's lenient about case, whitespace, markdown bold, typographic quotes and a
"..." where words were skipped, but strict about the words. An observation whose quote isn't found is discarded
and logged. This stops invented concerns, and an injected instruction can't make one up either. Deep-dive
observations are exempt, because they're based on tool results rather than the document.

### ADR-042: An AI observation that restates a rule finding scores 0

**Context.** The AI sometimes re-described a rule fact in its own words (INV-2004: "re-issued invoice", when
`DUPLICATE_INVOICE` had fired), adding 5-10 points for the same concern.
**Decision.** Each observation declares `relates_to_rule`: the rule type it overlaps, or null. If that rule
**actually fired**, the observation is kept as evidence at weight 0. Code checks the claim: an overlap with a rule
that didn't fire is ignored, and the observation counts normally. A zero-weight overlap doesn't use up its type,
so a later, genuinely new observation of the same type still counts.
**Live effect.** INV-2004 dropped from 40 to 35 (the restated duplicate now adds 0).
**Watch.** The model sometimes also tags pressure *wording* as overlapping `URGENCY_INDICATOR` (the priority
*label*), e.g. INV-1048 and 1052, which is arguably over-cautious in the conservative direction. Phase 6 measures
how often.

### ADR-043: AI findings can add at most 20 points in total (Bennet, 2026-09-30)

AI review and deep dive together are capped at `AI_SCORE_CAP = 20`, about one risk level. The AI can lift a case
(MEDIUM→HIGH, HIGH→CRITICAL) but can never decide an outcome alone. Rule weights are never capped. Trimmed AI
indicators stay as evidence, with their weight shown.
**Trade-off, seen live.** INV-1053 (Prestige Catering: new supplier, "verbal approval", holding-company account,
R215k) now scores **50, MEDIUM** (rules 30 + AI capped at 20), down from 60, HIGH. Its minimum action is
`REQUEST_VERIFICATION`, and POL-002 dual approval still applies. It would score HIGH if the account-holder rule
fired, but "Prestige Events Holdings" sits exactly at the 0.5 similarity threshold (ADR-021). Phase 6 should
decide whether that threshold moves, using the labelled data.

### ADR-044: Run-to-run variation is measured before it's treated (Bennet, 2026-09-30)

Gemini 3.x ignores temperature (ADR-025), so the same invoice can get different AI observations. Phase 6 runs
each case several times and reports how often the risk level and the recommended action change. A
self-consistency fix (run the review twice, keep only what agrees) was rejected for now: it doubles AI calls on a
15-requests-per-minute free tier. It will be revisited with data.

---

## Lessons caught during the build

- **Phase 0: a field named `date` shadowed the `date` type.** In both the Pydantic and SQLAlchemy
  models, `date: date | None = None` rebinds `date` inside the class body. The next line,
  `due_date: date | None`, then evaluated `None | None`, which crashed on import. On the ORM side
  it silently created `due_date` as `NOT NULL`. The construct-everything test caught the crash, and
  an autogenerate drift check caught the schema. Fix: `import datetime as dt` and annotate
  `dt.date`.
- **Phase 2: insert order without ORM relationships.** The invoice and its investigation were added in one
  flush. SQLAlchemy only orders inserts by foreign key when a `relationship()` is declared, so the case row was
  inserted first and violated its foreign key. Caught by the intake tests. Fix: an explicit `flush()` after the
  invoice.
- **Phase 2: a notebook found a behaviour bug the unit tests couldn't.** Running all 11 invoices through one
  database showed the original INV-1049 flagged as a duplicate of its later re-issue (ADR-026). Each unit test
  had only one invoice pair in a fixed order.
- **Phase 3: `durability="async"` and pending writes.** See ADR-032. The recovery test failed until both were
  understood. "Test the crash path" paid for itself.
- **Phase 3: a fake model that reused one message object.** LangGraph's `add_messages` reducer de-duplicates by
  message id, so a scripted fake that returned the *same* `AIMessage` each turn silently replaced history instead
  of appending, and the loop ended early. The fix was in the test fake (a fresh copy per turn). Worth knowing when
  replaying recorded messages.
- **Phase 3: `interrupt()` re-runs its node.** A probe showed the paused node running 4 times for 2 pauses. That's
  why `human_decision` has no side effects and all effects live in `execute_action`.
- **Phase 3: test isolation.** Graph tests need committed data. They now truncate before *and* after, because
  rows left behind broke the older rollback-based tests that expect an empty database.
- **Honest limit: `TRUNCATE` bypasses the append-only trigger.** The trigger is row-level (UPDATE/DELETE). The
  tests rely on `TRUNCATE` to reset. In production the application's database role would be granted only
  `INSERT, SELECT` on `audit_log`, with no `UPDATE`, `DELETE` or `TRUNCATE`.
