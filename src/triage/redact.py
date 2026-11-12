"""Remove payment details from a ticket before the model, the trace or a reply can see them.

Deterministic on purpose: this must hold even when the model misbehaves.
"""

import re
import unicodedata

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


# --- Strict guard, used from agent v3 ----------------------------------------------------------
#
# The guard above reads the longest run of digits as one number, so a card followed by its security
# code ("4242 4242 4242 4242 123") fails the Luhn check as a whole and is left alone. The strict
# guard looks at the groups of digits inside a run and masks any stretch of them that is a card.

_SEPARATORS = r" \t\r\n .\-/_"
_DIGIT_RUN = re.compile(rf"\d+(?:[{_SEPARATORS}]+\d+)*")
_CARD_WORD = re.compile(r"\bcard\b", re.IGNORECASE)
_CVV_STRICT = re.compile(
    r"\b(cvv2?|cvc2?|security (?:code|number)|(?:three|3|four|4) digits on the back)\b"
    r"((?:\s*(?:code|number))?\s*(?:is|are|:|=|-)?\s*)\d{3,4}\b",
    re.IGNORECASE,
)
_EXPIRY = re.compile(r"\b(exp(?:iry|ires|iration)?\.?(?: date)?)(\s*(?:is|:|=|-)?\s*)\d{1,2}\s*/\s*\d{2,4}\b", re.IGNORECASE)
_CODE_AFTER_CARD = re.compile(r"(\[CARD NUMBER REMOVED\])([ \t\r\n,;:\-]*)\d{3,4}\b(?![/\d])")
_CARD_LENGTHS = range(13, 20)


def _card_spans(text: str) -> list[tuple[int, int]]:
    """Start and end of every stretch of text that is a card number, however it is spaced."""
    spans = []
    for run in _DIGIT_RUN.finditer(text):
        groups = [(m.start() + run.start(), m.end() + run.start()) for m in re.finditer(r"\d+", run.group())]
        said_card = bool(_CARD_WORD.search(text[max(0, run.start() - 30) : run.start()]))
        first = 0
        while first < len(groups):
            matched = None
            for last in range(len(groups) - 1, first - 1, -1):
                parts = [text[start:end] for start, end in groups[first : last + 1]]
                digits = "".join(parts)
                # Groups of one or two digits are dates and amounts, not pieces of a card.
                well_formed = len(parts) == 1 or all(len(part) >= 3 for part in parts)
                if len(digits) in _CARD_LENGTHS and well_formed and (_passes_luhn(digits) or said_card):
                    matched = last
                    break
            if matched is None:
                # A card typed straight into other digits: look for one inside a single long group.
                start, end = groups[first]
                digits = text[start:end]
                if len(digits) > 16 and any(
                    _passes_luhn(digits[i : i + size]) for size in (15, 16) for i in range(len(digits) - size + 1)
                ):
                    spans.append((start, end))
                first += 1
            else:
                spans.append((groups[first][0], groups[matched][1]))
                first = matched + 1
    return spans


def find_card_numbers_strict(text: str) -> list[str]:
    """Every card number in the text, in any spacing the strict guard understands."""
    text = unicodedata.normalize("NFKC", text)
    return [text[start:end] for start, end in _card_spans(text)]


def redact_strict(text: str) -> tuple[str, int]:
    """Mask card numbers in any common format, security codes, expiry dates and UK bank details.

    Returns the cleaned text and how many things were masked. Known limits: a card split into groups
    of one or two digits, a wrong digit with no mention of "card" nearby, and secrets described only
    in words. No pattern list is complete.
    """
    text = unicodedata.normalize("NFKC", text)
    spans = _card_spans(text)
    for start, end in reversed(spans):
        text = text[:start] + "[CARD NUMBER REMOVED]" + text[end:]
    count = len(spans)
    # A short number straight after a card is its security code, even with no word saying so.
    text, trailing = _CODE_AFTER_CARD.subn(r"\1\2[CVV REMOVED]", text)
    count += trailing
    for pattern, label in (
        (_CVV_STRICT, "[CVV REMOVED]"),
        (_EXPIRY, "[EXPIRY REMOVED]"),
        (_BANK_ACCOUNT, "[ACCOUNT NUMBER REMOVED]"),
        (_SORT_CODE, "[SORT CODE REMOVED]"),
    ):
        text, found = pattern.subn(rf"\1\2{label}", text)
        count += found
    return text, count
