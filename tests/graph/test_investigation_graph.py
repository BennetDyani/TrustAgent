"""End-to-end workflow tests: start -> evidence -> score -> report -> pause -> human decisions -> close."""

import threading

import pytest
from sqlalchemy import func, select

from trustagent.db.models import AuditLogRow, EvidenceRow, InvestigationRow, InvoiceRow, TransactionRow
from trustagent.domain import HumanAction
from trustagent.graph.llm_steps import AIObservation
from trustagent.workflow.guards import GuardError
from trustagent.workflow.identity import DEMO_USERS
from trustagent.workflow.verification import VerificationEvidence, apply_supplier_verification

THANDI, SIPHO, LERATO = DEMO_USERS["thandi"], DEMO_USERS["sipho"], DEMO_USERS["lerato"]


def run(service, case_id):
    return list(service.start(case_id, THANDI))


def names(events):
    return [e["event"] for e in events]


def case_row(sf, case_id) -> InvestigationRow:
    with sf() as s:
        return s.get(InvestigationRow, case_id)


def audit_actions(sf, case_id) -> list[str]:
    with sf() as s:
        return list(s.scalars(select(AuditLogRow.action).where(AuditLogRow.investigation_id == case_id)
                              .order_by(AuditLogRow.id)))  # fmt: skip


# --- the happy path ------------------------------------------------------------------------------


def test_clean_invoice_runs_to_a_decision_point_with_evidence_before_score(service, upload, clean_db):
    case_id = upload()
    events = run(service, case_id)
    order = names(events)

    assert "evidence" in order and "risk" in order
    assert max(i for i, e in enumerate(order) if e == "evidence") < order.index("risk"), "evidence must precede score"
    assert order.index("risk") < order.index("recommendation") < order.index("awaiting_decision")

    risk = next(e for e in events if e["event"] == "risk")
    assert (risk["score"], risk["level"]) == (0, "LOW")
    case = case_row(clean_db, case_id)
    assert case.status == "ACTION_REQUIRED" and case.recommended_action == "APPROVE_PAYMENT"
    assert case.run_number == 1 and case.ai_review_available is True
    assert case.llm_usage and all("input_tokens" in u for u in case.llm_usage)


def test_small_payment_approved_by_one_person_closes_and_feeds_history(service, upload, clean_db):
    case_id = upload()
    run(service, case_id)
    with clean_db() as s:
        before = s.scalar(select(func.count()).select_from(TransactionRow))

    result = service.act(case_id, HumanAction.APPROVE_PAYMENT, THANDI)

    assert result.ok and result.closed
    case = case_row(clean_db, case_id)
    assert case.status == "CLOSED" and case.decision == "APPROVE_PAYMENT"
    with clean_db() as s:
        assert s.get(InvoiceRow, case.invoice_id).status == "APPROVED"
        assert s.scalar(select(func.count()).select_from(TransactionRow)) == before + 1
    assert not service.graph.get_state(service._config(case_id, 1)).next  # the run has ended


# --- dual authorisation through the interrupt loop --------------------------------------------------


def test_large_payment_needs_manager_and_head_across_two_resumes(service, upload, clean_db):
    # Digital Print Co: verified, no expected range -> R150k is over the threshold (LOW, 15 points).
    case_id = upload(supplier_name="Digital Print Co", bank_account_number="****2190", supplier_email=None,
                     bank_account_holder=None, total_due="R 150,000.00", subtotal=None, vat_amount=None)  # fmt: skip
    run(service, case_id)

    refused = service.act(case_id, HumanAction.APPROVE_PAYMENT, THANDI)
    assert not refused.ok and refused.status_code == 403 and "Finance Analyst cannot approve" in refused.message

    first = service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO)
    assert first.ok and not first.closed and "Department Head" in first.message
    assert case_row(clean_db, case_id).status == "ACTION_REQUIRED"

    second = service.act(case_id, HumanAction.APPROVE_PAYMENT, LERATO)
    assert second.ok and second.closed
    assert [a["name"] for a in case_row(clean_db, case_id).approvals] == ["Sipho Dlamini", "Lerato Khumalo"]
    assert "Approval 1 of 2 recorded" in audit_actions(clean_db, case_id)


# --- bank change policy (ADR-029 / ADR-030) -------------------------------------------------------------


def _bank_change(upload):
    # Metro Cleaning, verified with ****7733; invoice pays ****9917.
    return upload(bank_account_number="1092847591 9917", bank_name="Capitec Bank")


def test_bank_change_holds_even_if_the_model_proposes_approval(service, upload, clean_db, fake_llm):
    fake_llm.proposed_action = "APPROVE_PAYMENT"
    case_id = _bank_change(upload)
    events = run(service, case_id)

    rec = next(e for e in events if e["event"] == "recommendation")
    assert rec["recommended_action"] == "HOLD_PAYMENT"
    assert "below the minimum" in rec["recommendation"]
    assert fake_llm.report_inputs[0].minimum_action == "HOLD_PAYMENT"
    assert any("phone AND email" in step for step in fake_llm.report_inputs[0].next_steps)

    refused = service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO)
    assert not refused.ok and refused.status_code == 409 and "phone and email" in refused.message


def _evidence(account: str) -> VerificationEvidence:
    return VerificationEvidence(
        phone_confirmed=True, phone_contact="+27 11 803 4455", phone_source="onboarding_record",
        email_confirmed=True, email_contact="billing@metrocleaning.co.za", email_source="onboarding_record",
        confirmed_bank_account=account,
    )  # fmt: skip


def test_hold_then_verify_by_phone_and_email_then_approve(service, upload, clean_db):
    case_id = _bank_change(upload)
    run(service, case_id)

    held = service.act(case_id, HumanAction.HOLD_PAYMENT, THANDI)
    assert held.ok and not held.closed  # HOLD keeps the case open (ADR-030)
    case = case_row(clean_db, case_id)
    assert case.status == "ACTION_REQUIRED" and case.verification["status"] == "PENDING"

    # The supplier confirms its OLD account: the case must NOT clear.
    with clean_db() as s, s.begin():
        outcome = apply_supplier_verification(s, "SUP-002", _evidence("****7733"), SIPHO, today=None)
    assert outcome.cases_mismatched == [case_id] and outcome.cases_verified == []
    assert not service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO).ok

    # The supplier confirms the NEW account by phone and email: the case clears, and it can be paid.
    with clean_db() as s, s.begin():
        outcome = apply_supplier_verification(s, "SUP-002", _evidence("10928475919917"), SIPHO, today=None)
    assert outcome.cases_verified == [case_id]
    assert case_row(clean_db, case_id).verification["status"] == "VERIFIED"

    paid = service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO)
    assert paid.ok and paid.closed


def test_reject_closes_the_case_and_nothing_is_paid(service, upload, clean_db):
    case_id = _bank_change(upload)
    run(service, case_id)
    result = service.act(case_id, HumanAction.REJECT_INVOICE, THANDI, note="Supplier confirmed they never sent it.")
    assert result.ok and result.closed
    case = case_row(clean_db, case_id)
    assert case.status == "CLOSED" and case.decision == "REJECT_INVOICE"
    with pytest.raises(GuardError):
        service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO)


def test_escalate_keeps_the_case_open_and_waiting(service, upload, clean_db):
    case_id = upload()
    run(service, case_id)
    assert service.act(case_id, HumanAction.ESCALATE, THANDI).ok
    assert service.graph.get_state(service._config(case_id, 1)).next == ("human_decision",)
    assert service.act(case_id, HumanAction.APPROVE_PAYMENT, SIPHO).closed


# --- graceful degradation -----------------------------------------------------------------------


def test_llm_outage_still_completes_with_rules_and_a_cautious_fallback(service, upload, clean_db, fake_llm):
    fake_llm.review_down = fake_llm.report_down = True
    case_id = _bank_change(upload)
    events = run(service, case_id)

    assert "error" not in names(events) and "awaiting_decision" in names(events)
    rec = next(e for e in events if e["event"] == "recommendation")
    assert rec["recommended_action"] == "HOLD_PAYMENT"
    assert "AI review was unavailable" in rec["recommendation"]
    case = case_row(clean_db, case_id)
    assert case.ai_review_available is False and case.risk_score is not None
    assert "AI review unavailable" in names(events) or any(e.get("action") == "AI review unavailable" for e in events)


# --- the AI layer: whitelisted observations only --------------------------------------------------------


def test_ai_observations_are_scored_with_their_quote_and_source(service, upload, clean_db, fake_llm):
    fake_llm.observations = [AIObservation(type="SOCIAL_ENGINEERING", severity="HIGH",
                                           description="Pressure to bypass verification.",
                                           quote="Please do not call our office")]  # fmt: skip
    case_id = upload()
    events = run(service, case_id)
    ev = [e for e in events if e["event"] == "evidence" and e["type"] == "SOCIAL_ENGINEERING"]
    assert ev and ev[0]["source"] == "AI" and ev[0]["weight"] == 20
    assert 'Invoice says: "Please do not call our office"' in ev[0]["description"]
    # The reviewer saw the rule findings as facts, and only the redacted document.
    assert fake_llm.review_inputs[0].rule_findings


# --- the bounded deep dive -------------------------------------------------------------------------------


def test_deep_dive_runs_for_medium_risk_and_its_findings_count(service, upload, clean_db, fake_llm):
    fake_llm.dive_observations = [{"type": "OTHER", "description": "Same account used by another supplier.",
                                   "severity": "HIGH"}]  # fmt: skip
    # Unknown supplier, R96k: SUPPLIER_NOT_VERIFIED 15 + THRESHOLD_AVOIDANCE 15 = 30 -> MEDIUM
    case_id = upload(supplier_name="Brand New Traders", supplier_email="accounts@brandnew.co.za",
                     bank_account_holder="Brand New Traders", total_due="R 96,000.00", subtotal=None,
                     vat_amount=None)  # fmt: skip
    events = run(service, case_id)

    assert fake_llm.dive_inputs and "MEDIUM" in fake_llm.dive_inputs[0].reason
    ev = [e for e in events if e["event"] == "evidence" and e["description"].startswith("Deep dive:")]
    assert ev and ev[0]["weight"] == 5
    assert case_row(clean_db, case_id).deep_dive_summary == "Fake deep dive."


def test_clean_low_invoice_skips_the_deep_dive(service, upload, fake_llm):
    run(service, upload())
    assert fake_llm.dive_inputs == []


# --- state guards and concurrency ---------------------------------------------------------------------


def test_a_case_can_only_be_started_once(service, upload):
    case_id = upload()
    run(service, case_id)
    with pytest.raises(GuardError, match="only start from PENDING"):
        run(service, case_id)


def test_two_simultaneous_starts_produce_exactly_one_run(service, upload, clean_db):
    case_id = upload()
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        try:
            list(service.start(case_id, THANDI))
            outcomes.append("started")
        except GuardError:
            outcomes.append("refused")

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["refused", "started"]
    assert case_row(clean_db, case_id).run_number == 1


def test_actions_are_refused_before_the_investigation_has_run(service, upload):
    case_id = upload()
    with pytest.raises(GuardError, match="ACTION_REQUIRED"):
        service.act(case_id, HumanAction.APPROVE_PAYMENT, THANDI)


# --- re-run, crash and recovery ---------------------------------------------------------------------------


def test_rerun_starts_a_fresh_thread_and_keeps_the_audit_log(service, upload, clean_db):
    case_id = _bank_change(upload)
    run(service, case_id)
    audit_before = len(audit_actions(clean_db, case_id))

    with clean_db() as s, s.begin():
        apply_supplier_verification(s, "SUP-002", _evidence("10928475919917"), SIPHO, today=None)
    events = list(service.rerun(case_id, SIPHO))

    case = case_row(clean_db, case_id)
    assert case.run_number == 2 and case.status == "ACTION_REQUIRED"
    # The supplier record now holds the new account, so run 2 no longer finds a bank change.
    types = {e["type"] for e in events if e["event"] == "evidence"}
    assert "BANK_DETAILS_CHANGED" not in types
    assert len(audit_actions(clean_db, case_id)) > audit_before
    assert "Investigation re-run" in audit_actions(clean_db, case_id)
    assert list(service.checkpointer.list(service._config(case_id, 1))) == []  # old run can't be resumed
    with clean_db() as s:
        runs = set(s.scalars(select(EvidenceRow.run_number).where(EvidenceRow.investigation_id == case_id)))
    assert runs == {1, 2}  # earlier evidence kept for audit


def test_unexpected_crash_marks_failed_and_can_be_rerun(service, upload, clean_db, fake_llm):
    def boom(_):
        raise RuntimeError("disk on fire")

    service.deps.reviewer = boom
    service.graph = __import__("trustagent.graph.builder", fromlist=["build_graph"]).build_graph(
        service.deps, service.checkpointer
    )
    case_id = upload()
    events = run(service, case_id)
    assert names(events)[-1] == "error" and "disk on fire" in events[-1]["message"]
    assert case_row(clean_db, case_id).status == "FAILED"

    service.deps.reviewer = fake_llm.reviewer
    service.graph = __import__("trustagent.graph.builder", fromlist=["build_graph"]).build_graph(
        service.deps, service.checkpointer
    )
    assert "awaiting_decision" in names(list(service.rerun(case_id, THANDI)))


def test_a_run_interrupted_mid_way_resumes_from_its_checkpoint(service, upload, clean_db):
    case_id = upload()
    stream = service.start(case_id, THANDI)
    for event in stream:  # consume a few events, then "crash" (stop pulling)
        if event.get("action") == "Rule checks completed":
            break
    stream.close()
    assert case_row(clean_db, case_id).status == "IN_PROGRESS"

    events = list(service.recover(case_id))
    assert "awaiting_decision" in names(events)
    assert case_row(clean_db, case_id).status == "ACTION_REQUIRED"
    assert "Run recovered from checkpoint" in audit_actions(clean_db, case_id)


def test_checkpoint_history_and_diagram(service, upload):
    case_id = upload()
    run(service, case_id)
    history = service.checkpoint_history(case_id)
    assert len(history) > 5
    mermaid = service.graph.get_graph().draw_mermaid()
    for node in ("rule_checks", "ai_review", "deep_dive", "human_decision", "execute_action"):
        assert node in mermaid


def test_clean_invoice_report_gets_no_verification_steps(service, upload, fake_llm):
    run(service, upload())
    assert fake_llm.report_inputs[0].next_steps == [
        "No further checks are required; the payment can go through normal approval."
    ]
