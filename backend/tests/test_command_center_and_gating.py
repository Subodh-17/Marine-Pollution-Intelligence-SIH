"""
Tests for Command Center endpoint and API-level attribution safety gate enforcement.
"""
from fastapi.testclient import TestClient
from app.main import app
from app.investigation.orchestrator import _STORE, Investigation
from datetime import datetime, timezone


def test_command_center_endpoint():
    client = TestClient(app)
    resp = client.get("/api/command-center")
    assert resp.status_code == 200
    data = resp.json()
    assert "incidents" in data
    assert "jobs" in data
    assert "model_readiness" in data
    assert "data_sources" in data
    assert "recent_acquisitions" in data
    assert "review_queue" in data

    incidents = data["incidents"]
    assert "total_investigations" in incidents
    assert "confirmed_oil" in incidents
    assert "lookalike_count" in incidents

    mr = data["model_readiness"]
    assert "honest_metrics" in mr
    assert mr["honest_metrics"]["pixel_pooled"]["recall"] == 0.3949
    assert mr["honest_metrics"]["oil_scene_macro"]["recall"] == 0.6886
    assert mr["honest_metrics"]["classifier_metrics"]["auroc"] == 0.986


def test_analyze_ais_fail_closed_when_gated():
    client = TestClient(app)
    # Create an investigation with attribution gating active (e.g. LOOKALIKE)
    inv_id = "test_gated_lookalike"
    dummy_inv = Investigation(
        id=inv_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        mode="real_satellite",
        data_sources={"satellite": "test", "ais": "test", "environment": "test", "detection_method": "ML", "drift_method": "test", "attribution_method": "test"},
        satellite={
            "geometry": {"area_px": 500, "area_km2": 0.5, "centroid_lat": 19.0, "centroid_lon": 71.0},
            "attribution_gating": {
                "gated": True,
                "status": "ABSTAINED_LOOKALIKE",
                "reason": "Lookalike detected; vessel attribution withheld.",
                "oil_status": "LOOKALIKE",
                "mask_area_px": 500,
            },
            "attribution_status": "ABSTAINED_LOOKALIKE",
        },
        environment={},
        drift={
            "probable_origin": {"lat": 19.0, "lon": 71.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            "observed": {"lat": 19.0, "lon": 71.0, "timestamp": datetime.now(timezone.utc).isoformat()},
        },
        ais_summary={"search_radius_km": 50, "time_window_hours": 12, "attribution_gated": True},
        candidates=[],
    )
    _STORE[inv_id] = dummy_inv

    # Call /api/ais/analyze - must return candidates=[] and not bypass the safety gate
    resp = client.post(f"/api/ais/analyze?investigation_id={inv_id}&search_radius_km=30")
    assert resp.status_code == 200
    res = resp.json()
    assert res["candidates"] == []

    # Call /api/attribution/analyze - must also return candidates=[] and gated=True
    resp2 = client.post(f"/api/attribution/analyze?investigation_id={inv_id}", json={"spatial_proximity": 50})
    assert resp2.status_code == 200
    res2 = resp2.json()
    assert res2["candidates"] == []
    assert res2.get("gated") is True
