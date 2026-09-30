"""Mutation smoke test: break key business rules one at a time and confirm a test catches each.

Run with:  uv run python scripts/mutation_check.py
Every line should say CAUGHT. A MISSED line means the tests would not notice that rule changing.
"""

import pathlib
import subprocess
import sys

M = [
    ("src/trustagent/rules/checks.py", "if amount > threshold:", "if amount >= threshold:", "threshold strict >"),
    ("src/trustagent/rules/checks.py", "if not sup.verified:", "if False:", "unverified != bank change"),
    ("src/trustagent/rules/checks.py", 'signature != "" and ', "", "empty line items never match"),
    (
        "src/trustagent/rules/checks.py",
        "if amount > average * policy.pattern_multiplier:",
        "if amount >= average * policy.pattern_multiplier:",
        "pattern strict >",
    ),
    (
        "src/trustagent/rules/checks.py",
        "if ctx.invoice.urgency != Urgency.IMMEDIATE:",
        "if ctx.invoice.urgency == Urgency.NORMAL:",
        "HIGH not urgent",
    ),
    (
        "src/trustagent/rules/scoring.py",
        "elif indicator.type in counted:",
        "elif False:",
        "count once",
    ),
    ("src/trustagent/rules/scoring.py", "if score >= 60:", "if score > 60:", "HIGH boundary"),
    (
        "src/trustagent/rules/recommendation.py",
        "return Action.APPROVE_PAYMENT if supplier_verified else Action.REQUEST_VERIFICATION",
        "return Action.APPROVE_PAYMENT",
        "LOW unverified",
    ),
    (
        "src/trustagent/rules/recommendation.py",
        ">= Caution[floor.value]",
        "> Caution[floor.value]",
        "equal caution accepted",
    ),
    (
        "src/trustagent/workflow/approvals.py",
        "if any(_same_person(a.name, approver.name) for a in existing):",
        "if False:",
        "same person twice",
    ),
    (
        "src/trustagent/workflow/approvals.py",
        "return amount > _threshold()",
        "return amount >= _threshold()",
        "dual strict >",
    ),
    (
        "src/trustagent/workflow/guards.py",
        'if verification_status == "PENDING":',
        "if False:",
        "approve blocked when pending",
    ),
    (
        "src/trustagent/workflow/guards.py",
        'if bank_details_changed and verification_status != "VERIFIED":',
        "if False:",
        "bank change blocks approval until verified",
    ),
    (
        "src/trustagent/rules/recommendation.py",
        "if HOLD_REQUIRED_FINDINGS & set(findings):",
        "if False:",
        "bank change forces HOLD",
    ),
    (
        "src/trustagent/workflow/verification.py",
        "if evidence.phone_source not in TRUSTED_SOURCES or evidence.email_source not in TRUSTED_SOURCES:",
        "if evidence.phone_source not in TRUSTED_SOURCES:",
        "email must come from a trusted source",
    ),
    (
        "src/trustagent/rules/scoring.py",
        "weight = min(weight, max(ai_cap - ai_total, 0))",
        "weight = weight",
        "AI points capped",
    ),
    (
        "src/trustagent/rules/scoring.py",
        "if is_ai and indicator.overlaps in fired_rules:",
        "if False:",
        "AI overlap with a fired rule scores 0",
    ),
    (
        "src/trustagent/graph/nodes.py",
        "if not quote_supported(obs.quote, inp.invoice_text):",
        "if False:",
        "AI quote must be in the invoice",
    ),
    (
        "src/trustagent/rules/checks.py",
        "if len(accounts) < 2:",
        "if True:",
        "document with two accounts is flagged",
    ),
    (
        "src/trustagent/rules/checks.py",
        "and same_bank(k.bank_name, inv.bank_name)",
        "and True",
        "shared account needs the same bank",
    ),
    (
        "src/trustagent/extraction/pipeline.py",
        "if not number_in_document(extracted.bank_account_number, source.text):",
        "if False:",
        "extracted account must be in the document",
    ),
    (
        "src/trustagent/workflow/approvals.py",
        "if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and approver.role not in HIGH_RISK_APPROVER_ROLES:",
        "if False:",
        "analyst cannot approve HIGH/CRITICAL",
    ),
    (
        "src/trustagent/workflow/actions.py",
        "if sod := separation_of_duties_error(case.verification, actor):",
        "if sod := None:",
        "verifier cannot approve",
    ),
    (
        "src/trustagent/workflow/guards.py",
        "and verified_during == run_number:",
        "and False:",
        "re-run required after verification",
    ),
    (
        "src/trustagent/rules/contract.py",
        "if billed > agreed * (1 + tolerance):",
        "if billed > agreed * (1 + tolerance) * 2:",
        "contract tolerance is 2%, not more",
    ),
    (
        "src/trustagent/rules/contract.py",
        'if not quote_supported(quote, chunk["content"]):',
        "if False:",
        "contract quote must be in the clause",
    ),
    (
        "src/trustagent/rules/contract.py",
        'if _field(term, "price_basis") != "per_unit":',
        "if False:",
        "only per-unit prices are compared",
    ),
    (
        "src/trustagent/rag/retrieve.py",
        "or_(DocumentChunkRow.effective_to.is_(None), DocumentChunkRow.effective_to >= as_of),",
        "",
        "expired contracts are filtered out",
    ),
]
missed = 0
for path, old, new, name in M:
    p = pathlib.Path(path)
    src = p.read_text(encoding="utf-8")
    assert old in src, name
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    try:
        r = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "-x",
                "tests/rules",
                "tests/workflow",
                "tests/graph",
                "tests/extraction",
                "tests/rag",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        p.write_text(src, encoding="utf-8")
    missed += r.returncode == 0
    print(("CAUGHT  " if r.returncode else "MISSED  ") + name)
sys.exit(1 if missed else 0)
