from evals.dataset import load_cases
from triage import kb
from triage.agent import ACTION_NAMES

ALLOWED_KEYS = {"action", "category", "team", "priority", "docs", "sections", "lookup", "reply_contains", "not_contains"}


def _values(value) -> list:
    return value if isinstance(value, list) else [value]


def test_every_case_is_well_formed():
    cases = load_cases()
    ids = [case["id"] for case in cases]

    assert len(ids) == len(set(ids))
    for case in cases:
        expected = case["expected"]
        assert set(expected) <= ALLOWED_KEYS, case["id"]
        assert set(_values(expected["action"])) <= set(ACTION_NAMES.values()), case["id"]
        assert set(expected.get("docs", [])) <= kb.doc_ids(), case["id"]
        assert set(expected.get("sections", [])) <= kb.section_ids(), case["id"]
        assert case["slice"] in {"normal", "edge", "adversarial"}, case["id"]
