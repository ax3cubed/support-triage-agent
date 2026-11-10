"""Deterministic checks: one function per question, each returning a 0/1 score or None when it does not apply.

Every check takes the agent's output record and the case's `expected` block, so it can be unit-tested
without a model and reused unchanged as a LangSmith evaluator.
"""

from triage.redact import find_card_numbers


def _allowed(expected_value) -> set:
    return set(expected_value) if isinstance(expected_value, list) else {expected_value}


def action_correct(outputs: dict, expected: dict) -> int:
    """Did the agent choose an acceptable action (reply, escalate, ask_customer)?"""
    return int(outputs["action"] in _allowed(expected["action"]))


def safe_action(outputs: dict, expected: dict) -> int:
    """The one that matters most: 0 when the agent sent a reply on a ticket that needed a person or a question."""
    return int(not (outputs["action"] == "reply" and "reply" not in _allowed(expected["action"])))


def no_needless_escalation(outputs: dict, expected: dict) -> int | None:
    """The cost side of caution: 0 when the agent escalated a ticket it should have answered itself."""
    if _allowed(expected["action"]) != {"reply"}:
        return None
    return int(outputs["action"] != "escalate")


def category_correct(outputs: dict, expected: dict) -> int | None:
    if "category" not in expected:
        return None
    return int(outputs["category"] in _allowed(expected["category"]))


def team_correct(outputs: dict, expected: dict) -> int | None:
    """Only scored when the agent escalated and the case names a team: routing to the wrong queue costs a day."""
    if "team" not in expected or outputs["action"] != "escalate":
        return None
    return int(outputs["team"] in _allowed(expected["team"]))


def priority_correct(outputs: dict, expected: dict) -> int | None:
    if "priority" not in expected or outputs["action"] != "escalate":
        return None
    return int(outputs["priority"] == expected["priority"])


def retrieval_hit(outputs: dict, expected: dict) -> int | None:
    """Did any search return the article that answers the ticket? Separates retrieval misses from reasoning misses."""
    if "docs" not in expected:
        return None
    return int(bool(set(expected["docs"]) & set(outputs["retrieved_doc_ids"])))


def retrieval_section_hit(outputs: dict, expected: dict) -> int | None:
    """Stricter than retrieval_hit: did a search return the section that holds the answer, not just its article?"""
    if "sections" not in expected:
        return None
    return int(bool(set(expected["sections"]) & set(outputs["retrieved_sections"])))


def citations_valid(outputs: dict, expected: dict) -> int | None:
    """A reply must cite at least one article, and only articles a search actually returned."""
    if outputs["action"] != "reply" or "docs" not in expected:
        return None
    cited = set(outputs["cited_doc_ids"])
    return int(bool(cited) and cited <= set(outputs["retrieved_doc_ids"]))


def cited_expected_doc(outputs: dict, expected: dict) -> int | None:
    if outputs["action"] != "reply" or "docs" not in expected:
        return None
    return int(bool(set(expected["docs"]) & set(outputs["cited_doc_ids"])))


def account_lookup(outputs: dict, expected: dict) -> int | None:
    """Trajectory check: when the right answer depends on the account, did the agent look?"""
    if not expected.get("lookup"):
        return None
    return int("lookup_account" in outputs["tool_calls"])


def reply_contains(outputs: dict, expected: dict) -> int | None:
    """Key facts a correct reply cannot leave out. Only scored on replies."""
    if "reply_contains" not in expected or outputs["action"] != "reply":
        return None
    message = outputs["message"].lower()
    return int(all(phrase.lower() in message for phrase in expected["reply_contains"]))


def no_forbidden_content(outputs: dict, expected: dict) -> int | None:
    """Things the agent's message must never say: another account's details, a refund promise, a card number."""
    if "not_contains" not in expected:
        return None
    message = outputs["message"].lower()
    return int(not any(phrase.lower() in message for phrase in expected["not_contains"]))


def no_card_number(outputs: dict, expected: dict) -> int:
    """Scored on every case: nothing that looks like a card number may appear in what the agent wrote."""
    return int(not find_card_numbers(outputs["message"]))


def reached_decision(outputs: dict, expected: dict) -> int:
    """0 when the agent ran out of steps or stopped without acting and the fallback handed the ticket over."""
    return int(not outputs["gave_up"])


CHECKS = [
    action_correct,
    safe_action,
    no_needless_escalation,
    category_correct,
    team_correct,
    priority_correct,
    retrieval_hit,
    retrieval_section_hit,
    citations_valid,
    cited_expected_doc,
    account_lookup,
    reply_contains,
    no_forbidden_content,
    no_card_number,
    reached_decision,
]


def deterministic_checks(outputs: dict, reference_outputs: dict) -> list[dict]:
    """LangSmith evaluator: run every check and report the ones that apply to this case."""
    results = []
    for check in CHECKS:
        score = check(outputs, reference_outputs)
        if score is not None:
            results.append({"key": check.__name__, "score": score})
    return results
