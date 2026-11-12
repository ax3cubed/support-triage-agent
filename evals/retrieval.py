"""Retrieval on its own: search with the raw ticket text and see whether the section with the answer comes back.

Needs no model or key. It is a floor, not the real number: the agent writes its own search queries,
which usually do better than the raw ticket. A miss here shows where retrieval depends on that rewrite.

    uv run python -m evals.retrieval
"""

from evals.dataset import load_cases
from triage import kb


def main() -> None:
    cases = [case for case in load_cases() if "sections" in case["expected"]]
    for roots in (False, True):
        print(f"\n{'matching word roots (v3)' if roots else 'matching exact words (v0 to v2)'}")
        for k in (1, 4):
            misses = []
            for case in cases:
                query = f"{case['inputs']['subject']} {case['inputs']['body']}"
                found = {f"{chunk.doc_id}#{chunk.section}" for chunk in kb.search(query, k=k, roots=roots)}
                if not found & set(case["expected"]["sections"]):
                    misses.append(case["id"])
            print(f"  right section in top {k}: {len(cases) - len(misses)}/{len(cases)}   missed: {', '.join(misses) or 'none'}")


if __name__ == "__main__":
    main()
