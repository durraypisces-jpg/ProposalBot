"""Statistics for a report (FR-05), calculated in Python from the query result.

The LLM never calculates numbers: it only says which columns are measures,
which column is the period and which pair is a variance comparison.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class MeasureStats:
    measure: str
    count: int
    total: float
    average: float
    minimum: float
    maximum: float
    minimum_at: str | None = None
    maximum_at: str | None = None
    latest_period: str | None = None
    latest: float | None = None
    previous_period: str | None = None
    previous: float | None = None
    change: float | None = None
    pct_change: float | None = None


@dataclass
class Variance:
    actual: str
    comparison: str
    actual_total: float
    comparison_total: float
    variance: float
    pct_variance: float | None


def _num(value) -> float:
    return float(round(float(value), 2))


def _pct(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 1)


def _label(frame: pd.DataFrame, index, label_column: str | None) -> str | None:
    if not label_column or label_column not in frame.columns:
        return None
    return str(frame.loc[index, label_column])


def compute_statistics(
    frame: pd.DataFrame,
    measures: list[str],
    label_column: str | None = None,
    period_column: str | None = None,
    variance_pair: list[str] | None = None,
) -> dict:
    measures = [m for m in measures if m in frame.columns and pd.api.types.is_numeric_dtype(frame[m])]
    stats: list[MeasureStats] = []
    for measure in measures:
        series = frame[measure].dropna()
        if series.empty:
            continue
        item = MeasureStats(
            measure=measure,
            count=int(series.count()),
            total=_num(series.sum()),
            average=_num(series.mean()),
            minimum=_num(series.min()),
            maximum=_num(series.max()),
            minimum_at=_label(frame, series.idxmin(), label_column),
            maximum_at=_label(frame, series.idxmax(), label_column),
        )
        if period_column and period_column in frame.columns:
            # Period-over-period: aggregate per period, compare the last two.
            by_period = frame.groupby(period_column, sort=True)[measure].sum()
            if len(by_period) >= 2:
                item.latest_period, item.previous_period = str(by_period.index[-1]), str(by_period.index[-2])
                item.latest, item.previous = _num(by_period.iloc[-1]), _num(by_period.iloc[-2])
                item.change = _num(item.latest - item.previous)
                item.pct_change = _pct(item.latest, item.previous)
        stats.append(item)

    variance = None
    if variance_pair and len(variance_pair) == 2 and all(c in measures for c in variance_pair):
        actual, comparison = variance_pair
        a, c = _num(frame[actual].sum()), _num(frame[comparison].sum())
        variance = Variance(actual, comparison, a, c, _num(a - c), _pct(a, c))

    return {
        "row_count": int(len(frame)),
        "measures": [asdict(s) for s in stats],
        "variance": asdict(variance) if variance else None,
    }
