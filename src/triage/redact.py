"""Remove payment details from a ticket before the model, the trace or a reply can see them.

Deterministic on purpose: this must hold even when the model misbehaves.
"""

import re

_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_CVV = re.compile(r"\b(cvv|cvc|security code)\b(\s*(?:is|:|=)?\s*)\d{3,4}\b", re.IGNORECASE)
# UK bank details: an 8-digit account number and a 6-digit sort code.
_BANK_ACCOUNT = re.compile(r"\b(account|acct)(\s*(?:number|no\.?|#)?\s*(?:is|:)?\s*)\d{8}\b", re.IGNORECASE)
_SORT_CODE = re.compile(r"\b(sort code)(\s*(?:is|:)?\s*)\d{2}[- ]?\d{2}[- ]?\d{2}\b", re.IGNORECASE)


def _passes_luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def find_card_numbers(text: str) -> list[str]:
    """Return digit runs that look like a card number and pass the Luhn check."""
    found = []
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _passes_luhn(digits):
            found.append(match.group())
    return found


def redact(text: str) -> tuple[str, int]:
    """Return the text with card numbers, CVVs, bank account numbers and sort codes masked, and how many were masked."""
    count = 0
    for card in find_card_numbers(text):
        text = text.replace(card, "[CARD NUMBER REMOVED]")
        count += 1
    text, cvvs = _CVV.subn(r"\1\2[CVV REMOVED]", text)
    text, accounts = _BANK_ACCOUNT.subn(r"\1\2[ACCOUNT NUMBER REMOVED]", text)
    text, sort_codes = _SORT_CODE.subn(r"\1\2[SORT CODE REMOVED]", text)
    return text, count + cvvs + accounts + sort_codes
