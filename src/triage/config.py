"""Paths and model settings, read once from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "data" / "kb"
ACCOUNTS_FILE = ROOT / "data" / "accounts.json"

AGENT_MODEL = os.environ.get("TRIAGE_MODEL", "anthropic:claude-sonnet-5-5")
JUDGE_MODEL = os.environ.get("TRIAGE_JUDGE_MODEL", "anthropic:claude-opus-5-5")

# How many times the model may be called for one ticket before the agent gives up and hands over.
MAX_MODEL_CALLS = 6
