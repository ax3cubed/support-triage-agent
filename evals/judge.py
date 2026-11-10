"""LLM judge for the two questions a string match cannot answer about a reply.

grounded: is every factual claim in the reply supported by the help-centre text the agent retrieved,
          or by the account record? Invented steps, prices or promises fail.
helpful:  does the reply answer what the person asked, in a tone you would be happy to send?

Each verdict is pass/fail with the reasoning written first, which makes disagreements with a human
label easy to read. Only replies are judged: an escalation's quality is covered by the deterministic
routing checks.
"""

from functools import lru_cache

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from triage.config import JUDGE_MODEL
from triage.redact import redact

JUDGE_PROMPT = """You are reviewing a reply that an AI support assistant sent to a customer of Ledgerly, an invoicing product.

<ticket>
From: {from_email}
Subject: {subject}

{body}
</ticket>

<help_centre_sections_the_assistant_retrieved>
{context}
</help_centre_sections_the_assistant_retrieved>

<reply_sent>
{reply}
</reply_sent>

Judge two things separately.

GROUNDED. Pass only if every factual claim about Ledgerly in the reply (steps, menu names, prices, limits, policies,
timelines, what will happen next) is stated in the retrieved sections. The assistant may also have looked up the
sender's own account, so statements about the sender's plan, role or charges are acceptable. Fail if the reply states
anything about Ledgerly that the sections do not say, or promises an outcome (such as a refund) that the sections say
someone else decides. General courtesy and restating the customer's question are not claims.

HELPFUL. Pass only if the reply addresses what the person actually asked, tells them what to do next, and has a tone a
careful support lead would send unedited. Fail if it answers a different question, buries the answer, lectures, or is
curt with an upset customer.

Give your reasoning for each before the verdict."""


class Verdict(BaseModel):
    """The judge's structured answer."""

    grounded_reasoning: str = Field(description="Name any claim not supported by the sections, or say that all are.")
    grounded: bool
    helpful_reasoning: str
    helpful: bool


@lru_cache(maxsize=1)
def _judge():
    return init_chat_model(JUDGE_MODEL).with_structured_output(Verdict)


def judge_reply(inputs: dict, outputs: dict) -> Verdict:
    """Ask the judge about one reply."""
    prompt = JUDGE_PROMPT.format(
        from_email=inputs["from_email"],
        subject=redact(inputs["subject"])[0],
        body=redact(inputs["body"])[0],
        context=outputs["retrieved_context"] or "(nothing was retrieved)",
        reply=outputs["message"],
    )
    return _judge().invoke(prompt)


def reply_judge(inputs: dict, outputs: dict) -> list[dict]:
    """LangSmith evaluator: grounded and helpful, for replies only."""
    if outputs["action"] != "reply":
        return []
    verdict = judge_reply(inputs, outputs)
    return [
        {"key": "grounded", "score": int(verdict.grounded), "comment": verdict.grounded_reasoning},
        {"key": "helpful", "score": int(verdict.helpful), "comment": verdict.helpful_reasoning},
    ]
