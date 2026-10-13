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
            q = by_question[request.split("New request: ")[1]]
            if q.get("expect") == "decline":
                return make_plan(action="decline", message="No access.", sql="")
            return make_plan(sql=q["expected_sql"], domain="finance" if q["gate"] == 3 else "inventory")

        def narrate(self, payload):
            return ""

    results = run_eval.run(planner=Oracle())
    assert all(ok == total for ok, total in results.values())
