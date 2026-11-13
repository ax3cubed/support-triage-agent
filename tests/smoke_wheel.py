"""Run against a clean install of the built wheel (`make smoke`): the package must carry its own data."""

from triage import accounts, kb
from triage.agent import LATEST, initial_state
from triage.cli import main  # noqa: F401 - the console script's entry point must import

assert kb.search("turn on automatic reminders"), "the help centre is missing from the wheel"
assert accounts.lookup("alice@brightbakery.example"), "the accounts file is missing from the wheel"
assert initial_state({"from_email": "a@b.example", "subject": "s", "body": "b"}, LATEST)["ticket"]["subject"] == "s"
print("wheel ok")
