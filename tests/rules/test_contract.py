"""Contract-rate check: the AI maps and quotes; code verifies and compares."""

from decimal import Decimal

import pytest

from tests.factories import invoice
from trustagent.domain import IndicatorSource, LineItem, Severity
from trustagent.rules.contract import check_contract_terms

CLAUSE = (
    "Supply Agreement (ABC-2026-017) | 4 Rates\n4.1 The following rates apply for the Term, excluding VAT:\n"
    "Item Unit Rate (ZAR, excl. VAT)\nErgonomic office chairs - Model EX500 each R 3,950.00\n"
    "Rush turnaround (48 hours) surcharge 10% of the order value"
)
CHUNKS = {7: {"content": CLAUSE, "contract_id": "ABC-2026-017",
              "citation": {"source": "ABC-2026-017.pdf", "page": 1, "section": "4 Rates"}}}  # fmt: skip
INV = invoice(line_items=[LineItem(description="Ergonomic office chairs - Model EX500", quantity=Decimal(25),
                                   unit_price=Decimal("4500"), total=Decimal("112500"))])  # fmt: skip
TOL = Decimal("0.02")


def term(**overrides):
    t = {"invoice_line": "Ergonomic office chairs - Model EX500", "contract_item": "Ergonomic office chairs",
         "agreed_price": "R 3,950.00", "price_basis": "per_unit", "source_chunk_id": 7,
         "quote": "Ergonomic office chairs - Model EX500 each R 3,950.00"}  # fmt: skip
    t.update(overrides)
    return t


def test_price_above_contract_is_a_cited_deviation():
    result = check_contract_terms(INV, [term()], CHUNKS, TOL)
    dev = result.indicators[0]
    assert dev.type == "CONTRACT_DEVIATION" and dev.source == IndicatorSource.CONTRACT
    assert dev.severity == Severity.HIGH  # 13.9% above
    assert "R4,500.00" in dev.description and "R3,950.00" in dev.description and "13.9%" in dev.description
    assert dev.citations[0].section == "4 Rates" and dev.citations[0].page == 1


@pytest.mark.parametrize("billed, expected", [("4029.00", "CONFIRMED_MATCH"), ("4029.01", "CONTRACT_DEVIATION")])
def test_two_percent_tolerance_boundary(billed, expected):
    inv = INV.model_copy(update={"line_items": [INV.line_items[0].model_copy(update={"unit_price": Decimal(billed)})]})
    assert check_contract_terms(inv, [term()], CHUNKS, TOL).indicators[0].type == expected


@pytest.mark.parametrize(
    "override, note",
    [
        ({"source_chunk_id": 99}, "not among the retrieved clauses"),
        ({"quote": "Ergonomic office chairs each R 3,500.00"}, "not found in the clause"),
        ({"agreed_price": "R 3,500.00"}, "not in the quoted clause"),
        ({"price_basis": "percentage", "agreed_price": "10% of the order value"}, "not a per-unit rate"),
        ({"invoice_line": "Standing desks"}, "no matching line"),
    ],
)
def test_unverifiable_terms_are_dropped_with_a_note(override, note):
    result = check_contract_terms(INV, [term(**override)], CHUNKS, TOL)
    assert result.indicators == [] and note in result.notes[0]


def test_line_matching_ignores_case_and_spacing():
    result = check_contract_terms(INV, [term(invoice_line="ergonomic  office chairs - model ex500")], CHUNKS, TOL)
    assert result.indicators[0].type == "CONTRACT_DEVIATION"
