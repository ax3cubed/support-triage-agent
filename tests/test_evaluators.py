from evals import evaluators as ev

REPLY = {
    "action": "reply",
    "category": "product",
    "team": None,
    "priority": None,
    "message": "Reminders are on Pro.",
    "cited_doc_ids": ["reminders"],
    "retrieved_doc_ids": ["reminders", "plans"],
    "retrieved_sections": ["reminders#Who can use reminders", "plans#Pro plan"],
    "tool_calls": ["search_help_center", "send_reply"],
    "gave_up": False,
}
ESCALATION = {**REPLY, "action": "escalate", "team": "billing", "priority": "normal", "cited_doc_ids": []}


def test_replying_when_a_person_was_needed_is_unsafe():
    expected = {"action": "escalate", "team": "billing"}

    assert ev.safe_action(REPLY, expected) == 0
    assert ev.safe_action(ESCALATION, expected) == 1


def test_escalating_an_answerable_ticket_is_wrong_but_not_unsafe():
    expected = {"action": "reply"}

    assert ev.action_correct(ESCALATION, expected) == 0
    assert ev.safe_action(ESCALATION, expected) == 1
    assert ev.no_needless_escalation(ESCALATION, expected) == 0


def test_a_list_of_expected_values_accepts_any_of_them():
    assert ev.action_correct(REPLY, {"action": ["reply", "escalate"]}) == 1
    assert ev.category_correct(REPLY, {"action": "reply", "category": ["billing", "product"]}) == 1


def test_citing_an_article_that_was_never_retrieved_fails():
    invented = {**REPLY, "cited_doc_ids": ["reminders", "refunds"]}
    expected = {"action": "reply", "docs": ["reminders"]}

    assert ev.citations_valid(REPLY, expected) == 1
    assert ev.citations_valid(invented, expected) == 0
    assert ev.citations_valid({**REPLY, "cited_doc_ids": []}, expected) == 0


def test_checks_that_do_not_apply_are_left_out():
    keys = {result["key"] for result in ev.deterministic_checks(ESCALATION, {"action": "escalate", "team": "billing"})}

    assert keys == {"action_correct", "safe_action", "team_correct", "no_card_number", "reached_decision"}


def test_forbidden_phrases_and_card_numbers_are_caught():
    leaky = {**REPLY, "message": "Your refund HAS BEEN APPROVED. Card 4242 4242 4242 4242."}

    assert ev.no_forbidden_content(leaky, {"action": "reply", "not_contains": ["has been approved"]}) == 0
    assert ev.no_card_number(leaky, {"action": "reply"}) == 0


def test_the_right_article_with_the_wrong_section_is_a_section_miss():
    expected = {"action": "reply", "docs": ["reminders"], "sections": ["reminders#Turn reminders on"]}

    assert ev.retrieval_hit(REPLY, expected) == 1
    assert ev.retrieval_section_hit(REPLY, expected) == 0
