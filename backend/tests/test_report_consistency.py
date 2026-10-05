"""Tests for Section 11: report/export consistency and the exact required
disclaimer wording."""
from __future__ import annotations

from fastapi.testclient import TestClient


def client():
    from app.main import app
    return TestClient(app)


REQUIRED_DISCLAIMER = "All findings are decision-support indicators and not a legal determination of fault or responsibility."


def test_markdown_report_contains_exact_disclaimer():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    inv_id = r.json()["id"]
    rep = c.get(f"/api/report/{inv_id}", params={"fmt": "markdown"})
    assert rep.status_code == 200
    assert REQUIRED_DISCLAIMER in rep.json()["markdown"]


def test_markdown_report_includes_new_explainable_evidence_fields():
    """SIH requirement G: report exports must include factor-level evidence -
    this covers the new distance-from-detection-location, data-completeness,
    and confidence-level fields (requirement D/E) actually reaching the
    report, not just the API response."""
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    inv_id = r.json()["id"]
    assert r.json()["candidates"], "demo investigation should produce at least one candidate"
    rep = c.get(f"/api/report/{inv_id}", params={"fmt": "markdown"}).json()["markdown"]
    assert "Distance to raw detection location" in rep
    assert "Data completeness" in rep
    assert "Confidence in this score" in rep


def test_pdf_report_generates_without_error():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    inv_id = r.json()["id"]
    rep = c.get(f"/api/report/{inv_id}", params={"fmt": "pdf"})
    assert rep.status_code == 200
    assert rep.headers["content-type"] == "application/pdf"
    assert len(rep.content) > 500  # a real, non-trivial PDF was produced


def test_geolocation_source_present_and_consistent_across_views():
    c = client()
    r = c.post("/api/spill/detect", params={"mode": "demo"})
    inv_id = r.json()["id"]
    inv = r.json()
    assert "geolocation_source" in inv["satellite"]

    rep = c.get(f"/api/report/{inv_id}", params={"fmt": "markdown"}).json()["markdown"]
    assert inv["satellite"]["geolocation_source"] in rep
