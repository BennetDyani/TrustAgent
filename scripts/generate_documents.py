"""Generate the synthetic contracts and the procurement policy manual (PDF) used by RAG.

Run with:  uv run python scripts/generate_documents.py

The documents are fictional but realistic: numbered sections, rate-card tables,
running headers/footers, an amended clause, and an EXPIRED contract whose older
rates must not be used (tests the "active contract" metadata filter). Metadata
lives in a manifest next to the files; nothing is guessed from the PDF.
"""

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1] / "data"
ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=14, spaceAfter=6)
H1 = ParagraphStyle("h1", parent=ss["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=ss["Heading3"], fontSize=11, spaceBefore=6, spaceAfter=4)
TITLE = ParagraphStyle("title", parent=ss["Title"], fontSize=18)


def _table(rows):
    t = Table(rows, hAlign="LEFT", colWidths=None)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf2")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))  # fmt: skip
    return t


def build(path: Path, title: str, header: str, blocks: list) -> int:
    """blocks: ("h1", text) | ("h2", text) | ("p", text) | ("table", rows) | ("break",)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = {"n": 0}

    def decorate(canvas, doc):
        pages["n"] = doc.page
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, 287 * mm, header)
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.drawString(18 * mm, 10 * mm, "Confidential - TrustCorp Holdings Ltd")
        canvas.restoreState()

    story = [Paragraph(title, TITLE), Spacer(1, 6)]
    for block in blocks:
        kind = block[0]
        if kind == "h1":
            story.append(Paragraph(block[1], H1))
        elif kind == "h2":
            story.append(Paragraph(block[1], H2))
        elif kind == "p":
            story.append(Paragraph(block[1], BODY))
        elif kind == "table":
            story += [_table(block[1]), Spacer(1, 6)]
        elif kind == "break":
            story.append(PageBreak())
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=title)  # fmt: skip
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return pages["n"]


def standard_clauses(supplier: str, account: str, bank: str) -> list:
    return [
        ("h1", "6 Invoicing and Payment"),
        (
            "p",
            "6.1 The Supplier shall submit a valid tax invoice for each delivery or service period, quoting the "
            "Client's purchase order number and this contract number. Invoices without a purchase order number "
            "may be returned unpaid.",
        ),
        (
            "p",
            "6.2 Payment terms are thirty (30) days from the date of a valid invoice. The Client does not pay "
            "deposits or advance payments unless agreed in writing by both parties.",
        ),
        (
            "p",
            f"6.3 Payment will be made only to the bank account nominated at onboarding: {bank}, account ending "
            f"{account[-4:]}. Any change to the nominated account must be notified in writing on the Supplier's "
            "letterhead, signed by a director, and is subject to the Client's independent verification procedure "
            "(POL-001). The Client will not act on changes notified by email alone.",
        ),
        (
            "p",
            "6.4 Prices on invoices must match the rates in this Agreement. Any price not listed in this Agreement "
            "requires a written quotation accepted by the Client before the goods or services are supplied.",
        ),
        ("h1", "7 Price Adjustments"),
        (
            "p",
            "7.1 Rates are fixed for the first twelve (12) months of the Term. Thereafter rates may be adjusted "
            "once per year by no more than the published CPI rate or six percent (6%), whichever is lower, on "
            "sixty (60) days' written notice. An adjustment takes effect only when confirmed in writing by the "
            "Client.",
        ),
        ("h1", "8 Termination"),
        (
            "p",
            "8.1 Either party may terminate this Agreement on ninety (90) days' written notice. The Client may "
            "terminate immediately for fraud, a material breach that is not remedied within fourteen (14) days, "
            "or repeated invoicing errors.",
        ),
        ("h1", "9 Signatures"),
        (
            "p",
            f"Signed for {supplier} by its authorised director, and for TrustCorp Holdings Ltd by the Head of "
            "Procurement. This Agreement is the entire agreement between the parties on its subject matter.",
        ),
    ]


def contract(supplier, contract_id, start, end, scope, rates, extra=None, account="", bank="", note=None):
    blocks = [
        (
            "p",
            f"<b>Contract number:</b> {contract_id} &nbsp;&nbsp; <b>Supplier:</b> {supplier} &nbsp;&nbsp; "
            "<b>Client:</b> TrustCorp Holdings Ltd",
        ),
    ]
    if note:
        blocks.append(("p", f"<b>{note}</b>"))
    blocks += [
        ("h1", "1 Parties"),
        (
            "p",
            f"This Supply Agreement is entered into between TrustCorp Holdings Ltd (the Client) and {supplier} "
            "(the Supplier).",
        ),
        ("h1", "2 Term"),
        (
            "p",
            f"2.1 This Agreement is effective from {start} and remains in force until {end}, unless terminated "
            "earlier in accordance with clause 8.",
        ),
        ("h1", "3 Scope of Supply"),
        ("p", scope),
        ("h1", "4 Rates"),
        ("p", "4.1 The following rates apply for the Term, excluding VAT at the standard rate of 15%:"),
        ("table", [["Item", "Unit", "Rate (ZAR, excl. VAT)"], *rates]),
    ]
    blocks += extra or []
    blocks += standard_clauses(supplier, account, bank)
    return blocks


CONTRACTS = [
    dict(
        file="ABC-2026-017_ABC_Office_Solutions_Supply_Agreement.pdf",
        document_id="ABC-2026-017",
        supplier_id="SUP-001",
        contract_id="ABC-2026-017",
        effective_from="2026-02-15",
        effective_to="2027-02-14",
        title="Supply Agreement - Office Consumables and Furniture",
        supplier="ABC Office Solutions (Pty) Ltd",
        account="62718304554821",
        bank="First National Bank",
        scope="3.1 The Supplier shall supply office consumables (paper, toner, stationery) on a standing monthly "
        "order and office furniture on request, delivered to the Client's Rosebank premises.",
        rates=[
            ["A4 copy paper - 80gsm (box of 5 reams)", "per box", "R 289.00"],
            ["Toner cartridges - HP 58A", "each", "R 1,150.00"],
            ["Stationery restock pack (pens, notebooks, sticky notes)", "per pack", "R 145.00"],
            ["Ergonomic office chairs - Model EX500", "each", "R 3,950.00"],
            ["Standing desk converters - FlexiDesk Pro", "each", "R 3,200.00"],
            ["Delivery and installation (Gauteng)", "per delivery", "R 2,500.00"],
        ],
        extra=[
            ("h1", "5 Delivery"),
            (
                "p",
                "5.1 Standard delivery within 5 business days. Deliveries of furniture include installation. "
                "No surcharge applies to urgent orders unless quoted in writing beforehand.",
            ),
        ],
    ),
    dict(
        file="MC-2025-004_Metro_Cleaning_Services_Agreement.pdf",
        document_id="MC-2025-004",
        supplier_id="SUP-002",
        contract_id="MC-2025-004",
        effective_from="2025-04-01",
        effective_to="2027-03-31",
        title="Cleaning Services Agreement",
        supplier="Metro Cleaning Services CC",
        account="04120398217733",
        bank="Standard Bank",
        scope="3.1 The Supplier shall clean offices 1-4 at the Client's Rosebank premises Monday to Friday, and "
        "provide periodic deep cleaning and exterior window cleaning as scheduled.",
        rates=[
            ["Monthly cleaning service (Mon-Fri, offices 1-4)", "per month", "R 12,500.00"],
            ["Deep clean - boardroom and executive offices", "per occasion (max. once a month)", "R 3,500.00"],
            ["Window cleaning - exterior, floors 1-2", "per quarter", "R 2,500.00"],
            ["After-hours call-out", "per hour", "R 850.00"],
        ],
        extra=[
            ("h1", "5 Service Levels"),
            (
                "p",
                "5.1 Monthly service is invoiced in arrears on the first business day of the following month, "
                "naming the month of service. Each month's service may be invoiced once only.",
            ),
        ],
    ),
    dict(
        file="MC-2024-001_Metro_Cleaning_Services_Agreement_EXPIRED.pdf",
        document_id="MC-2024-001",
        supplier_id="SUP-002",
        contract_id="MC-2024-001",
        effective_from="2024-04-01",
        effective_to="2025-03-31",
        title="Cleaning Services Agreement (previous term)",
        supplier="Metro Cleaning Services CC",
        account="04120398217733",
        bank="Standard Bank",
        note="SUPERSEDED: this agreement expired on 31 March 2025 and was replaced by MC-2025-004.",
        scope="3.1 The Supplier shall clean offices 1-4 at the Client's Rosebank premises Monday to Friday.",
        rates=[
            ["Monthly cleaning service (Mon-Fri, offices 1-4)", "per month", "R 11,000.00"],
            ["Deep clean - boardroom and executive offices", "per occasion", "R 3,000.00"],
        ],
    ),
    dict(
        file="DPC-2025-009_Digital_Print_Co_Rate_Card_Agreement.pdf",
        document_id="DPC-2025-009",
        supplier_id="SUP-003",
        contract_id="DPC-2025-009",
        effective_from="2025-11-10",
        effective_to="2026-11-09",
        title="Print Services Rate Card Agreement",
        supplier="Digital Print Co (Pty) Ltd",
        account="40715523902190",
        bank="Absa Bank",
        scope="3.1 The Supplier shall provide printing of marketing material, stationery and corporate reports on "
        "request, against a purchase order for each job.",
        rates=[
            ["Conference banners - pull-up, 850x2000mm", "each", "R 1,850.00"],
            ["Branded folders - A4 presentation, full colour", "each (min. 500)", "R 22.00"],
            ["Business cards - 350gsm matt laminate", "per 500", "R 480.00"],
            ["Annual report printing - premium stock, full colour", "per copy (min. 250)", "R 165.00"],
            ["Marketing brochures - A4 tri-fold, gloss", "each (min. 1,000)", "R 9.50"],
            ["Rush turnaround (48 hours)", "surcharge", "10% of the order value"],
        ],
        extra=[
            ("h1", "5 Turnaround"),
            (
                "p",
                "5.1 Standard turnaround is 7 business days from proof approval. A 48-hour rush turnaround is "
                "charged as a percentage surcharge (see clause 4.1), never as a fixed fee.",
            ),
        ],
    ),
]


# --- the policy manual ----------------------------------------------------------------------------

P = "p"
MANUAL = [
    (
        "p",
        "<b>Document:</b> FIN-POL-01 &nbsp;&nbsp; <b>Version:</b> 3.2 &nbsp;&nbsp; <b>Effective:</b> 1 June 2026 "
        "&nbsp;&nbsp; <b>Owner:</b> Chief Financial Officer",
    ),
    ("h1", "1 Purpose and Scope"),
    (
        P,
        "1.1 This manual sets out how TrustCorp Holdings Ltd procures goods and services and pays its suppliers. "
        "Its aim is to make sure that every payment is for goods or services actually received, at the agreed "
        "price, to the right supplier and into the right bank account.",
    ),
    (
        P,
        "1.2 It applies to all employees who request, approve, process or record supplier payments, and to "
        "automated systems that assist them. Automated tools may investigate and recommend, but a payment is "
        "only released by an authorised person following this manual.",
    ),
    (
        P,
        "1.3 Supplier payment fraud, and in particular business email compromise (BEC), is the most significant "
        "fraud risk in accounts payable. Most losses start with a request to change bank details, an urgent "
        "request that bypasses normal checks, or an invoice that looks genuine but is not.",
    ),
    ("h1", "2 Definitions"),
    (
        "table",
        [
            ["Term", "Meaning"],
            [
                "Supplier master data",
                "The verified record of a supplier: legal name, registration, contacts and bank account.",
            ],
            ["Onboarding record", "The contact details and documents captured when the supplier was first verified."],
            [
                "Independent source",
                "A source not controlled by the requester, e.g. the company register or a bank confirmation letter.",
            ],
            ["Finance role", "Finance Analyst, Finance Manager or Department Head."],
            ["Contract rate", "A price agreed in a signed supply agreement or rate card that is in force."],
        ],
    ),
    ("break",),
    ("h1", "3 Roles and Segregation of Duties"),
    (
        P,
        "3.1 No single person may control a payment from end to end. The person who changes or verifies supplier "
        "master data must not approve payments that rely on that change.",
    ),
    (
        P,
        "3.2 Finance Analysts capture and investigate invoices, and may hold, escalate or reject a payment. "
        "Finance Managers and Department Heads approve payments within the limits in section 6.",
    ),
    (
        P,
        "3.3 Where a case is assessed as high or critical risk, approval must come from a Finance Manager or a "
        "Department Head, regardless of the amount.",
    ),
    ("h1", "4 Supplier Onboarding and Master Data"),
    (
        P,
        "4.1 A new supplier must be verified before its first payment. Verification confirms the legal entity "
        "(company register), the directors, the contact details and the bank account, using independent sources "
        "only. Contact details printed on the supplier's own invoice are not an independent source.",
    ),
    (
        P,
        "4.2 The bank account must be confirmed by a bank confirmation letter or by a call to the supplier on a "
        "number obtained independently. The name on the bank account must match the supplier's legal name; a "
        "holding-company or personal account requires written justification and Finance Manager approval.",
    ),
    (
        P,
        "4.3 Until verification is complete the supplier is recorded as unverified, and invoices from it are "
        "treated as onboarding cases, not as routine payments.",
    ),
    ("break",),
    ("h1", "5 Supplier Bank Account Changes (POL-001)"),
    (
        P,
        "5.1 Any change to a supplier's banking details must be independently verified before any payment is "
        "made to the new account. This is the single most important control in this manual.",
    ),
    ("h2", "5.2 Verification procedure"),
    (
        P,
        "5.2.1 On receiving a change request, or an invoice showing an account that differs from the verified "
        "record, the payment is placed on HOLD immediately.",
    ),
    (
        P,
        "5.2.2 A Finance team member confirms the change by telephone AND by email, using the contact details in "
        "the original onboarding record. Never use a telephone number, email address or link supplied in the "
        "change request or printed on the invoice: these are exactly what a fraudster controls.",
    ),
    (
        P,
        "5.2.3 The person verifying records who they spoke to, the number and address used, and the account "
        "number the supplier confirmed. Only then is the supplier master record updated and marked verified.",
    ),
    (
        P,
        "5.2.4 If the supplier confirms its existing account rather than the new one, the change request is "
        "treated as attempted fraud: the payment stays on hold and the matter is escalated.",
    ),
    (
        P,
        "5.3 Common pretexts include an account that is 'frozen', 'under audit' or 'closed', a request to reply "
        "by email only, and discouraging telephone contact. Any of these increases the risk of the request.",
    ),
    ("h1", "6 Payment Authorisation Thresholds (POL-002)"),
    (P, "6.1 Payments are authorised according to the following limits. Amounts include VAT."),
    (
        "table",
        [
            ["Payment amount", "Risk assessment", "Required approval"],
            ["Up to R100,000", "Low or medium", "One approval from any finance role"],
            ["Up to R100,000", "High or critical", "Finance Manager or Department Head"],
            ["Above R100,000", "Any", "Finance Manager AND Department Head (two different people)"],
        ],
    ),
    (
        P,
        "6.2 Splitting a purchase into several invoices to stay below a threshold is prohibited. Invoices that "
        "sit just below R100,000, or a request to 'split across approvals', must be escalated.",
    ),
    (
        P,
        "6.3 (Amended June 2026) The dual authorisation limit was previously R150,000 and was reduced to "
        "R100,000 in version 3.2 of this manual. The two approvers must be different people.",
    ),
    ("break",),
    ("h1", "7 Transaction Pattern Monitoring (POL-003)"),
    (
        P,
        "7.1 An invoice for more than three times the supplier's historical average payment must be flagged "
        "for review before payment. Where a supplier has an agreed expected spending range, invoices above the "
        "range are flagged instead.",
    ),
    (
        P,
        "7.2 A supplier with fewer than three completed payments has no reliable average; its invoices are "
        "checked against the absolute thresholds in section 6.",
    ),
    ("break",),
    ("h1", "8 Urgent Payment Requests (POL-004)"),
    (
        P,
        "8.1 Requests marked immediate or urgent, or demanding payment the same day, must undergo additional "
        "scrutiny. Urgency is the most common social-engineering tactic in payment fraud.",
    ),
    (
        P,
        "8.2 Warning signs include: threats of cancellation or penalties, claims that a senior executive has "
        "already approved the payment, requests for secrecy or to avoid discussing the payment, and instructions "
        "to bypass normal approvals. None of these is a reason to skip a control.",
    ),
    (
        P,
        "8.3 A genuine urgent payment is still verified. Where time is short, escalate to a Finance Manager "
        "rather than shortening the checks.",
    ),
    ("break",),
    ("h1", "9 Duplicate Invoices"),
    (
        P,
        "9.1 Each invoice may be paid once. An invoice is a possible duplicate if it has the same supplier and "
        "either the same billed items, or the same amount on the same date, as an earlier invoice.",
    ),
    (
        P,
        "9.2 Recurring monthly services name the month of service, so invoices for different months are not "
        "duplicates. A 're-issued' invoice for a period already billed must be checked against payment records "
        "before anything is paid.",
    ),
    ("h1", "10 Contract Compliance and Pricing"),
    (
        P,
        "10.1 Where a signed agreement or rate card is in force, invoiced unit prices must match the contract "
        "rate. A tolerance of two percent (2%) is allowed for rounding.",
    ),
    (
        P,
        "10.2 An invoiced price above the contract rate plus tolerance is a contract deviation. It must be "
        "queried with the supplier and approved in writing by the contract owner before payment.",
    ),
    (
        P,
        "10.3 Only the agreement in force on the invoice date applies. Rates in expired or superseded "
        "agreements must not be used, and neither may rates that were proposed but not accepted in writing.",
    ),
    (P, "10.4 Items not covered by an agreement require an accepted written quotation before supply."),
    ("break",),
    ("h1", "11 Purchase Orders"),
    (
        P,
        "11.1 Goods and services must be ordered with an approved purchase order before they are supplied: "
        "'no PO, no pay'. Verbal approvals are not accepted.",
    ),
    (
        P,
        "11.2 Exceptions (for example emergency repairs) must be approved in writing by a Department Head "
        "within two business days and recorded against the invoice.",
    ),
    ("break",),
    ("h1", "12 VAT and Tax Invoices"),
    (
        P,
        "12.1 A valid tax invoice shows the supplier's legal name, address and VAT registration number, the "
        "words 'Tax Invoice', a sequential number, the date, a description of the goods or services, and VAT at "
        "the standard rate of 15% shown separately.",
    ),
    (
        P,
        "12.2 An invoice that charges no VAT for standard-rated goods, or whose totals do not add up, must be "
        "returned for correction before payment.",
    ),
    ("h1", "13 Record Keeping and Audit"),
    (
        P,
        "13.1 Every step of an investigation and every payment decision is recorded in the audit log, with who "
        "acted and when. Audit records are never altered or deleted; corrections are made by adding a new record.",
    ),
    (
        P,
        "13.2 Records are kept for at least five years. Bank account numbers are stored masked except where the "
        "full number is required to make a payment (Protection of Personal Information Act).",
    ),
    ("h1", "14 Exceptions and Escalation"),
    (
        P,
        "14.1 Any departure from this manual requires the written approval of the Chief Financial Officer. "
        "Suspected fraud is reported to the Head of Internal Audit on the same day.",
    ),
    (
        P,
        "14.2 Escalation is not a failure. A Finance Analyst who is unsure should escalate rather than approve; "
        "the escalation is recorded and a Finance Manager responds within one business day.",
    ),
    ("break",),
    ("h1", "15 Payment Runs and Bank File Controls"),
    (
        P,
        "15.1 Supplier payments are released in scheduled payment runs on Tuesdays and Thursdays. Payments "
        "outside a run are exceptional and require Finance Manager approval with a written reason.",
    ),
    (
        P,
        "15.2 The payment file sent to the bank is generated from approved invoices only. It is reviewed by a "
        "second person, who confirms that the total and the number of payments agree with the approved list and "
        "that no bank account in the file differs from the verified supplier master data.",
    ),
    (
        P,
        "15.3 Access to edit supplier bank details in the ERP is restricted to the Supplier Master Data team. "
        "Members of that team may not release payment runs.",
    ),
    (
        P,
        "15.4 After each payment run, a report of any supplier master data changes made in the previous seven "
        "days is reviewed by a Finance Manager, who confirms that each change followed section 5.",
    ),
    ("h1", "16 Credit Notes, Refunds and Overpayments"),
    (
        P,
        "16.1 A credit note must reference the original invoice. Credits are applied against future invoices "
        "from the same supplier, not refunded, unless the supplier relationship has ended.",
    ),
    (
        P,
        "16.2 A request from a supplier to refund an 'overpayment' to a different bank account is a known fraud "
        "pattern and must be verified under section 5 before any refund is made.",
    ),
    ("h1", "17 Foreign Currency Payments"),
    (
        P,
        "17.1 Invoices in a currency other than ZAR are converted at the bank's rate on the approval date for the "
        "purpose of the thresholds in section 6. Foreign bank accounts are verified under section 5, with the "
        "additional step of confirming the SWIFT/BIC code with the supplier's bank in writing.",
    ),
    ("break",),
    ("h1", "18 Fraud Response Plan"),
    (
        P,
        "18.1 If a payment may have been made to a fraudulent account, time matters. Within one hour: notify "
        "the Client's bank and ask it to recall the payment; notify the receiving bank through the bank; inform "
        "the Chief Financial Officer and the Head of Internal Audit.",
    ),
    (
        P,
        "18.2 Within one business day: preserve all emails, invoices and system records; open a case with the "
        "South African Police Service; and review every open payment to the same supplier and to the same bank "
        "account.",
    ),
    (
        P,
        "18.3 After the incident: identify which control failed or was bypassed, update this manual if needed, "
        "and brief all finance staff on the pattern that was used.",
    ),
    ("h1", "19 Automated Investigation Tools"),
    (
        P,
        "19.1 Automated tools that assist with invoice investigation must apply the rules in this manual "
        "deterministically wherever a fact can be checked (bank account, amounts, thresholds, duplicates). "
        "AI-generated observations are advisory: they may raise the level of scrutiny but never lower it.",
    ),
    (
        P,
        "19.2 Every automated finding must be explainable and recorded as evidence, including checks that passed. "
        "Automated tools may not release, schedule or approve payments.",
    ),
    ("h1", "20 Training and Awareness"),
    (
        P,
        "20.1 All finance staff complete payment-fraud awareness training on joining and every twelve months. "
        "Training covers the red flags in Appendix A and the verification procedure in section 5.",
    ),
    (
        P,
        "20.2 Simulated fraud exercises are run twice a year. Results are reported to the Audit Committee "
        "without naming individuals.",
    ),
    ("break",),
    ("h1", "Appendix C - Worked Examples"),
    ("h2", "C.1 Bank account change by email"),
    (
        P,
        "A long-standing supplier emails to say its bank account is 'under audit' and asks for payment to a new "
        "account, adding 'please do not call our office line'. The payment is held (5.2.1). The analyst calls "
        "the number in the onboarding record, not the one in the email, and also emails the onboarding address. "
        "The supplier confirms it sent no such request. The change is rejected and the incident reported (18).",
    ),
    ("h2", "C.2 Genuine bank change"),
    (
        P,
        "A supplier moves to a new bank and sends a letter on its letterhead signed by a director. The payment is "
        "held while the Finance team calls and emails the onboarding contacts, who confirm the new account "
        "number. The master record is updated, the case is re-assessed, and a different person approves the "
        "payment (3.1).",
    ),
    ("h2", "C.3 Just under the threshold"),
    (
        P,
        "An unknown consultancy invoices R98,500 for 'confidential advisory work', says the CEO approved it, and "
        "asks for payment today, 'split across approvals if required'. Every element is a red flag: the supplier "
        "is unverified (4.3), the amount sits just below the threshold (6.2), and urgency, secrecy and claimed "
        "executive approval are social-engineering signs (8.2). The payment is held and escalated.",
    ),
    ("h2", "C.4 Price above contract"),
    (
        P,
        "A contracted printer invoices annual reports at R185 per copy against a contract rate of R165. The "
        "difference exceeds the 2% tolerance (10.1), so the price is queried with the supplier and the contract "
        "owner must approve any higher price in writing before payment (10.2).",
    ),
    ("break",),
    ("h1", "Appendix A - Red Flags Checklist"),
    (
        "table",
        [
            ["Red flag", "Relevant section"],
            ["Bank account differs from the verified record", "5 (POL-001)"],
            ["'Our account is frozen / closed / under audit'", "5.3"],
            ["Do not call us; reply by email only", "5.2.2, 5.3"],
            ["Personal email address (gmail, outlook, yahoo)", "4.1"],
            ["Account holder name differs from supplier name", "4.2"],
            ["Pay today / immediately; threats of cancellation", "8 (POL-004)"],
            ["Claimed executive approval; secrecy", "8.2"],
            ["Just below R100,000; split across approvals", "6.2 (POL-002)"],
            ["More than three times the usual amount", "7 (POL-003)"],
            ["Re-issued invoice for a period already billed", "9.2"],
            ["Price above the contract rate", "10 (Contract compliance)"],
            ["No purchase order; verbal approval", "11"],
        ],
    ),
    ("h1", "Appendix B - Revision History"),
    (
        "table",
        [
            ["Version", "Date", "Change"],
            ["3.0", "March 2025", "Added contract compliance (section 10)."],
            ["3.1", "November 2025", "Telephone AND email verification required for bank changes (5.2.2)."],
            ["3.2", "June 2026", "Dual authorisation limit reduced to R100,000; high-risk approvals (3.3, 6.1)."],
        ],
    ),
]


def main() -> None:
    manifest = []
    for c in CONTRACTS:
        path = ROOT / "contracts" / c["file"]
        pages = build(path, c["title"], f"{c['contract_id']} - {c['supplier']} - Supply Agreement",
                      contract(c["supplier"], c["contract_id"], c["effective_from"], c["effective_to"], c["scope"],
                               c["rates"], c.get("extra"), c["account"], c["bank"], c.get("note")))  # fmt: skip
        manifest.append({
            "file": f"contracts/{c['file']}", "document_id": c["document_id"], "doc_type": "contract",
            "supplier_id": c["supplier_id"], "contract_id": c["contract_id"], "title": c["title"],
            "effective_from": c["effective_from"], "effective_to": c["effective_to"], "pages": pages,
        })  # fmt: skip
        print(f"{path.name}: {pages} page(s)")

    path = ROOT / "policies" / "FIN-POL-01_Procurement_and_Payments_Policy_Manual.pdf"
    pages = build(path, "Procurement and Supplier Payments Policy Manual",
                  "TrustCorp Holdings Ltd - FIN-POL-01 Procurement and Supplier Payments - v3.2", MANUAL)  # fmt: skip
    manifest.append({
        "file": f"policies/{path.name}", "document_id": "FIN-POL-01", "doc_type": "policy", "supplier_id": None,
        "contract_id": None, "title": "Procurement and Supplier Payments Policy Manual",
        "effective_from": "2026-06-01", "effective_to": None, "pages": pages,
    })  # fmt: skip
    print(f"{path.name}: {pages} page(s)")
    (ROOT / "documents_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
