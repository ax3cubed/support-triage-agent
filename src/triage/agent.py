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
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from triage import accounts, kb
from triage.config import AGENT_MODEL, MAX_MODEL_CALLS
from triage.prompts import PROMPTS
from triage.redact import redact

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


class Ticket(TypedDict):
    """An incoming support request."""

    from_email: str
    subject: str
    body: str


class TriageState(TypedDict, total=False):
    """Everything one run accumulates. The list fields append across steps."""

    ticket: Ticket
    messages: Annotated[list[AnyMessage], add_messages]
    retrieved: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[str], operator.add]
    redactions: int
    model_calls: int
    decision: dict


def build_graph(model: BaseChatModel | None = None, prompt_version: str = "v0"):
    """Compile the agent. Pass a model to override the configured one (tests pass a scripted model)."""
    llm = (model or init_chat_model(AGENT_MODEL)).bind_tools(LOOKUP_TOOLS + ACTION_TOOLS)
    system_prompt = PROMPTS[prompt_version]

    def redact_ticket(state: TriageState) -> dict:
        ticket = state["ticket"]
        subject, subject_count = redact(ticket["subject"])
        body, body_count = redact(ticket["body"])
        text = f"From: {ticket['from_email']}\nSubject: {subject}\n\n{body}"
        return {
            "messages": [SystemMessage(system_prompt), HumanMessage(text)],
            "redactions": subject_count + body_count,
            "model_calls": 0,
        }

    def call_model(state: TriageState) -> dict:
        return {"messages": [llm.invoke(state["messages"])], "model_calls": state["model_calls"] + 1}

    def run_tools(state: TriageState) -> dict:
        update: dict = {"messages": [], "retrieved": [], "tool_calls": []}
        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            update["tool_calls"].append(name)
            if name == "search_help_center":
                chunks = kb.search(args.get("query", ""))
                update["retrieved"] += [{"doc_id": c.doc_id, "section": c.section, "text": c.text} for c in chunks]
                result = kb.format_chunks(chunks)
            elif name == "lookup_account":
                account = accounts.lookup(state["ticket"]["from_email"])
                result = json.dumps(account, ensure_ascii=False) if account else "No Ledgerly account uses the sender's email."
            elif name in ACTION_NAMES:
                if "decision" not in update:
                    update["decision"] = {"action": ACTION_NAMES[name], **args}
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
        "retrieved_context": "\n\n".join(f"[{c['doc_id']}] {c['section']}\n{c['text']}" for c in retrieved),
        "tool_calls": state.get("tool_calls", []),
        "model_calls": state.get("model_calls", 0),
        "redactions": state.get("redactions", 0),
        "gave_up": decision.get("gave_up", False),
    }


def triage(ticket: Ticket, model: BaseChatModel | None = None, prompt_version: str = "v0") -> dict:
    """Run one ticket through the agent and return its decision record."""
    graph = build_graph(model, prompt_version)
    state = graph.invoke({"ticket": ticket}, config={"run_name": "triage", "metadata": {"prompt_version": prompt_version}})
    return summarise(state)
