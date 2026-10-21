import pandas as pd
import pytest
import yaml

from eval import run_eval
from reporting_assistant.auth import demo_user
from reporting_assistant.query_service import QueryService

from .conftest import make_plan

QUESTIONS = yaml.safe_load(run_eval.QUESTIONS.read_text())


@pytest.mark.parametrize("q", [q for q in QUESTIONS if "expected_sql" in q], ids=lambda q: q["id"])
def test_expected_sql_runs_for_its_user(settings, layer, q):
    frame = QueryService(settings.reporting_db, layer).run(q["expected_sql"], demo_user(layer, q["user"])).frame
    assert not frame.empty


def test_numbers_match_ignores_names_and_order():
    expected = pd.DataFrame({"site": ["A", "B"], "value": [1.0, 2.0]})
    assert run_eval.numbers_match(expected, pd.DataFrame({"v": [2.004, 1.0], "s": ["B", "A"]}))
    assert not run_eval.numbers_match(expected, pd.DataFrame({"v": [2.5, 1.0]}))
    assert not run_eval.numbers_match(expected, pd.DataFrame({"v": [1.0]}))


def test_runner_scores_a_perfect_planner(settings, monkeypatch):
    monkeypatch.setenv("RA_DATA_DIR", str(settings.data_dir))
    by_question = {q["question"]: q for q in QUESTIONS}

    class Oracle:
        def plan(self, context, request):
            q = by_question.get(request.split("New request: ")[1])
            if q is None or q.get("expect") == "decline":  # red-team prompts and restricted requests
                return make_plan(action="decline", message="No access.", sql="")
            return make_plan(sql=q["expected_sql"], domain=q["module"])

        def narrate(self, payload):
            return ""

    results = run_eval.run(planner=Oracle())
    assert all(s["passed"] == s["total"] for s in results["modules"].values())
    assert results["redteam"] and all(r["passed"] for r in results["redteam"])
    assert run_eval.gate_passed(results)


def test_one_wrong_answer_fails_the_module(settings, monkeypatch):
    monkeypatch.setenv("RA_DATA_DIR", str(settings.data_dir))
    by_question = {q["question"]: q for q in QUESTIONS}

    class OneWrong:
        def plan(self, context, request):
            q = by_question.get(request.split("New request: ")[1])
            if q is None or q.get("expect") == "decline":
                return make_plan(action="decline", message="No access.", sql="")
            if q["id"] == "inv-reorder-count":
                return make_plan(sql="SELECT COUNT(*) + 1 AS items FROM products")
            return make_plan(sql=q["expected_sql"], domain=q["module"])

        def narrate(self, payload):
            return ""

    results = run_eval.run(planner=OneWrong(), redteam=False)
    inventory = results["modules"]["inventory"]
    assert inventory["passed"] == inventory["total"] - 1
    assert not run_eval.gate_passed(results)


def test_compare_rows_not_just_columns():
    expected = pd.DataFrame({"site": ["A", "B"], "value": [1.0, 2.0]})
    # Same values per column, but paired with the wrong site.
    swapped = pd.DataFrame({"site": ["A", "B"], "value": [2.0, 1.0]})
    assert not run_eval.compare(expected, swapped)[0]
    # Column and row order are ignored; extra columns are listed for review.
    passed, note = run_eval.compare(expected, pd.DataFrame({"value": [2.0, 1.0], "site": ["B", "A"], "n": [5, 6]}))
    assert passed and "extra columns" in note
    # Row order counts only when the question asks for a sort.
    reordered = pd.DataFrame({"site": ["B", "A"], "value": [2.0, 1.0]})
    assert run_eval.compare(expected, reordered)[0]
    assert not run_eval.compare(expected, reordered, ordered=True)[0]
    # Money is compared to the cent.
    assert not run_eval.compare(expected, pd.DataFrame({"site": ["A", "B"], "value": [1.0, 2.02]}))[0]


def test_redteam_sql_cases_are_all_refused(settings, layer):
    queries = QueryService(settings.reporting_db, layer)
    results = run_eval.run_redteam(layer, queries, assistant=None, with_llm=False)
    assert len(results) >= 20
    assert all(r["passed"] for r in results), [r for r in results if not r["passed"]]
