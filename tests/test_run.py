from conftest import ScriptedModel
from langsmith import evaluate

from evals.dataset import load_cases, local_examples
from evals.evaluators import deterministic_checks
from evals.run import collect_rows, summarise
from triage.agent import triage


def test_a_local_run_scores_each_case_and_summarises_by_slice():
    cases = [case for case in load_cases() if case["id"] in ("n01", "e01")]

    def always_replies(inputs: dict) -> dict:
        model = ScriptedModel(
            [("search_help_center", {"query": "automatic reminders"})],
            [("send_reply", {"category": "product", "body": "Settings › Reminders.", "cited_doc_ids": ["reminders"]})],
        )
        return triage(inputs, model=model)

    experiment = evaluate(always_replies, data=local_examples(cases), evaluators=[deterministic_checks], upload_results=False)
    rows = collect_rows(experiment)

    assert [row["case_id"] for row in rows] == ["e01", "n01"]
    assert rows[0]["scores"]["safe_action"] == 0  # replied to a refund request
    assert rows[1]["scores"]["action_correct"] == 1
    assert summarise(rows)["all"]["safe_action"] == {"mean": 0.5, "n": 2}
