"""Load the evaluation cases and keep the LangSmith copy of the dataset in step with the file."""

import json
import uuid
from datetime import UTC, datetime

from langsmith import Client
from langsmith.schemas import Example

from triage.config import ROOT

DATASET_FILE = ROOT / "data" / "eval" / "triage.jsonl"
DATASET_NAME = "support-triage"


def load_cases() -> list[dict]:
    """Read every case from the JSONL file."""
    return [json.loads(line) for line in DATASET_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def as_examples(cases: list[dict]) -> list[dict]:
    """Shape cases the way LangSmith stores them: inputs, reference outputs and metadata for slicing."""
    return [
        {
            "inputs": case["inputs"],
            "outputs": case["expected"],
            "metadata": {"case_id": case["id"], "slice": case["slice"], "note": case["note"]},
        }
        for case in cases
    ]


def local_examples(cases: list[dict]) -> list[Example]:
    """The same examples as in-memory objects, for a run that uploads nothing."""
    dataset_id = uuid.uuid4()
    return [
        Example(id=uuid.uuid4(), dataset_id=dataset_id, created_at=datetime.now(UTC), **example) for example in as_examples(cases)
    ]


def sync_to_langsmith(client: Client, cases: list[dict]) -> str:
    """Make the LangSmith dataset match the file: add new cases, update changed ones, remove deleted ones."""
    if not client.has_dataset(dataset_name=DATASET_NAME):
        client.create_dataset(DATASET_NAME, description="Support tickets for Ledgerly with the expected triage outcome.")
    existing = {example.metadata["case_id"]: example for example in client.list_examples(dataset_name=DATASET_NAME)}
    wanted = {example["metadata"]["case_id"]: example for example in as_examples(cases)}

    new = [example for case_id, example in wanted.items() if case_id not in existing]
    if new:
        client.create_examples(dataset_name=DATASET_NAME, examples=new)
    for case_id, example in wanted.items():
        current = existing.get(case_id)
        if current and (current.inputs, current.outputs) != (example["inputs"], example["outputs"]):
            client.update_example(current.id, inputs=example["inputs"], outputs=example["outputs"], metadata=example["metadata"])
    removed = [existing[case_id].id for case_id in existing if case_id not in wanted]
    if removed:
        client.delete_examples(example_ids=removed)
    return DATASET_NAME
