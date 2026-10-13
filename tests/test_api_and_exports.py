import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from reporting_assistant.app import create_app
from reporting_assistant.exporters import render_chart_png

from .conftest import FakePlanner


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings, planner=FakePlanner()))


def login(client, username):
    token = client.post("/api/login", json={"username": username}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_requires_sign_in(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer admin.forged"}).status_code == 401


def test_templates_follow_role(client):
    omar = client.get("/api/templates", headers=login(client, "omar")).json()
    assert omar and all(t["domain"] == "inventory" for t in omar)


def test_chat_and_exports(client):
    headers = login(client, "omar")
    reply = client.post("/api/chat", json={"message": "Stock value by site"}, headers=headers).json()
    assert reply["kind"] == "report" and reply["session_id"]
    report_id = reply["report"]["id"]

    pdf = client.get(f"/api/reports/{report_id}/export/pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    for fmt, member in (("docx", "word/document.xml"), ("xlsx", "xl/workbook.xml")):
        response = client.get(f"/api/reports/{report_id}/export/{fmt}", headers=headers)
        assert response.status_code == 200
        assert member in zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert client.get(f"/api/reports/{report_id}/export/csv", headers=headers).status_code == 400
    chart = client.get(f"/api/reports/{report_id}/chart.png", headers=headers)
    assert chart.status_code == 200 and chart.content.startswith(b"\x89PNG")


def test_reports_are_private_to_their_owner(client):
    report_id = client.post("/api/chat", json={"message": "x"}, headers=login(client, "omar")).json()["report"]["id"]
    assert client.get(f"/api/reports/{report_id}", headers=login(client, "amina")).status_code == 404
    assert client.get(f"/api/reports/{report_id}", headers=login(client, "admin")).status_code == 200


def test_admin_only_endpoints(client):
    assert client.get("/api/admin/audit", headers=login(client, "omar")).status_code == 403
    client.post("/api/chat", json={"message": "Stock value by site"}, headers=login(client, "omar"))
    entries = client.get("/api/admin/audit", headers=login(client, "admin")).json()
    assert entries[0]["username"] == "omar" and entries[0]["outcome"] == "report"


def test_favourites(client):
    headers = login(client, "omar")
    favourite_id = client.post("/api/favourites", json={"name": "Stock", "request": "Stock value by site"}, headers=headers).json()["id"]
    assert [f["name"] for f in client.get("/api/favourites", headers=headers).json()] == ["Stock"]
    assert client.get("/api/favourites", headers=login(client, "amina")).json() == []
    client.delete(f"/api/favourites/{favourite_id}", headers=headers)
    assert client.get("/api/favourites", headers=headers).json() == []


@pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
def test_chart_rendering(chart_type):
    report = {"title": "T", "columns": ["site", "value"], "rows": [["A", 1.0], ["B", 2.0]],
              "chart": {"type": chart_type, "x": "site", "y": ["value"], "series": ""}}
    assert render_chart_png(report).startswith(b"\x89PNG")


def test_series_chart_and_table_type():
    report = {"title": "T", "columns": ["month", "site", "units"],
              "rows": [["01", "A", 1], ["01", "B", 2], ["02", "A", 3], ["02", "B", 4]],
              "chart": {"type": "line", "x": "month", "y": ["units"], "series": "site"}}
    assert render_chart_png(report).startswith(b"\x89PNG")
    report["chart"]["type"] = "table"
    assert render_chart_png(report) is None


def test_static_portal_is_served(client):
    assert "AI Reporting Assistant" in client.get("/").text
