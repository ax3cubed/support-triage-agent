"""The triage agent: a small LangGraph loop that ends in exactly one action.

    redact -> agent <-> tools -> END
                 \\-> give_up -> END

The model can search the help centre and look up the sender's account as often as it needs, then
must finish with one action tool: send a reply, hand over to a human, or ask the customer a question.
If it never does, `give_up` hands the ticket to a human, so every run ends in a decision.
"""

import json
import operator
from typing import Annotated, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, ValidationError

from triage import accounts, kb, policy
from triage.config import AGENT_MODEL, MAX_MODEL_CALLS, MAX_TICKET_CHARS, MAX_TOOL_CALLS_PER_STEP
from triage.prompts import PROMPTS
from triage.redact import find_card_numbers_strict, redact, redact_strict

Category = Literal["product", "billing", "bug", "account_security", "privacy", "other"]
Team = Literal["billing", "engineering", "security", "privacy", "support"]
Priority = Literal["urgent", "normal"]

CATEGORY_HELP = (
    "product: how Ledgerly works. billing: plans, charges, refunds, the subscription. bug: something is broken. "
    "account_security: sign-in, 2FA, ownership, suspicious activity. privacy: personal data and legal requests. "
    "other: none of these."
)


class search_help_center(BaseModel):  # noqa: N801 - the class name is the tool name the model sees
    """Search Ledgerly's help centre. Returns the best matching article sections with their doc_id."""

    query: str = Field(description="A short English search phrase, for example 'turn on automatic reminders'.")


class lookup_account(BaseModel):  # noqa: N801
    """Look up the Ledgerly account of the person who sent this ticket: plan, role, subscription and recent charges.

    Takes no arguments. It only ever returns the sender's own account.
    """


class get_recent_charges(BaseModel):  # noqa: N801
    """Get the recent subscription charges on the sender's own Ledgerly account.

    Takes no arguments. Use it only when the ticket is about a charge, a payment to Ledgerly or a refund.
    """


class send_reply(BaseModel):  # noqa: N801
    """Finish by sending this reply to the person. It is sent as written, with no human review."""

    category: Category = Field(description=CATEGORY_HELP)
    body: str = Field(description="The full reply to the person.")
    cited_doc_ids: list[str] = Field(description="doc_id of every help-centre article the reply relies on.")


class escalate_to_human(BaseModel):  # noqa: N801
    """Finish by handing the ticket to a team at Ledgerly. The person is told a human will follow up."""

    category: Category = Field(description=CATEGORY_HELP)
    team: Team
    priority: Priority
    summary: str = Field(description="What the person needs and what you found, so the team need not reread the ticket.")


class ask_customer(BaseModel):  # noqa: N801
    """Finish by asking the person one clarifying question, when the ticket cannot be understood without it."""

    category: Category = Field(description=CATEGORY_HELP)
    question: str


LOOKUP_TOOLS = [search_help_center, lookup_account]
ACTION_TOOLS = [send_reply, escalate_to_human, ask_customer]
ACTION_NAMES = {"send_reply": "reply", "escalate_to_human": "escalate", "ask_customer": "ask_customer"}


# What each agent version can see. In v0 one lookup returns the whole account, charges included.
# From v1 the lookup returns the basics only and charges need their own, deliberate tool call.
SPLIT_BILLING = {"v0": False, "v1": True, "v2": True, "v3": True}

# v3 hardens the agent in code. With this on:
# - the payment-details guard is the strict one, run before the graph starts and on outgoing messages
# - every tool call is checked against its schema, and extra fields are refused
# - an action must be called on its own; of two actions the more cautious is kept
# - a refund request cannot end in a reply (triage.policy.refund_gate)
# - errors and silence end in a hand-over that carries the ticket, not in a lost ticket
HARDENED = {"v0": False, "v1": False, "v2": False, "v3": True}
ACTION_CLASSES = {"send_reply": send_reply, "escalate_to_human": escalate_to_human, "ask_customer": ask_customer}
# Most cautious first: used when the model calls more than one action in a step.
CAUTION_ORDER = ["escalate_to_human", "ask_customer", "send_reply"]
NUDGE = "You must finish by calling exactly one action tool: send_reply, escalate_to_human or ask_customer."


def tools_for(version: str) -> list:
    """The tools offered to the model in this agent version."""
    if not SPLIT_BILLING[version]:
        return [*LOOKUP_TOOLS, *ACTION_TOOLS]
    basics = convert_to_openai_tool(lookup_account)
    basics["function"]["description"] = (
        "Look up the Ledgerly account of the person who sent this ticket: name, business, role, plan and "
        "subscription status. Takes no arguments. It only ever returns the sender's own account, and never charges."
    )
    return [search_help_center, basics, get_recent_charges, *ACTION_TOOLS]


class Ticket(TypedDict):
    """An incoming support request."""

    from_email: str
    subject: str
    body: str


class TriageState(TypedDict, total=False):
    """Everything one run accumulates. The list fields append across steps."""

    ticket: Ticket
    messages: Annotated[list[AnyMessage], add_messages]
    card_in_ticket: bool
    card_reached_model: bool
    outgoing_redactions: int
    retrieved: Annotated[list[dict], operator.add]
    searches: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[str], operator.add]
    saw_charges: bool
    redactions: int
    model_calls: int
    refused_calls: int
    nudged: bool
    model_error: str
    decision: dict


def build_graph(model: BaseChatModel | None = None, version: str = "v0"):
    """Compile one version of the agent. Pass a model to override the configured one (tests pass a scripted model)."""
    llm = (model or init_chat_model(AGENT_MODEL)).bind_tools(tools_for(version))
    system_prompt = PROMPTS[version]
    split_billing = SPLIT_BILLING[version]
    hardened = HARDENED[version]

    def ticket_text(state: TriageState) -> str:
        return f"{state['ticket']['subject']}\n{state['ticket']['body']}"

    def redact_ticket(state: TriageState) -> dict:
        ticket = state["ticket"]
        clean = redact_strict if hardened else redact
        subject, subject_count = clean(ticket["subject"])
        body, body_count = clean(ticket["body"])
        text = f"From: {ticket['from_email']}\nSubject: {subject}\n\n{body}"
        return {
            "messages": [SystemMessage(system_prompt), HumanMessage(text)],
            # In v3 the ticket was already cleaned once before the graph started; keep that count.
            "redactions": state.get("redactions", 0) + subject_count + body_count,
            "card_reached_model": bool(find_card_numbers_strict(text)),
            "model_calls": 0,
        }

    def call_model(state: TriageState) -> dict:
        calls = state["model_calls"] + 1
        if not hardened:
            return {"messages": [llm.invoke(state["messages"])], "model_calls": calls}
        for attempt in (1, 2):
            try:
                return {"messages": [llm.invoke(state["messages"])], "model_calls": calls}
            except Exception as error:  # noqa: BLE001 - any provider failure must end in a hand-over, not a crash
                if attempt == 2:
                    return {"model_error": type(error).__name__, "model_calls": calls}

    def nudge(state: TriageState) -> dict:
        return {"messages": [HumanMessage(NUDGE)], "nudged": True}

    def run_lookup(name: str, args: dict, state: TriageState, update: dict) -> str:
        if name == "search_help_center":
            query = args.get("query", "")
            if hardened:
                if not isinstance(query, str) or not query.strip():
                    update["refused_calls"] += 1
                    return "Not done. The query must be a short piece of text."
                query = query[:300]
            chunks = kb.search(query)
            update["retrieved"] += [{"doc_id": c.doc_id, "section": c.section, "text": c.text} for c in chunks]
            update["searches"].append({"query": query, "sections": [f"{c.doc_id}#{c.section}" for c in chunks]})
            return kb.format_chunks(chunks)
        if name == "lookup_account" or (name == "get_recent_charges" and split_billing):
            account = accounts.lookup(state["ticket"]["from_email"])
            shows_charges = name == "get_recent_charges" or not split_billing
            if not account:
                return "No Ledgerly account uses the sender's email."
            # Non-owners have a refusal string in place of charge rows, so they have seen no charges.
            if shows_charges and isinstance(account["recent_charges"], list):
                update["saw_charges"] = True
            if name == "get_recent_charges":
                return json.dumps(account["recent_charges"], ensure_ascii=False)
            if split_billing:
                return json.dumps({k: v for k, v in account.items() if k != "recent_charges"}, ensure_ascii=False)
            return json.dumps(account, ensure_ascii=False)
        return f"There is no tool called {name}."

    def checked_action(name: str, args: dict) -> tuple[dict | None, str]:
        """Return the decision for a valid action call, or None and the reason it was refused."""
        extra = sorted(set(args) - set(ACTION_CLASSES[name].model_fields))
        if extra:
            return None, f"Not done. These fields are not allowed: {', '.join(extra)}."
        try:
            fields = ACTION_CLASSES[name].model_validate(args).model_dump()
        except ValidationError as error:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
            return None, f"Not done. Invalid arguments: {problems}. Call the action again with valid values."
        return {**fields, "action": ACTION_NAMES[name]}, "Done."

    def run_tools(state: TriageState) -> dict:
        update: dict = {"messages": [], "retrieved": [], "searches": [], "tool_calls": [], "refused_calls": 0}
        calls = state["messages"][-1].tool_calls
        refused: dict[str, str] = {}
        if hardened:
            actions = [call for call in calls if call["name"] in ACTION_NAMES]
            if actions and len(actions) < len(calls):
                # The model has not read the lookup results yet, so it may not act on them.
                for call in actions:
                    refused[call["id"]] = "Not done. Read the lookup results first, then call one action on its own."
            elif len(actions) > 1:
                kept = min(actions, key=lambda call: CAUTION_ORDER.index(call["name"]))
                for call in actions:
                    if call is not kept:
                        refused[call["id"]] = "Not done. Only one action is allowed; the most cautious one was kept."
            for call in calls[MAX_TOOL_CALLS_PER_STEP:]:
                refused[call["id"]] = "Not done. Too many tool calls in one step."

        for call in calls:
            name, args = call["name"], call["args"]
            update["tool_calls"].append(name)
            if call["id"] in refused:
                update["refused_calls"] += 1
                result = refused[call["id"]]
            elif name not in ACTION_NAMES:
                try:
                    result = run_lookup(name, args, state, update)
                except Exception as error:  # noqa: BLE001 - a failing tool is reported to the model, not raised
                    if not hardened:
                        raise
                    update["refused_calls"] += 1
                    result = f"Tool error: {type(error).__name__}."
            elif "decision" in update:
                result = "Done."
            elif not hardened:
                update["decision"] = {"action": ACTION_NAMES[name], **args}
                result = "Done."
            else:
                decision, result = checked_action(name, args)
                if decision is None:
                    update["refused_calls"] += 1
                else:
                    decision = policy.refund_gate(ticket_text(state), decision)
                    # Clean whatever the agent wrote before it leaves, in case a detail got through.
                    for field in ("body", "summary", "question"):
                        if isinstance(decision.get(field), str):
                            decision[field], found = redact_strict(decision[field])
                            update["outgoing_redactions"] = update.get("outgoing_redactions", 0) + found
                    update["decision"] = decision
            update["messages"].append(ToolMessage(result, tool_call_id=call["id"]))
        update["refused_calls"] += state.get("refused_calls", 0)
        return update

    def give_up(state: TriageState) -> dict:
        if not hardened:
            return {
                "decision": {
                    "action": "escalate",
                    "category": "other",
                    "team": "support",
                    "priority": "normal",
                    "summary": "The assistant could not reach a decision on this ticket.",
                    "gave_up": True,
                }
            }
        last = state["messages"][-1]
        if state.get("model_error"):
            reason = f"the model failed twice ({state['model_error']})"
        elif state["model_calls"] >= MAX_MODEL_CALLS:
            reason = "it used all its steps without choosing an action"
        else:
            reason = "it stopped without choosing an action"
        category, team, priority = policy.fallback_route(ticket_text(state))
        last_words = last.content if isinstance(last, AIMessage) and isinstance(last.content, str) else ""
        summary = (
            f"The assistant could not reach a decision: {reason}. Nothing was sent to the customer.\n"
            f"From: {state['ticket']['from_email']}\nSubject: {state['ticket']['subject']}\n"
            f"Ticket: {state['ticket']['body'][:1500]}\n"
            f"Steps taken: {', '.join(state.get('tool_calls', [])) or 'none'}."
            + (f"\nThe assistant's last words: {last_words[:500]}" if last_words else "")
        )
        return {
            "decision": {
                "action": "escalate",
                "category": category,
                "team": team,
                "priority": priority,
                "summary": redact_strict(summary)[0],
                "gave_up": True,
            }
        }

    def after_model(state: TriageState) -> str:
        if state.get("model_error"):
            return "give_up"
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        # A model that answers in words gets one reminder to use an action tool before the fallback.
        if hardened and not state.get("nudged") and state["model_calls"] < MAX_MODEL_CALLS:
            return "nudge"
        return "give_up"

    def after_tools(state: TriageState) -> str:
        if "decision" in state:
            return END
        return "agent" if state["model_calls"] < MAX_MODEL_CALLS else "give_up"

    graph = StateGraph(TriageState)
    graph.add_node("redact", redact_ticket)
    graph.add_node("agent", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("nudge", nudge)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "redact")
    graph.add_edge("redact", "agent")
    graph.add_conditional_edges("agent", after_model, ["tools", "nudge", "give_up"])
    graph.add_edge("nudge", "agent")
    graph.add_conditional_edges("tools", after_tools, ["agent", "give_up", END])
    graph.add_edge("give_up", END)
    return graph.compile()


def summarise(state: TriageState) -> dict:
    """Flatten a finished run into the record that evaluators and the CLI read."""
    decision = state["decision"]
    retrieved = state.get("retrieved", [])
    return {
        "action": decision["action"],
        "category": decision.get("category"),
        "team": decision.get("team"),
        "priority": decision.get("priority"),
        "message": decision.get("body") or decision.get("summary") or decision.get("question") or "",
        "cited_doc_ids": decision.get("cited_doc_ids", []),
        "retrieved_doc_ids": list(dict.fromkeys(chunk["doc_id"] for chunk in retrieved)),
        "retrieved_sections": list(dict.fromkeys(f"{chunk['doc_id']}#{chunk['section']}" for chunk in retrieved)),
        "searches": state.get("searches", []),
        "retrieved_context": "\n\n".join(f"[{c['doc_id']}] {c['section']}\n{c['text']}" for c in retrieved),
        "tool_calls": state.get("tool_calls", []),
        "saw_charges": state.get("saw_charges", False),
        "card_in_ticket": state.get("card_in_ticket", False),
        "card_reached_model": state.get("card_reached_model", False),
        "outgoing_redactions": state.get("outgoing_redactions", 0),
        "refused_calls": state.get("refused_calls", 0),
        "gate": decision.get("gate"),
        "model_calls": state.get("model_calls", 0),
        "redactions": state.get("redactions", 0),
        "gave_up": decision.get("gave_up", False),
    }


def initial_state(ticket: Ticket, version: str) -> dict:
    """The state a run starts from. From v3 the ticket is checked and cleaned here, before anything is traced."""
    if not HARDENED[version]:
        raw = f"{ticket['from_email']}\n{ticket['subject']}\n{ticket['body']}"
        return {"ticket": ticket, "card_in_ticket": bool(find_card_numbers_strict(raw))}
    # A missing or odd field must not crash the run, and a line break in the address must not let
    # the sender forge extra header lines in what the model reads.
    sender = " ".join(str(ticket.get("from_email") or "").split())[:200]
    subject = str(ticket.get("subject") or "")[:300]
    body = str(ticket.get("body") or "")[:MAX_TICKET_CHARS]
    clean_sender, _ = redact_strict(sender)
    clean_subject, subject_count = redact_strict(subject)
    clean_body, body_count = redact_strict(body)
    return {
        "ticket": {"from_email": clean_sender, "subject": clean_subject, "body": clean_body},
        "card_in_ticket": bool(find_card_numbers_strict(f"{sender}\n{subject}\n{body}")),
        "redactions": subject_count + body_count,
    }


def triage(ticket: Ticket, model: BaseChatModel | None = None, version: str = "v0") -> dict:
    """Run one ticket through the agent and return its decision record."""
    graph = build_graph(model, version)
    config = {"run_name": "triage", "metadata": {"agent_version": version}}
    return summarise(graph.invoke(initial_state(ticket, version), config=config))
