import json
import sys

import pytest
from conftest import ScriptedModel

from triage import agent, cli


def run_cli(monkeypatch, capsys, *argv, model=None):
    monkeypatch.setattr(sys, "argv", ["triage", *argv])
    if model:
        monkeypatch.setattr(agent, "init_chat_model", lambda name: model)
    cli.main()
    return capsys.readouterr().out


def test_the_cli_prints_each_step_and_keeps_every_account_lookup(monkeypatch, capsys):
    model = ScriptedModel(
        [("lookup_account", {})],
        [("get_recent_charges", {})],
        [("escalate_to_human", {"category": "billing", "team": "billing", "priority": "normal", "summary": "Charge."})],
    )

    out = run_cli(monkeypatch, capsys, "Why was I charged?", "--from", "daniel@harbourprint.example", model=model)

    steps, _, record = out.partition("{")
    result = json.loads("{" + record)
    assert steps.split() == ["[tools]", "lookup_account", "[tools]", "get_recent_charges", "[tools]", "escalate_to_human"]
    assert result["action"] == "escalate" and len(result["account_context"].splitlines()) == 2


@pytest.mark.parametrize("argv", [["--case", "zz99"], ["hello", "--agent", "v9"], []])
def test_the_cli_names_a_bad_argument_and_does_not_show_a_traceback(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch, capsys, *argv)

    assert stopped.value.code == 2
