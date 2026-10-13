"""Gate test runner (proposal sections 8 and 13).

Asks the assistant every question in questions.yaml, runs the expert's
expected SQL as the same user, and checks that the numbers match. Prints
accuracy per gate against the targets (Gate 2: 95%, Gate 3: 98%) and exits
non-zero if a gate misses its target, so it can block a release.

    python -m eval.run_eval              # uses Claude (needs API credentials)
    python -m eval.run_eval --only fin-  # a subset by id prefix

Each run calls the Claude API once or twice per question.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import pandas as pd
import yaml

from reporting_assistant.assistant import Assistant
from reporting_assistant.auth import demo_user
from reporting_assistant.config import get_settings
from reporting_assistant.llm import ClaudePlanner, Planner
from reporting_assistant.query_service import QueryService
from reporting_assistant.semantic import load_semantic_layer
from reporting_assistant.store import AppStore

TARGETS = {2: 0.95, 3: 0.98}
QUESTIONS = Path(__file__).with_name("questions.yaml")


def _numeric_columns(frame: pd.DataFrame) -> list[list[float]]:
    return [sorted(round(float(v), 2) for v in frame[c].dropna())
            for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]


def numbers_match(expected: pd.DataFrame, actual: pd.DataFrame, tolerance: float = 0.011) -> bool:
    """Every numeric column of the expected result appears in the actual result
    with the same values (column names, order and extra columns don't matter)."""
    if len(expected) != len(actual):
        return False
    candidates = _numeric_columns(actual)
    for column in _numeric_columns(expected):
        if not any(len(c) == len(column) and all(abs(a - b) <= tolerance for a, b in zip(c, column)) for c in candidates):
            return False
    return True


def run(planner: Planner | None = None, only: str = "") -> dict[int, tuple[int, int]]:
    settings = get_settings()
    layer = load_semantic_layer(settings.semantic_layer_path)
    queries = QueryService(settings.reporting_db, layer, settings.query_timeout_seconds, settings.max_rows)
    assistant = Assistant(layer, queries, planner or ClaudePlanner(settings.model, settings.plan_effort, settings.narrative_effort),
                          AppStore(settings.data_dir / "eval.db"), settings.reporting_db)
    questions = [q for q in yaml.safe_load(QUESTIONS.read_text()) if q["id"].startswith(only)]

    results: dict[int, tuple[int, int]] = {}
    for q in questions:
        user = demo_user(layer, q["user"])
        reply = assistant.handle(user, uuid.uuid4().hex, q["question"])
        if q.get("expect") == "decline":
            passed, note = reply.kind == "decline", reply.kind
        elif reply.kind != "report":
            passed, note = False, f"{reply.kind}: {reply.message}"
        else:
            expected = queries.run(q["expected_sql"], user).frame
            actual = queries.run(reply.report["sql"], user).frame
            passed = numbers_match(expected, actual)
            note = "" if passed else f"expected {expected.to_dict('list')} got {actual.head(10).to_dict('list')}"
        ok, total = results.get(q["gate"], (0, 0))
        results[q["gate"]] = (ok + passed, total + 1)
        print(f"{'PASS' if passed else 'FAIL'}  gate {q['gate']}  {q['id']}  {note}")

    print()
    for gate, (ok, total) in sorted(results.items()):
        print(f"Gate {gate}: {ok}/{total} correct = {ok / total:.0%} (target {TARGETS[gate]:.0%})")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="Run questions whose id starts with this prefix")
    args = parser.parse_args()
    results = run(only=args.only)
    sys.exit(0 if all(ok / total >= TARGETS[g] for g, (ok, total) in results.items()) else 1)


if __name__ == "__main__":
    main()
