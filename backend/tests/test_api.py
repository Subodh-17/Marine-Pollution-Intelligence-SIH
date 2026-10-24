"""
API-level regression tests (as opposed to the unit tests in the other
test_*.py files, which exercise pipeline modules directly). These hit the
actual FastAPI app through TestClient, covering every route the frontend
calls plus the edge cases found during hardening.
"""
import numpy as np
import pytest
import time
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.satellite.detection import DetectionResult

client = TestClient(app)


def _run_demo():
    r = client.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    return r.json()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_spill_detect_demo_returns_full_investigation():
    inv = _run_demo()
    assert inv["id"]
    assert inv["satellite"]["geometry"] is not None
    assert inv["drift"]["probable_origin"] is not None
    assert len(inv["candidates"]) >= 1
    assert inv["data_sources"]["ais"].endswith("SYNTHETIC_DEMO")


def test_get_spill_by_id():
    inv = _run_demo()
    r = client.get(f"/api/spill/{inv['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == inv["id"]


def test_get_spill_404_for_unknown_id():
    r = client.get("/api/spill/does-not-exist")
    assert r.status_code == 404


def test_geometry_hindcast_forecast_endpoints():
    inv = _run_demo()
    iid = inv["id"]
    g = client.get(f"/api/spill/{iid}/geometry")
    assert g.status_code == 200 and g.json()["area_km2"] > 0

    h = client.get(f"/api/spill/{iid}/hindcast")
    assert h.status_code == 200
    assert len(h.json()["backward_track"]) == 4

    f = client.get(f"/api/spill/{iid}/forecast")
    assert f.status_code == 200
    assert len(f.json()["forward_track"]) == 4


def test_ais_candidates_endpoint():
    inv = _run_demo()
    r = client.get("/api/ais/candidates", params={"investigation_id": inv["id"]})
    assert r.status_code == 200
    body = r.json()
    assert "ais_summary" in body and "candidates" in body


def test_ais_analyze_persists_to_stored_investigation():
    """Regression test: /api/ais/analyze previously computed and returned
    re-scored candidates but never wrote them back onto the stored
    Investigation, so GET /api/spill/{id} (and therefore the report and
    ranking endpoints) kept showing stale, pre-filter-change results."""
    inv = _run_demo()
    iid = inv["id"]
    original_score = inv["candidates"][0]["final_score"]

    r = client.post("/api/ais/analyze", params={
        "investigation_id": iid, "search_radius_km": 150, "time_window_hours": 25,
    })
    assert r.status_code == 200
    new_score = r.json()["candidates"][0]["final_score"]
    assert new_score != original_score, "widening radius/window should change the top score in this scenario"

    refetched = client.get(f"/api/spill/{iid}").json()
    assert refetched["candidates"][0]["final_score"] == new_score, "re-analysis must persist onto the stored investigation"
    assert refetched["ais_summary"]["search_radius_km"] == 150.0


def test_ais_upload_and_custom_mode():
    csv_content = (
        "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,VesselType,IMO\n"
        "444555666,2026-08-27T00:00:00,19.05,71.20,12.0,90,90,MV API TEST,Tanker,9444555\n"
    )
    files = {"file": ("test.csv", csv_content, "text/csv")}
    up = client.post("/api/ais/upload", files=files)
    assert up.status_code == 200

    inv = client.post("/api/spill/detect", params={"mode": "custom_ais"}).json()
    assert inv["mode"] == "custom_ais"
    assert inv["ais_summary"]["ais_source"] == "USER_UPLOADED_CSV"


def test_vessel_ranking_and_detail():
    inv = _run_demo()
    iid = inv["id"]
    ranking = client.get("/api/vessels/ranking", params={"investigation_id": iid})
    assert ranking.status_code == 200
    assert len(ranking.json()) == len(inv["candidates"])

    mmsi = inv["candidates"][0]["mmsi"]
    detail = client.get(f"/api/vessels/{mmsi}", params={"investigation_id": iid})
    assert detail.status_code == 200
    assert detail.json()["mmsi"] == mmsi

    missing = client.get("/api/vessels/000000000", params={"investigation_id": iid})
    assert missing.status_code == 404


def test_attribution_analyze_actually_uses_custom_weights():
    """Regression test: /api/attribution/analyze accepted a `weights` body
    but silently ignored it and just echoed back the existing candidates,
    computed with the default weights, regardless of what was passed in."""
    inv = _run_demo()
    iid = inv["id"]
    default_score = inv["candidates"][0]["final_score"]

    # heavily weight only behavioural anomalies — should change the ranking/score.
    # Every known component must be listed (and zeroed) here, or the two newer
    # components (speed_consistency, data_completeness — see SIH requirement D)
    # would merge in at their nonzero defaults and dilute the "100% on one
    # component" intent of this test.
    skewed = {
        "spatial_proximity": 0, "temporal_correlation": 0, "trajectory_consistency": 0,
        "heading_consistency": 0, "speed_consistency": 0, "behavioural_anomalies": 100,
        "ais_continuity": 0, "data_completeness": 0,
    }
    r = client.post(f"/api/attribution/analyze?investigation_id={iid}", json=skewed)
    assert r.status_code == 200
    body = r.json()
    assert body["weights_used"]["behavioural_anomalies"] == pytest.approx(100.0)
    new_top_score = body["candidates"][0]["final_score"]
    assert new_top_score != default_score, "custom weights must actually change the computed score"

    # invalid component name should be rejected, not silently ignored
    bad = client.post(f"/api/attribution/analyze?investigation_id={iid}", json={"not_a_real_component": 50})
    assert bad.status_code == 400

    # negative weight should be rejected
    neg = client.post(f"/api/attribution/analyze?investigation_id={iid}", json={"spatial_proximity": -5})
    assert neg.status_code == 400


def test_new_explainable_factors_present_end_to_end():
    """SIH requirement D/E: distance-from-detection-location, speed
    consistency, data completeness, confidence level, and missing-data
    warnings must reach every candidate returned by the main detect
    endpoint AND both re-scoring endpoints (/api/ais/analyze,
    /api/attribution/analyze) - not just the initial response."""
    inv = _run_demo()
    iid = inv["id"]
    assert inv["candidates"], "demo investigation should produce at least one candidate"
    for c in inv["candidates"]:
        assert "distance_from_detection_km" in c
        assert "confidence_level" in c and c["confidence_level"] in ("low", "medium", "high")
        assert "data_completeness_pct" in c and 0.0 <= c["data_completeness_pct"] <= 100.0
        assert "data_warnings" in c and isinstance(c["data_warnings"], list)
        component_names = {b["component"] for b in c["breakdown"]}
        assert {"speed_consistency", "data_completeness"} <= component_names

    ais_r = client.post(f"/api/ais/analyze?investigation_id={iid}")
    assert ais_r.status_code == 200
    for c in ais_r.json()["candidates"]:
        assert "distance_from_detection_km" in c
        assert "confidence_level" in c

    attr_r = client.post(f"/api/attribution/analyze?investigation_id={iid}", json={"spatial_proximity": 50})
    assert attr_r.status_code == 200
    for c in attr_r.json()["candidates"]:
        assert "distance_from_detection_km" in c
        assert "confidence_level" in c


def test_report_markdown_and_pdf():
    inv = _run_demo()
    iid = inv["id"]
    md = client.get(f"/api/report/{iid}", params={"fmt": "markdown"})
    assert md.status_code == 200
    text = md.json()["markdown"]
    # Report title must use the scientifically-cautious platform name, never
    # the legacy "Oil Spill Investigation Report" title (see Section 3 of
    # the submission-hardening brief).
    assert "MARINE POLLUTION INTELLIGENCE INVESTIGATION REPORT" in text
    assert "OIL SPILL INVESTIGATION REPORT" not in text
    for section in ["Incident Overview", "Slick Characterisation", "Hindcast Analysis",
                     "Vessel Ranking", "Attribution Confidence", "Limitations"]:
        assert section in text

    pdf = client.get(f"/api/report/{iid}", params={"fmt": "pdf"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


def test_no_detection_scenario_returns_200_not_500():
    """Regression test: a scene with no detected slick previously crashed
    the whole /api/spill/detect call with an unhandled AttributeError."""
    def fake_detect(*a, **kw):
        return DetectionResult(mask=np.zeros((512, 512), dtype=bool), candidate_masks=[],
                                confidence=0.0, notes=["No plausible oil-slick-like dark region found."])

    with patch("app.investigation.orchestrator.detect_oil_slick", fake_detect):
        r = client.post("/api/spill/detect", params={"mode": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["satellite"]["geometry"] is None
    assert body["candidates"] == []

    # AIS analysis on a no-detection investigation should fail cleanly
    # (409, "nothing to search around"), not throw a 500
    r2 = client.post("/api/ais/analyze", params={"investigation_id": body["id"]})
    assert r2.status_code == 409

    # report generation must still succeed and read honestly
    rpt = client.get(f"/api/report/{body['id']}", params={"fmt": "markdown"})
    assert rpt.status_code == 200
    assert "No oil-slick-like feature was detected" in rpt.json()["markdown"]


def test_cors_headers_present():
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_custom_ais_mode_without_prior_upload_returns_400_not_silent_demo_fallback():
    """Regression test: requesting mode=custom_ais with no AIS file ever
    uploaded previously fell through to the synthetic demo fleet silently,
    misrepresenting synthetic data as the user's own upload. Must now fail
    clearly instead.

    _LAST_UPLOADED_AIS is process-global state that other tests in this
    file legitimately populate, so it must be explicitly cleared here rather
    than relying on test execution order to leave it empty.
    """
    import app.main as main_module
    main_module._LAST_UPLOADED_AIS.clear()
    r = client.post("/api/spill/detect", params={"mode": "custom_ais"})
    assert r.status_code == 400
    assert "upload" in r.json()["detail"].lower()


def test_malformed_ais_csv_returns_400_not_500():
    """Regression test: an uploaded AIS CSV missing required columns
    (mmsi/timestamp/lat/lon) previously raised an unhandled ValueError,
    surfacing as a raw 500/traceback instead of an understandable error."""
    r = client.post("/api/ais/upload", files={"file": ("bad.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 200
    r2 = client.post("/api/spill/detect", params={"mode": "custom_ais"})
    assert r2.status_code == 400
    assert "missing required columns" in r2.json()["detail"]


def test_malformed_ais_csv_error_does_not_leak_server_temp_path():
    """Regression test: the 400 above must not expose the server's
    internal randomly-named temp file path (e.g. `/tmp/tmpXXXXXXXX.csv`)
    to the client - that's an internal implementation detail, never the
    name the user actually uploaded, and leaking it serves no purpose for
    the caller. The message should still surface the columns actually
    found in the file, which does help the user spot their own typo. Also
    checked via the async job path, since job.error stores str(e) directly
    with no per-endpoint chance to sanitize it (see app/persistence/jobs.py)."""
    r = client.post("/api/ais/upload", files={"file": ("bad.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert r.status_code == 200
    r2 = client.post("/api/spill/detect", params={"mode": "custom_ais"})
    assert r2.status_code == 400
    detail = r2.json()["detail"]
    assert "/tmp/" not in detail
    assert "foo" in detail and "bar" in detail

    r3 = client.post("/api/jobs/investigation", params={"mode": "custom_ais"})
    assert r3.status_code == 200
    job_id = r3.json()["id"]
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "failed"
    assert "/tmp/" not in job["error"]


def test_empty_ais_csv_returns_400_not_500():
    r = client.post("/api/ais/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert r.status_code == 200
    r2 = client.post("/api/spill/detect", params={"mode": "custom_ais"})
    assert r2.status_code == 400
