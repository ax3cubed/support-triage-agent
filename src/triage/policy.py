"""Rules the agent must follow whatever the model says. They are code, so a prompt cannot talk past them.

Used from agent v3.
"""

import re

# A ticket that asks for money back. Only the billing team may answer these.
_REFUND_TICKET = re.compile(
    r"\brefund|\bmoney back\b|\bchargeback|\breimburs|\bdispute"
    r"|\bcharged (?:me |us )?(?:twice|double)|\bdouble[- ]charg"
    r"|\b(?:took|taken|charged|billed)\b[^.?!]{0,40}\btwice\b"
    r"|\b(?:want|need|get|have)\b[^.?!]{0,30}\bback\b[^.?!]{0,20}(?:£|\bmoney\b|\bcharge)"
    r"|(?:£\d+|\bmoney\b|\bcharge)[^.?!]{0,30}\bback\b",
    re.IGNORECASE,
)
# A message that promises or refuses a refund, whatever the ticket asked.
_REFUND_VERDICT = re.compile(
    r"\brefund[^.?!]{0,60}\b(?:approved|processed|issued|on its way|confirmed)\b"
    r"|\b(?:will|we'll|i'll|shall)\b[^.?!]{0,30}\brefund"
    r"|\b(?:can't|cannot|won't|unable to|not able to)\b[^.?!]{0,30}\brefund"
    r"|\bnot (?:normally |usually )?(?:be )?refund(?:ed|able)\b|\bnon-refundable\b|\bno refunds?\b",
    re.IGNORECASE,
)
_SECURITY = re.compile(
    r"\bhack|\bunauthori[sz]ed|\bstolen\b|\bfraud|\bcompromis|\bbreach|\blocked out\b"
    r"|\b(?:did not|didn't|never) (?:make|create|send|do|authori[sz]e)\b|\b(?:wasn't|was not|not) me\b"
    r"|\bsomeone (?:else )?(?:is|has|logged|got|changed)\b",
    re.IGNORECASE,
)


def refund_gate(ticket_text: str, decision: dict) -> dict:
    """Turn a customer-facing answer to a refund request into a hand-over to billing.

    Fires when the ticket asks for money back and the agent chose to reply, or when any message to the
    customer promises or refuses a refund. The cost is a needless hand-over when someone only asks what
    the refund policy is; that is the safe side to err on.
    """
    if decision["action"] == "escalate":
        return decision
    message = decision.get("body") or decision.get("question") or ""
    asks_for_refund = bool(_REFUND_TICKET.search(ticket_text))
    gives_verdict = bool(_REFUND_VERDICT.search(message.replace("’", "'")))
    if not ((asks_for_refund and decision["action"] == "reply") or gives_verdict):
        return decision
    reason = "the message promised or refused a refund" if gives_verdict else "the ticket asks for money back"
    return {
        "action": "escalate",
        "category": "billing",
        "team": "billing",
        "priority": "normal",
        "summary": (
            f"Sent to billing by the refund rule ({reason}); only billing may decide a refund. "
            f"The assistant's draft, which was NOT sent to the customer: {message}"
        ),
        "gate": "refund",
    }


def fallback_route(ticket_text: str) -> tuple[str, str, str]:
    """Where a ticket goes when the agent fails to decide: (category, team, priority).

    A ticket that reads like an account takeover must not wait in the general queue.
    """
    if _SECURITY.search(ticket_text):
        return "account_security", "security", "urgent"
    return "other", "support", "normal"
