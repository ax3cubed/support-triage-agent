"""Retrieval on its own: search with the raw ticket text and see whether the expected article comes back.

Needs no model or key. It is a floor, not the real number: the agent writes its own search queries,
which usually do better than the raw ticket. A miss here shows where retrieval depends on that rewrite.

    uv run python -m evals.retrieval
"""

from evals.dataset import load_cases
from triage import kb


def main() -> None:
    cases = [case for case in load_cases() if "docs" in case["expected"]]
    for k in (1, 4):
        misses = []
        for case in cases:
            query = f"{case['inputs']['subject']} {case['inputs']['body']}"
            found = {chunk.doc_id for chunk in kb.search(query, k=k)}
            if not found & set(case["expected"]["docs"]):
                misses.append(f"{case['id']} (wanted {case['expected']['docs']}, got {sorted(found)})")
        print(f"recall@{k}: {len(cases) - len(misses)}/{len(cases)}")
        for miss in misses:
            print(f"  miss {miss}")


if __name__ == "__main__":
    main()
