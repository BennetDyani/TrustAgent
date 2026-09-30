"""TrustAgent UI (Streamlit). A client of the API only.

Run with:  uv run streamlit run src/trustagent/ui/app.py   (API running on API_BASE_URL)

Pages: Cases (list, detail, live investigation, decisions), New invoice (upload), Suppliers (verification).
Evidence always comes first: decision buttons appear only once the investigation has completed, and each
shows whether the current user may take it, and why not.
"""

import pandas as pd
import streamlit as st

from trustagent.ui import client as api_client

st.set_page_config(page_title="TrustAgent", page_icon="🛡️", layout="wide")

ACTION_LABELS = {
    "HOLD_PAYMENT": "Hold payment",
    "REQUEST_VERIFICATION": "Request verification",
    "ESCALATE": "Escalate",
    "APPROVE_PAYMENT": "Approve payment",
    "REJECT_INVOICE": "Reject invoice",
}
LEVEL_ICON = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴", None: "⚪"}


def money(value) -> str:
    return f"R{float(value):,.2f}" if value is not None else "-"


def case_label(cases: list[dict], case_id: str) -> str:
    c = next(c for c in cases if c["id"] == case_id)
    return f"{c['invoice_number']} · {c['supplier_name']} ({case_id})"


def supplier_label(suppliers: list[dict], supplier_id: str) -> str:
    return f"{next(s['name'] for s in suppliers if s['id'] == supplier_id)} ({supplier_id})"


def api() -> api_client.ApiClient:
    return api_client.ApiClient(st.session_state.get("user_key", "thandi"))


def show_error(exc: api_client.ApiError) -> None:
    st.error(f"{exc.detail} ({exc.status})")


# --- sidebar: identity and navigation -------------------------------------------------------------

with st.sidebar:
    st.title("🛡️ TrustAgent")
    st.caption("Supplier invoice fraud investigation")
    try:
        users = api_client.ApiClient("thandi").users()
    except Exception as exc:  # API not running
        st.error(f"API not reachable: {exc}")
        st.stop()
    labels = {u["key"]: f"{u['name']} ({u['role'].replace('_', ' ').title()})" for u in users}
    st.selectbox("Acting as", list(labels), format_func=labels.get, key="user_key")
    st.caption("Demo identity. Production: single sign-on.")
    page = st.radio("Page", ["Cases", "New invoice", "Suppliers"], key="page")
    try:
        h = api().health()
        st.caption(f"Model: {h['llm']} · Documents: {h['document_chunks']} chunks")
    except api_client.ApiError:
        pass


# --- live investigation stream -----------------------------------------------------------------------


def run_stream(case_id: str, operation: str = "run") -> None:
    evidence: list[dict] = []
    with st.status("Investigating…", expanded=True) as status:
        table = st.empty()
        try:
            for e in api().stream(case_id, operation):
                kind = e["event"]
                if kind == "activity":
                    icon = {"FAILED": "⚠️", "IN_PROGRESS": "⏳"}.get(e.get("status"), "✅")
                    st.write(f"{icon} **{e['action']}** · {e.get('detail', '')}")
                elif kind == "evidence":
                    evidence.append(e)
                    table.dataframe(
                        pd.DataFrame(evidence)[["type", "source", "weight", "description"]],
                        hide_index=True,
                        width="stretch",
                    )
                elif kind == "risk":
                    st.metric("Risk score", f"{e['score']}/100", e["level"])
                elif kind == "recommendation":
                    st.info(f"**Recommended: {ACTION_LABELS[e['recommended_action']]}**\n\n{e['recommendation']}")
                elif kind == "awaiting_decision":
                    status.update(label="Investigation complete: waiting for a decision", state="complete")
                elif kind == "error":
                    status.update(label="Investigation failed", state="error")
                    st.error(e["message"])
        except api_client.ApiError as exc:
            status.update(label="Could not start", state="error")
            show_error(exc)


# --- case detail -------------------------------------------------------------------------------------------


def render_case(case_id: str) -> None:
    try:
        c = api().case(case_id)
    except api_client.ApiError as exc:
        show_error(exc)
        return
    inv = c["invoice"]
    st.subheader(
        f"{LEVEL_ICON[c['risk_level']]} {inv['invoice_number']} · {inv['supplier_name']} · {money(inv['amount'])}"
    )
    cols = st.columns(5)
    cols[0].metric("Status", c["status"].replace("_", " ").title())
    cols[1].metric("Risk", f"{c['risk_score']}/100" if c["risk_score"] is not None else "-", c["risk_level"])
    cols[2].metric("Invoice", inv["invoice_status"] if "invoice_status" in inv else c["invoice_status"])
    cols[3].metric("Verification", (c["verification"] or "not requested").title())
    cols[4].metric("Run", c["run_number"])

    if c["status"] == "PENDING":
        st.info("This case has not been investigated yet. Evidence comes first; decisions follow.")
        if st.button("▶ Run investigation", type="primary"):
            run_stream(case_id, "run")
            st.button("Show case")
        return
    if c["status"] == "IN_PROGRESS":
        st.warning("An investigation is in progress, or was interrupted.")
        if st.button("Resume from last checkpoint"):
            run_stream(case_id, "recover")
        return

    # Recommendation first, then the evidence behind it.
    if c["recommended_action"]:
        st.info(f"**Recommended: {ACTION_LABELS[c['recommended_action']]}**\n\n{c['recommendation']}")
    if c["ai_review_available"] is False:
        st.warning("The AI review was unavailable for this run: the assessment is based on rule checks only.")
    if c["summary"]:
        st.write(c["summary"])

    st.markdown("#### Evidence")
    if c["evidence"]:
        rows = [
            {
                "finding": e["type"],
                "source": e["source"],
                "points": e["weight"],
                "severity": e["severity"],
                "evidence": e["description"],
                "cited": "; ".join(f"{x['source']} p.{x.get('page')} {x.get('section') or ''}" for x in e["citations"]),
            }
            for e in c["evidence"]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if c["deep_dive_summary"]:
        with st.expander("Deep dive (bounded agent)"):
            st.write(c["deep_dive_summary"])

    with st.expander("Invoice as extracted"):
        st.json(
            {
                k: inv[k]
                for k in (
                    "invoice_number",
                    "date",
                    "due_date",
                    "bank_account",
                    "bank_name",
                    "bank_account_holder",
                    "supplier_email",
                    "urgency",
                    "document_accounts",
                )
            }
        )
        if inv["line_items"]:
            st.dataframe(pd.DataFrame(inv["line_items"]), hide_index=True)
        for w in inv["extraction_warnings"] or []:
            st.caption(f"⚠️ {w}")

    # Decisions: only after the investigation has completed.
    if c["actions"]:
        st.markdown("#### Decision")
        if c["approvals"]:
            st.caption("Approvals so far: " + ", ".join(f"{a['name']} ({a['role']})" for a in c["approvals"]))
        note = st.text_input("Note for the audit log (optional)", key=f"note-{case_id}")
        cols = st.columns(len(c["actions"]))
        for col, a in zip(cols, c["actions"], strict=True):
            label = ACTION_LABELS[a["action"]]
            if col.button(
                label,
                key=f"{case_id}-{a['action']}",
                disabled=not a["allowed"],
                help=a["reason"],
                type="primary" if a["action"] == c["recommended_action"] else "secondary",
            ):
                try:
                    result = api().act(case_id, a["action"], note or None)
                    (st.success if result["ok"] else st.error)(result["message"])
                except api_client.ApiError as exc:
                    show_error(exc)
        blocked = [a for a in c["actions"] if not a["allowed"]]
        for a in blocked:
            st.caption(f"🔒 {ACTION_LABELS[a['action']]}: {a['reason']}")

    rerun_help = "New run with fresh evidence; the audit log is kept."
    if c["status"] in ("ACTION_REQUIRED", "FAILED") and st.button("↻ Re-run investigation", help=rerun_help):
        run_stream(case_id, "rerun")

    with st.expander(f"Audit trail ({len(c['audit'])} entries, append-only)"):
        st.dataframe(
            pd.DataFrame(c["audit"])[["timestamp", "actor", "action", "detail"]],
            hide_index=True,
            width="stretch",
        )
    st.caption(f"Model tokens this run: {c['tokens']:,}")


# --- pages ----------------------------------------------------------------------------------------------------

if page == "Cases":
    st.header("Cases")
    data = api().cases()
    m = data["metrics"]
    cols = st.columns(4)
    metric_names = [
        ("Cases", "total"),
        ("High risk", "high_risk"),
        ("Action required", "action_required"),
        ("On hold", "on_hold"),
    ]
    for col, (label, key) in zip(cols, metric_names, strict=True):
        col.metric(label, m[key])
    if not data["cases"]:
        st.info("No cases yet. Upload an invoice on the New invoice page.")
    else:
        table = pd.DataFrame(
            [
                {
                    "case": c["id"],
                    "invoice": c["invoice_number"],
                    "supplier": c["supplier_name"],
                    "amount": money(c["amount"]),
                    "risk": f"{LEVEL_ICON[c['risk_level']]} {c['risk_score'] if c['risk_score'] is not None else ''}",
                    "status": c["status"],
                    "recommended": c["recommended_action"] or "",
                }
                for c in data["cases"]
            ]
        )
        st.dataframe(table, hide_index=True, width="stretch")
        ids = [c["id"] for c in data["cases"]]
        default = ids.index(st.session_state["case_id"]) if st.session_state.get("case_id") in ids else 0
        st.session_state["case_id"] = st.selectbox(
            "Open case",
            ids,
            index=default,
            format_func=lambda i: case_label(data["cases"], i),
        )
        st.divider()
        render_case(st.session_state["case_id"])

elif page == "New invoice":
    st.header("New invoice")
    st.caption("PDF, Markdown, text or JSON. PDFs and Markdown are read by the AI; JSON is read by code.")
    uploaded = st.file_uploader("Invoice", type=["pdf", "md", "txt", "json"])
    if uploaded and st.button("Upload and extract", type="primary"):
        try:
            with st.spinner("Reading the invoice…"):
                r = api().upload(uploaded.name, uploaded.getvalue())
            st.session_state["case_id"] = r["investigation_id"]
            inv = r["invoice"]
            st.success(
                f"Case {r['investigation_id']} opened ({r['supplier_match'].replace('_', ' ').lower()}: "
                f"{r['supplier_id']}, similarity {r['match_confidence']})."
            )
            st.json(
                {
                    k: inv[k]
                    for k in (
                        "invoice_number",
                        "supplier_name",
                        "amount",
                        "date",
                        "bank_account",
                        "bank_name",
                        "bank_account_holder",
                        "supplier_email",
                        "urgency",
                    )
                }
            )
            for w in r["warnings"]:
                st.warning(w)
            st.info("Check the fields above, then open the case on the Cases page to run the investigation.")
        except api_client.ApiError as exc:
            show_error(exc)

elif page == "Suppliers":
    st.header("Suppliers")
    suppliers = api().suppliers()
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "verified": "✅" if s["verified"] else "❌",
                    "bank account": s["bank_account"],
                    "bank": s["bank_name"],
                    "verified by": s["verified_by"],
                    "open cases": s["open_cases"],
                }
                for s in suppliers
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    st.markdown("#### Verify a supplier's bank account (POL-001)")
    st.caption(
        "Confirm by phone AND email, using contact details from the onboarding records (or, for a new "
        "supplier, an independent source such as the company register), never those on the invoice. "
        "Whoever verifies can't approve that supplier's payment."
    )
    choice = st.selectbox("Supplier", [s["id"] for s in suppliers], format_func=lambda i: supplier_label(suppliers, i))
    sources = ["onboarding_record", "independent_source", "invoice", "other"]
    with st.form("verify"):
        c1, c2 = st.columns(2)
        phone_ok = c1.checkbox("Confirmed by phone")
        phone = c1.text_input("Phone number called")
        phone_src = c1.selectbox("Where the number came from", sources)
        email_ok = c2.checkbox("Confirmed by email")
        email = c2.text_input("Email address used")
        email_src = c2.selectbox("Where the address came from", sources)
        account = st.text_input("Bank account number the supplier confirmed")
        notes = st.text_area("Notes (who you spoke to)")
        if st.form_submit_button("Mark verified", type="primary"):
            try:
                r = api().verify(
                    choice,
                    {
                        "phone_confirmed": phone_ok,
                        "phone_contact": phone,
                        "phone_source": phone_src,
                        "email_confirmed": email_ok,
                        "email_contact": email,
                        "email_source": email_src,
                        "confirmed_bank_account": account,
                        "notes": notes,
                    },
                )
                st.success(f"{choice} verified. Cases cleared: {r['cases_verified'] or 'none'}.")
                if r["cases_mismatched"]:
                    st.warning(
                        f"Still on hold (invoice account differs from the verified one): {r['cases_mismatched']}"
                    )
                if r["cases_verified"]:
                    st.info("Re-run those cases before approving, and a different person must approve.")
            except api_client.ApiError as exc:
                show_error(exc)
