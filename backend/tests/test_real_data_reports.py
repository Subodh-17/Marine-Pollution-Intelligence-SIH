"""
Tests for the real-data-integration report additions:
- the new `fmt=csv` report format (Part H)
- model provenance / dataset DOI / evaluation status appearing in reports
  whenever the ML path actually ran (Part E/F/G/H)
- the AIS-ranking-is-probabilistic disclaimer appearing in every report
"""
import csv
import io

from fastapi.testclient import TestClient


def client():
    from app.main import app
    return TestClient(app)


class TestCsvReportFormat:
    def test_csv_report_returns_200_and_correct_content_type(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "csv"})
        assert rep.status_code == 200
        assert "text/csv" in rep.headers["content-type"]

    def test_csv_report_is_parseable_and_has_required_fields(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "csv"})
        rows = dict(csv.reader(io.StringIO(rep.text)))
        for required_field in (
            "investigation_id", "detector_provenance", "processing_method", "confidence_pct",
            "model_name", "is_real_data_trained", "dataset_used_for_training", "dataset_dois",
            "evaluation_status", "model_limitations", "ais_ranking_disclaimer",
        ):
            assert required_field in rows, f"missing field {required_field!r} in CSV report"

    def test_csv_report_ais_disclaimer_is_probabilistic_language(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "csv"})
        rows = dict(csv.reader(io.StringIO(rep.text)))
        assert "probabilistic" in rows["ais_ranking_disclaimer"].lower()

    def test_invalid_fmt_rejected(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "xml"})
        assert rep.status_code == 422


class TestReportModelProvenanceContent:
    def test_markdown_report_has_ais_probabilistic_disclaimer(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "markdown"}).json()["markdown"]
        assert "probabilistic investigative assistance" in rep

    def test_markdown_report_states_processing_method(self):
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        inv_id = r.json()["id"]
        rep = c.get(f"/api/report/{inv_id}", params={"fmt": "markdown"}).json()["markdown"]
        assert "Processing method:" in rep

    def test_json_investigation_includes_model_provenance_key(self):
        """Even when None (classical-only path), the key exists so API
        consumers don't need to guess whether it's ever populated."""
        c = client()
        r = c.post("/api/spill/detect", params={"mode": "demo"})
        body = r.json()
        assert "model_provenance_info" in body["satellite"]
