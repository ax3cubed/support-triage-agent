from conftest import ScriptedModel
from langchain_core.tracers.context import collect_runs

from triage.agent import build_graph, initial_state, triage
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

    triage(stranger, model=model, version="v0")

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


DANIEL = {"from_email": "daniel@harbourprint.example", "subject": "Reminders", "body": "How do I stop reminders for one client?"}
REPLY_CALL = ("send_reply", {"category": "product", "body": "Open the client and choose Edit.", "cited_doc_ids": []})


def test_v0_account_lookup_shows_the_model_every_charge():
    model = ScriptedModel([("lookup_account", {})], [REPLY_CALL])

    result = triage(DANIEL, model=model, version="v0")

    assert "£15" in model.seen[1][-1].content
    assert result["saw_charges"]


def test_v1_account_lookup_returns_the_basics_without_charges():
    model = ScriptedModel([("lookup_account", {})], [REPLY_CALL])

    result = triage(DANIEL, model=model, version="v1")

    shown = model.seen[1][-1].content
    assert "Harbour Print" in shown and "owner" in shown
    assert "£15" not in shown and "recent_charges" not in shown
    assert not result["saw_charges"]


def test_v1_charges_need_their_own_tool_call():
    model = ScriptedModel([("get_recent_charges", {})], [REPLY_CALL])

    result = triage(DANIEL, model=model, version="v1")

    assert "£15" in model.seen[1][-1].content
    assert result["saw_charges"]


def test_v1_does_not_show_charges_to_someone_who_is_not_the_owner():
    member = {**DANIEL, "from_email": "tom@brightbakery.example"}
    model = ScriptedModel([("get_recent_charges", {})], [REPLY_CALL])

    result = triage(member, model=model, version="v1")

    assert "only the owner" in model.seen[1][-1].content
    assert not result["saw_charges"]


CARD_TICKET = {"from_email": "frank@greenleaf.example", "subject": "card", "body": "Use 5555 5555 5555 4444 321 instead."}
ASK = ("ask_customer", {"category": "billing", "question": "Which plan?"})


def test_a_card_followed_by_its_security_code_reaches_the_model_before_v3():
    old = triage(CARD_TICKET, model=ScriptedModel([ASK]), version="v2")
    new = triage(CARD_TICKET, model=ScriptedModel([ASK]), version="v3")

    assert old["card_in_ticket"] and old["card_reached_model"]
    assert new["card_in_ticket"] and not new["card_reached_model"]


def _traced_text(version: str) -> str:
    with collect_runs() as collected:
        build_graph(ScriptedModel([ASK]), version).invoke(initial_state(CARD_TICKET, version))

    def walk(run):
        yield run
        for child in run.child_runs:
            yield from walk(child)

    return " ".join(f"{run.inputs} {run.outputs}" for root in collected.traced_runs for run in walk(root))


def test_v3_keeps_the_card_out_of_the_trace():
    assert "5555 5555 5555 4444" in _traced_text("v2")
    assert "5555 5555 5555 4444" not in _traced_text("v3")


def test_v3_cleans_a_card_the_model_writes_into_its_own_message():
    search = [("search_help_center", {"query": "update the card on file"})]
    body = "I have noted card 4242 4242 4242 4242."
    leaky = ("send_reply", {"category": "billing", "body": body, "cited_doc_ids": ["subscription"]})

    result = triage(CARD_TICKET, model=ScriptedModel(search, [leaky]), version="v3")

    assert "4242" not in result["message"]
    assert result["outgoing_redactions"] == 1
