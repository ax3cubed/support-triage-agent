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
from pydantic import BaseModel, Field

from triage import accounts, kb
from triage.config import AGENT_MODEL, MAX_MODEL_CALLS
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

# From v3 the payment-details guard is the strict one. It runs before the graph starts, so the trace
# never holds the original ticket, and again on every message the agent writes.
STRICT_GUARD = {"v0": False, "v1": False, "v2": False, "v3": True}


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
    decision: dict


def build_graph(model: BaseChatModel | None = None, version: str = "v0"):
    """Compile one version of the agent. Pass a model to override the configured one (tests pass a scripted model)."""
    llm = (model or init_chat_model(AGENT_MODEL)).bind_tools(tools_for(version))
    system_prompt = PROMPTS[version]
    split_billing = SPLIT_BILLING[version]
    strict_guard = STRICT_GUARD[version]

    def redact_ticket(state: TriageState) -> dict:
        ticket = state["ticket"]
        clean = redact_strict if strict_guard else redact
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
        return {"messages": [llm.invoke(state["messages"])], "model_calls": state["model_calls"] + 1}

    def run_tools(state: TriageState) -> dict:
        update: dict = {"messages": [], "retrieved": [], "searches": [], "tool_calls": []}
        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            update["tool_calls"].append(name)
            if name == "search_help_center":
                chunks = kb.search(args.get("query", ""))
                update["retrieved"] += [{"doc_id": c.doc_id, "section": c.section, "text": c.text} for c in chunks]
                update["searches"].append(
                    {"query": args.get("query", ""), "sections": [f"{c.doc_id}#{c.section}" for c in chunks]}
                )
                result = kb.format_chunks(chunks)
            elif name == "lookup_account" or (name == "get_recent_charges" and split_billing):
                account = accounts.lookup(state["ticket"]["from_email"])
                shows_charges = name == "get_recent_charges" or not split_billing
                if not account:
                    result = "No Ledgerly account uses the sender's email."
                elif name == "get_recent_charges":
                    result = json.dumps(account["recent_charges"], ensure_ascii=False)
                elif split_billing:
                    result = json.dumps({k: v for k, v in account.items() if k != "recent_charges"}, ensure_ascii=False)
                else:
                    result = json.dumps(account, ensure_ascii=False)
                # Non-owners have a refusal string in place of charge rows, so they have seen no charges.
                if account and shows_charges and isinstance(account["recent_charges"], list):
                    update["saw_charges"] = True
            elif name in ACTION_NAMES:
                if "decision" not in update:
                    update["decision"] = {"action": ACTION_NAMES[name], **args}
                    if strict_guard:
                        # Clean whatever the agent wrote before it leaves, in case a detail got through.
                        for field in ("body", "summary", "question"):
                            if isinstance(args.get(field), str):
                                cleaned, found = redact_strict(args[field])
                                update["decision"][field] = cleaned
                                update["outgoing_redactions"] = update.get("outgoing_redactions", 0) + found
                result = "Done."
            else:
                result = f"There is no tool called {name}."
            update["messages"].append(ToolMessage(result, tool_call_id=call["id"]))
        return update

    def give_up(state: TriageState) -> dict:
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

    def after_model(state: TriageState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "give_up"

    def after_tools(state: TriageState) -> str:
        if "decision" in state:
            return END
        return "agent" if state["model_calls"] < MAX_MODEL_CALLS else "give_up"

    graph = StateGraph(TriageState)
    graph.add_node("redact", redact_ticket)
    graph.add_node("agent", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "redact")
    graph.add_edge("redact", "agent")
    graph.add_conditional_edges("agent", after_model, ["tools", "give_up"])
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
        "model_calls": state.get("model_calls", 0),
        "redactions": state.get("redactions", 0),
        "gave_up": decision.get("gave_up", False),
    }


def initial_state(ticket: Ticket, version: str) -> dict:
    """The state a run starts from. From v3 the ticket is cleaned here, before anything is traced."""
    raw = f"{ticket['from_email']}\n{ticket['subject']}\n{ticket['body']}"
    state: dict = {"ticket": ticket, "card_in_ticket": bool(find_card_numbers_strict(raw))}
    if STRICT_GUARD[version]:
        subject, subject_count = redact_strict(ticket["subject"])
        body, body_count = redact_strict(ticket["body"])
        state["ticket"] = {**ticket, "subject": subject, "body": body}
        state["redactions"] = subject_count + body_count
    return state


def triage(ticket: Ticket, model: BaseChatModel | None = None, version: str = "v0") -> dict:
    """Run one ticket through the agent and return its decision record."""
    graph = build_graph(model, version)
    config = {"run_name": "triage", "metadata": {"agent_version": version}}
    return summarise(graph.invoke(initial_state(ticket, version), config=config))
