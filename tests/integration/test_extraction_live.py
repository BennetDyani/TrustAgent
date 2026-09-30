"""Live extraction against the configured model (real API calls, real quota).

Deselected by default. Run with:  uv run pytest -m live
"""

import pytest

from trustagent.config import get_settings
from trustagent.extraction.labels import compare, load_labels
from trustagent.extraction.pipeline import extract_document

pytestmark = pytest.mark.live

LABELS = load_labels()


@pytest.mark.parametrize("name", [n for n in LABELS if not n.endswith(".json")])
def test_sample_invoice_extracts_correctly(name):
    content = (get_settings().invoices_dir / name).read_bytes()
    result = extract_document(name, content)
    mismatches = {f: (e, a) for f, (e, a, ok) in compare(result.invoice, LABELS[name]).items() if not ok}
    assert mismatches == {}, f"{name}: {mismatches}"
