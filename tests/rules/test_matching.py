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
