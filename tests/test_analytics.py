import pandas as pd

from reporting_assistant.analytics import compute_statistics


def test_totals_min_max_and_period_change():
    frame = pd.DataFrame({
        "month": ["2026-07", "2026-08", "2026-09"],
        "revenue": [100.0, 150.0, 120.0],
    })
    stats = compute_statistics(frame, ["revenue"], label_column="month", period_column="month")
    (m,) = stats["measures"]
    assert m["total"] == 370.0 and m["average"] == 123.33
    assert (m["minimum"], m["minimum_at"]) == (100.0, "2026-07")
    assert (m["maximum"], m["maximum_at"]) == (150.0, "2026-08")
    assert (m["latest_period"], m["previous_period"]) == ("2026-09", "2026-08")
    assert m["change"] == -30.0 and m["pct_change"] == -20.0


def test_period_change_aggregates_series_rows():
    frame = pd.DataFrame({"month": ["01", "01", "02", "02"], "site": list("ABAB"), "units": [1, 2, 3, 3]})
    (m,) = compute_statistics(frame, ["units"], period_column="month")["measures"]
    assert (m["previous"], m["latest"], m["pct_change"]) == (3.0, 6.0, 100.0)


def test_budget_variance():
    frame = pd.DataFrame({"department": ["IT", "Sales"], "actual_usd": [90.0, 120.0], "budget_usd": [100.0, 100.0]})
    stats = compute_statistics(frame, ["actual_usd", "budget_usd"], variance_pair=["actual_usd", "budget_usd"])
    assert stats["variance"] == {"actual": "actual_usd", "comparison": "budget_usd", "actual_total": 210.0,
                                 "comparison_total": 200.0, "variance": 10.0, "pct_variance": 5.0}


def test_ignores_missing_and_non_numeric_measures():
    frame = pd.DataFrame({"name": ["a"], "value": [1]})
    stats = compute_statistics(frame, ["name", "absent", "value"])
    assert [m["measure"] for m in stats["measures"]] == ["value"]
    assert compute_statistics(frame, [], period_column="x")["measures"] == []


def test_zero_base_has_no_percentage():
    frame = pd.DataFrame({"p": [1, 2], "v": [0.0, 5.0]})
    (m,) = compute_statistics(frame, ["v"], period_column="p")["measures"]
    assert m["pct_change"] is None and m["change"] == 5.0
