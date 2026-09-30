import pytest

from trustagent.rules.matching import name_similarity, normalize_name


def test_normalize_strips_punctuation_and_entity_words():
    assert normalize_name("ABC Office Solutions (Pty) Ltd") == "abc office"
    assert normalize_name("Metro Cleaning Services CC") == "metro cleaning cc"


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("ABC Office Solutions", "ABC Office Solutions (Pty) Ltd", 1.0),
        ("Metro Cleaning Services", "Metro Cleaning Services CC", 0.85),  # containment
        ("Prestige Catering & Events", "Prestige Events Holdings", 0.5),  # 2 shared of 4 tokens
        ("QuickShip Logistics", "Q-Ship Trading", 0.0),
        ("", "Anything", 0.0),
        ("Pty Ltd", "Pty Ltd", 0.0),  # nothing left after normalising
    ],
)
def test_name_similarity(a, b, expected):
    assert name_similarity(a, b) == pytest.approx(expected)


def test_similarity_is_symmetric():
    assert name_similarity("Digital Print Co", "Print Digital") == name_similarity("Print Digital", "Digital Print Co")


# --- supplier matching ------------------------------------------------------------------

from tests.factories import seed_supplier  # noqa: E402
from trustagent.rules.matching import find_best_supplier  # noqa: E402

SEEDED = [seed_supplier(i) for i in ("SUP-001", "SUP-002", "SUP-003")]


@pytest.mark.parametrize(
    "printed, expected",
    [
        ("ABC Office Solutions (Pty) Ltd", "SUP-001"),
        ("Metro Cleaning Services CC", "SUP-002"),
        ("Digital Print Co (Pty) Ltd", "SUP-003"),
        ("Nexus Advisory Partners", None),
        ("Secure IT Solutions (Pty) Ltd", None),
        ("Prestige Catering & Events", None),
    ],
)
def test_sample_supplier_names_match_as_expected(printed, expected):
    match = find_best_supplier(printed, SEEDED, threshold=0.5)
    assert (match.supplier.id if match.supplier else None) == expected


def test_below_threshold_reports_confidence_but_no_supplier():
    match = find_best_supplier("Metro Logistics", SEEDED, threshold=0.5)
    assert match.supplier is None and 0 < match.confidence < 0.5
