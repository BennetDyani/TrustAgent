"""Contract-rate check: code decides CONTRACT_DEVIATION (ADR-003, ADR-049).

The model's only job is to *map* invoice lines to contract rates and quote
the clause (a semantic step). Everything checkable is checked here:

- the cited chunk was actually retrieved for this case,
- the quote really appears in that chunk,
- the agreed price really appears in the quote,
- only per-unit prices are compared ("10% of the order value" is never read as R10),
- billed > agreed x (1 + tolerance) is a deviation (policy manual 10.1: 2%).

A term that fails any check is dropped with a note, never guessed.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trustagent.domain import Citation, IndicatorSource, Invoice, LineItem, RiskIndicator, Severity
from trustagent.extraction.accounts import amount_in_document
from trustagent.extraction.normalize import parse_amount
from trustagent.rules.recommendation import quote_supported
from trustagent.text import normalise_text


@dataclass
class ContractCheckResult:
    indicators: list[RiskIndicator]
    notes: list[str]


def _field(term: Any, name: str) -> Any:
    return term.get(name) if isinstance(term, dict) else getattr(term, name, None)


def _find_line(invoice: Invoice, description: str) -> LineItem | None:
    wanted = normalise_text(description or "")
    if not wanted:
        return None
    for li in invoice.line_items:
        have = normalise_text(li.description)
        if have == wanted or wanted in have or have in wanted:
            return li
    return None


def _unit_price(li: LineItem) -> Decimal:
    if li.unit_price:
        return li.unit_price
    return li.total / li.quantity if li.quantity else li.total


def check_contract_terms(
    invoice: Invoice, terms: list[Any], chunks: dict[int, dict[str, Any]], tolerance: Decimal
) -> ContractCheckResult:
    """``chunks`` maps retrieved chunk id -> {"content", "contract_id", "citation": {...}}."""
    indicators: list[RiskIndicator] = []
    notes: list[str] = []
    for term in terms:
        line_desc = _field(term, "invoice_line") or ""
        chunk = chunks.get(_field(term, "source_chunk_id"))
        quote = _field(term, "quote")
        label = f"'{line_desc[:60]}'"
        if chunk is None:
            notes.append(f"{label}: the cited contract clause was not among the retrieved clauses; ignored.")
            continue
        if not quote_supported(quote, chunk["content"]):
            notes.append(f"{label}: the quoted contract text was not found in the clause; ignored.")
            continue
        if _field(term, "price_basis") != "per_unit":
            notes.append(
                f"{label}: contract price is not a per-unit rate ({_field(term, 'agreed_price')}); not compared."
            )
            continue
        agreed = parse_amount(_field(term, "agreed_price"))
        if agreed is None or agreed <= 0 or not amount_in_document(agreed, quote):
            notes.append(f"{label}: the agreed price is not in the quoted clause; ignored.")
            continue
        line = _find_line(invoice, line_desc)
        if line is None:
            notes.append(f"{label}: no matching line on the invoice; ignored.")
            continue

        billed = _unit_price(line)
        citation = Citation(**chunk["citation"])
        ref = f"{chunk.get('contract_id') or citation.source}, {citation.section or 'rates'}"
        if billed > agreed * (1 + tolerance):
            pct = (billed - agreed) / agreed * 100
            indicators.append(RiskIndicator(
                type="CONTRACT_DEVIATION",
                severity=Severity.HIGH if pct > 10 else Severity.MEDIUM,
                source=IndicatorSource.CONTRACT,
                citations=[citation],
                description=(
                    f"'{line.description}' billed at R{billed:,.2f} per unit against the contract rate of "
                    f"R{agreed:,.2f} ({ref}): {pct:.1f}% above the agreed price (tolerance {tolerance * 100:.0f}%, "
                    "policy manual 10.1)."
                ),
            ))  # fmt: skip
        else:
            indicators.append(RiskIndicator(
                type="CONFIRMED_MATCH", severity=Severity.LOW, source=IndicatorSource.CONTRACT, citations=[citation],
                description=f"'{line.description}' at R{billed:,.2f} matches the contract rate of "
                f"R{agreed:,.2f} ({ref}).",
            ))  # fmt: skip
    return ContractCheckResult(indicators, notes)
