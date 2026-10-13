"""Loads the semantic layer and answers questions about it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class View:
    name: str
    domain: str
    source: str
    description: str
    columns: dict[str, str]
    row_filter: str | None = None
    consolidated: bool = False


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    domains: tuple[str, ...]
    row_filters: dict[str, str] = field(default_factory=dict)
    consolidated: bool = False
    can_administer: bool = False


@dataclass(frozen=True)
class SemanticLayer:
    reporting_currency: str
    fiscal_start_month: int
    fiscal_notes: tuple[str, ...]
    business_rules: tuple[str, ...]
    views: dict[str, View]
    roles: dict[str, Role]
    demo_users: tuple[dict, ...]
    templates: tuple[dict, ...]

    def fiscal_period_of(self, day: date) -> tuple[int, int]:
        """Return (fiscal_year, fiscal_period) for a calendar date (FR-15)."""
        offset = (day.month - self.fiscal_start_month) % 12
        fiscal_year = day.year + (1 if day.month >= self.fiscal_start_month and self.fiscal_start_month != 1 else 0)
        return fiscal_year, offset + 1

    def views_for(self, role: Role) -> list[View]:
        visible = []
        for view in self.views.values():
            if view.domain != "shared" and view.domain not in role.domains:
                continue
            if view.consolidated and not role.consolidated:
                continue
            visible.append(view)
        return visible

    def describe_for(self, role: Role) -> str:
        """The schema and rules the LLM sees: only views this role may query."""
        lines = [f"Reporting currency: {self.reporting_currency}", "", "Fiscal calendar:"]
        lines += [f"- {note}" for note in self.fiscal_notes]
        lines += ["", "Business rules:"]
        lines += [f"- {rule}" for rule in self.business_rules]
        lines += ["", "Views you may query (SQLite dialect):"]
        for view in self.views_for(role):
            lines.append(f"\n{view.name}  -- {view.description}")
            for column, meaning in view.columns.items():
                lines.append(f"  {column}: {meaning}")
        hidden = sorted(set(self.views) - {v.name for v in self.views_for(role)})
        if hidden:
            lines += ["", "Views that exist but this user may NOT see: " + ", ".join(hidden)]
        return "\n".join(lines)


def _parse(raw: dict) -> SemanticLayer:
    calendar = raw["fiscal_calendar"]
    views = {
        name: View(
            name=name,
            domain=spec["domain"],
            source=spec["source"],
            description=spec["description"],
            columns=dict(spec["columns"]),
            row_filter=spec.get("row_filter"),
            consolidated=bool(spec.get("consolidated", False)),
        )
        for name, spec in raw["views"].items()
    }
    roles = {
        name: Role(
            name=name,
            label=spec["label"],
            domains=tuple(spec.get("domains", [])),
            row_filters=dict(spec.get("row_filters", {})),
            consolidated=bool(spec.get("consolidated", False)),
            can_administer=bool(spec.get("can_administer", False)),
        )
        for name, spec in raw["roles"].items()
    }
    return SemanticLayer(
        reporting_currency=raw["reporting_currency"],
        fiscal_start_month=int(calendar["start_month"]),
        fiscal_notes=(calendar["naming"], calendar["periods"]),
        business_rules=tuple(raw["business_rules"]),
        views=views,
        roles=roles,
        demo_users=tuple(raw.get("demo_users", [])),
        templates=tuple(raw.get("templates", [])),
    )


@lru_cache(maxsize=4)
def load_semantic_layer(path: Path) -> SemanticLayer:
    with open(path, encoding="utf-8") as fh:
        return _parse(yaml.safe_load(fh))
