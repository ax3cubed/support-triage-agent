"""Account lookup. A stand-in for the product's database: reads the bundled data/accounts.json."""

import json
from functools import lru_cache

from triage.config import ACCOUNTS_FILE


@lru_cache(maxsize=1)
def _accounts() -> dict[str, dict]:
    records = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    return {record["email"].lower(): record for record in records}


def lookup(email: str) -> dict | None:
    """Return the account record for this email, or None when nobody has signed up with it."""
    return _accounts().get(email.strip().lower())
