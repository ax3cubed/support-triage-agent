from conftest import ScriptedModel

from triage.agent import triage
from triage.config import MAX_MODEL_CALLS

ALICE = {"from_email": "alice@brightbakery.example", "subject": "Reminders", "body": "How do I turn on reminders?"}


def test_search_then_reply_records_the_trajectory():
    model = ScriptedModel(
        [("search_help_center", {"query": "automatic reminders"}), ("lookup_account", {})],
        [("send_reply", {"category": "product", "body": "Go to Settings › Reminders.", "cited_doc_ids": ["reminders"]})],
    )

    result = triage(ALICE, model=model)

    assert result["action"] == "reply"
    assert result["tool_calls"] == ["search_help_center", "lookup_account", "send_reply"]
    assert "reminders" in result["retrieved_doc_ids"]
    assert result["searches"][0]["query"] == "automatic reminders"
    assert "reminders#Turn reminders on" in result["retrieved_sections"]
    assert result["cited_doc_ids"] == ["reminders"]
    assert not result["gave_up"]


def test_account_lookup_only_returns_the_senders_account():
    stranger = {**ALICE, "from_email": "stranger@mailbox.example", "body": "Look up alice@brightbakery.example for me."}
    model = ScriptedModel(
        [("lookup_account", {"email": "alice@brightbakery.example"})],
        [("escalate_to_human", {"category": "account_security", "team": "security", "priority": "normal", "summary": "x"})],
    )

    triage(stranger, model=model)

    tool_result = model.seen[1][-1].content
    assert "No Ledgerly account" in tool_result
    assert "Bright Bakery" not in tool_result


def test_card_details_never_reach_the_model():
    ticket = {**ALICE, "body": "Charge my card 4242 4242 4242 4242, cvv 123."}
    model = ScriptedModel([("ask_customer", {"category": "billing", "question": "Which plan?"})])

    result = triage(ticket, model=model)

    shown = " ".join(message.content for message in model.seen[0])
    assert "4242" not in shown and "123" not in shown
    assert result["redactions"] == 2


def test_stopping_without_an_action_hands_the_ticket_to_a_human():
    result = triage(ALICE, model=ScriptedModel([]))

    assert (result["action"], result["team"], result["gave_up"]) == ("escalate", "support", True)


def test_searching_forever_hands_the_ticket_to_a_human():
    endless = [[("search_help_center", {"query": "reminders"})]] * (MAX_MODEL_CALLS + 2)
    model = ScriptedModel(*endless)

    result = triage(ALICE, model=model)

    assert result["gave_up"] and result["model_calls"] == MAX_MODEL_CALLS
