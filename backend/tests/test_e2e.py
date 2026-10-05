from app.investigation.orchestrator import run_investigation, get_investigation


def test_full_demo_investigation_runs_end_to_end():
    inv = run_investigation(mode="demo")

    assert inv.id
    assert inv.satellite["geometry"] is not None
    assert inv.satellite["confidence"] > 0

    assert inv.drift["probable_origin"]["lat"] != 0
    assert len(inv.drift["backward_track"]) == 4
    assert len(inv.drift["forward_track"]) == 4

    assert inv.ais_summary["total_raw_records"] > 0
    assert inv.ais_summary["within_final_filter"] >= 1

    assert len(inv.candidates) >= 1
    # top candidate should be Vessel A given the demo scenario design
    top = inv.candidates[0]
    assert top.final_score > 0

    steps = [p["step"] for p in inv.progress_log]
    for expected in [
        "Satellite scene loaded", "SAR preprocessing completed", "Slick geometry calculated",
        "Hindcast completed", "Spill origin estimated", "Forecast generated",
        "AIS traffic reconstructed", "Irrelevant vessels filtered",
        "Candidate scoring completed", "Vessel ranking generated", "Investigation ready",
    ]:
        assert expected in steps


def test_investigation_retrievable_by_id():
    inv = run_investigation(mode="demo")
    fetched = get_investigation(inv.id)
    assert fetched is not None
    assert fetched.id == inv.id


def test_vessel_a_ranks_highly_in_demo_scenario():
    """Vessel A is constructed to pass directly through the estimated
    origin at approximately the right time - it should rank at or near the
    top of the candidate list."""
    inv = run_investigation(mode="demo")
    names = [c.vessel_name for c in inv.candidates]
    assert "MV KONKAN STAR" in names
    rank = names.index("MV KONKAN STAR")
    assert rank <= 1  # top-2 at worst, given synthetic construction


def test_background_vessels_are_filtered_out():
    inv = run_investigation(mode="demo")
    names = [c.vessel_name for c in inv.candidates]
    background_present = [n for n in names if n and "BACKGROUND" in n]
    assert len(background_present) == 0, "Background/irrelevant traffic should be filtered by spatial/temporal filters"


def test_no_detection_does_not_crash_the_pipeline(tmp_path):
    """Regression test: if no slick is detected, characterize_slick() returns
    None, and the orchestrator must not dereference it (previously crashed
    with AttributeError). The investigation should complete cleanly with
    geometry=None, an empty candidate list, and no fabricated hindcast/AIS
    results — never silently substituting fake data for a real detection.

    Uses pytest's `tmp_path` fixture (not a hardcoded '/tmp/...' path) so
    this test is portable across Linux, macOS, and Windows, and never
    depends on a Unix-only directory existing."""
    import numpy as np
    from unittest.mock import patch
    from app.satellite.detection import DetectionResult
    from app.reports.generator import generate_markdown_report, generate_pdf_report

    def fake_detect(*a, **kw):
        return DetectionResult(
            mask=np.zeros((512, 512), dtype=bool), candidate_masks=[],
            confidence=0.0, notes=["No plausible oil-slick-like dark region found."],
        )

    with patch("app.investigation.orchestrator.detect_oil_slick", fake_detect):
        inv = run_investigation(mode="demo")

    assert inv.satellite["geometry"] is None
    assert inv.satellite["age_estimate"] is None
    assert inv.environment is None
    assert inv.drift["probable_origin"] is None
    assert inv.drift["backward_track"] == []
    assert inv.candidates == []
    assert inv.ais_summary["ais_source"] == "NOT_RUN"

    # report generation must also handle this gracefully end to end
    md = generate_markdown_report(inv)
    assert "No oil-slick-like feature was detected" in md
    assert "AttributeError" not in md

    pdf_path = generate_pdf_report(inv, str(tmp_path / "test_no_detection_report.pdf"))
    import os
    assert os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0

    # Defense-in-depth (Section 3): the output directory itself doesn't
    # exist yet here either - generate_pdf_report must create nested
    # directories automatically, not just accept an existing one.
    nested_path = generate_pdf_report(
        inv, str(tmp_path / "nested" / "reports" / "no_detection.pdf")
    )
    assert os.path.exists(nested_path) and os.path.getsize(nested_path) > 0
