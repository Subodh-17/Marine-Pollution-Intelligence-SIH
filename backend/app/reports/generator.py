"""
Investigation report generator (Section 31).

Produces a Markdown report (source of truth) and a PDF rendering of it.
Content is generated entirely from the `Investigation` result object - no
values are invented here. Language follows Section 19 (never asserts proven
causation) and Section 37 (states limitations explicitly).
"""
from __future__ import annotations

from app.investigation.orchestrator import Investigation


def _fmt_pt(p: dict) -> str:
    return f"{p['label']} ({p['timestamp']}): {p['lat']:.4f}, {p['lon']:.4f}  [uncertainty ~{p['uncertainty_km']} km]"


# Detector-aware confidence wording (see MODEL_CARD.md "ML confidence must
# be honest"): the confidence NUMBER's actual mechanism differs by which
# detector produced the final result, so the report must never caption a
# classical heuristic score as if it were an ML softmax score, or vice versa
# - and a fallback result must say so explicitly, not read as if the ML
# model itself produced it.
_CONFIDENCE_WORDING = {
    "CLASSICAL_DARK_SPOT": "heuristic score",
    "ML_PROTOTYPE_SYNTHETIC_TRAINED": "uncalibrated prototype softmax score",
    "ML_REAL_ZENODO_TRAINED": "real-data-trained model softmax score",
    "CLASSICAL_DARK_SPOT_ML_FALLBACK": "heuristic classical score (ML fallback)",
}
_CONFIDENCE_WORDING_LONG = {
    "CLASSICAL_DARK_SPOT": (
        "heuristic detection confidence — a 0–100% plausibility index (elongation, texture, "
        "solidity, size) from the classical dark-spot detector, not a calibrated statistical "
        "probability"
    ),
    "ML_PROTOTYPE_SYNTHETIC_TRAINED": (
        "the ML prototype classifier's own raw, uncalibrated softmax score — not a calibrated "
        "statistical probability, and the model is not validated on real Sentinel-1 data "
        "(see Model Card)"
    ),
    "ML_REAL_ZENODO_TRAINED": (
        "the real-data-trained model's own softmax score — trained on the real Zenodo Sentinel-1 "
        "SAR Oil Spill Dataset, but still not a calibrated statistical probability; see the "
        "evaluation report referenced below for held-out test-set metrics before treating this "
        "as validated for a new deployment context (see Model Card §6b)"
    ),
    "CLASSICAL_DARK_SPOT_ML_FALLBACK": (
        "heuristic classical detection confidence — ML detection was requested but did not "
        "produce a usable result, so the classical detector's heuristic score is reported "
        "instead (see the explicit fallback reason in Section 2/3 detection notes)"
    ),
}


def _confidence_wording(detector_provenance: str) -> str:
    return _CONFIDENCE_WORDING.get(detector_provenance, "heuristic score")


def _confidence_wording_long(detector_provenance: str) -> str:
    return _CONFIDENCE_WORDING_LONG.get(
        detector_provenance,
        "heuristic detection confidence — a 0–100% plausibility index, not a calibrated "
        "statistical probability",
    )


def generate_markdown_report(inv: Investigation) -> str:
    sat = inv.satellite
    geo = sat.get("geometry") or {}
    age = sat.get("age_estimate") or {}
    env = inv.environment
    drift = inv.drift
    ais = inv.ais_summary
    candidates = inv.candidates

    lines = []
    lines.append(f"# MARINE POLLUTION INTELLIGENCE INVESTIGATION REPORT")
    lines.append(f"\n**Investigation ID:** {inv.id}  \n**Generated:** {inv.created_at} UTC  \n**Mode:** {inv.mode}\n")

    detector_provenance = sat.get("detector_provenance", "CLASSICAL_DARK_SPOT")
    scene_classification = sat.get("scene_classification")

    lines.append("---\n## 1. Incident Overview")
    if scene_classification and scene_classification.get("oil_status") not in (None, "LIKELY_OIL"):
        # Phase 3 safety requirement: this must be impossible to miss, and
        # must appear even when a segmentation mask was also produced (see
        # app/ml/detector.py's scene_classification docstring for why the
        # two can disagree).
        verdict = scene_classification["oil_status"]
        verdict_wording = {
            "NO_OIL": "**NO OIL** - the scene classifier does not consider this scene to contain oil",
            "LOOKALIKE": "**SAR LOOK-ALIKE / UNCERTAIN TARGET** - a SAR dark-region anomaly consistent with a "
                          "non-oil cause (e.g. low-wind area, ship wake, natural slick, rain), not a confirmed "
                          "or likely oil spill",
            "UNCERTAIN": "**INSUFFICIENT CONFIDENCE** - the scene classifier's confidence was too low to "
                          "reliably assign any of Oil / No-Oil / Lookalike",
        }.get(verdict, verdict)
        lines.append(
            f"> **Scene classification verdict: {verdict_wording}** "
            f"(classifier: {scene_classification['label']}, {scene_classification['confidence']*100:.1f}% "
            f"confidence). This applies regardless of whether a segmentation mask is shown below - a "
            f"segmentation mask being present does NOT override this verdict."
        )
    if sat.get("geometry"):
        lines.append(
            f"An oil-slick-like feature was detected in a {sat['source']} SAR scene "
            f"with a {_confidence_wording(detector_provenance)} of **{sat['confidence']*100:.1f}%**. "
            f"This report documents the automated detection, drift reconstruction, "
            f"and AIS-based candidate-vessel analysis performed for this incident. "
            f"**All findings below are decision-support indicators, not a legal "
            f"determination of fault.**"
        )
        if detector_provenance == "CLASSICAL_DARK_SPOT_ML_FALLBACK":
            fallback_notes = [n for n in sat.get("detection_notes", []) if n.startswith("ML_") or "fallback" in n.lower()]
            lines.append(
                "\n> **ML fallback notice:** ML detection was requested for this scene but did "
                "not produce a usable result, so the classical detector's output is reported "
                "above instead. " + (fallback_notes[0] if fallback_notes else "See Section 2/3 detection notes for the specific reason.")
            )
    else:
        lines.append(
            f"No oil-slick-like feature was detected in this {sat['source']} SAR scene above "
            f"the detector's plausibility threshold. No hindcast, origin estimate, or AIS "
            f"attribution analysis was performed, since none of those steps have a valid "
            f"location or time to anchor to without a detected slick. See Section 3 for "
            f"detector notes."
        )

    lines.append("\n## 2. Satellite Observation")
    lines.append(
        f"- Data source: {sat['source']} (reference dataset: {sat['dataset_reference']})\n"
        f"- Detection method: {sat['method']}\n"
        f"- Geolocation source: **{sat.get('geolocation_source', 'assumed_center_coordinates')}**"
        f"{' (assumed/approximate, not read from the file)' if sat.get('geolocation_is_assumed') else ' (read from the file' + chr(39) + 's embedded GeoTIFF tags)'}\n"
        f"- Notes: " + "; ".join(sat.get("detection_notes", [])[:5])
    )

    lines.append("\n## 3. Oil Slick Detection")
    lines.append(
        f"Detection confidence ({_confidence_wording_long(detector_provenance)}): "
        f"**{sat['confidence']*100:.1f}%**"
    )
    lines.append(f"Detector provenance: **{detector_provenance}**")
    lines.append(f"Processing method: **{sat.get('method', 'n/a')}**")
    if scene_classification:
        lines.append(
            f"Scene classification: **{scene_classification['label']}** "
            f"({scene_classification['confidence']*100:.1f}% confidence) -> "
            f"**{scene_classification['oil_status']}**  \n"
            f"Class probabilities: " + ", ".join(
                f"{k} {v*100:.1f}%" for k, v in scene_classification["probabilities"].items()
            )
        )

    model_info = sat.get("model_provenance_info")
    if model_info:
        lines.append("\n**Model provenance:**")
        lines.append(f"- Model: {model_info.get('model_name')} (version: {model_info.get('model_version')})")
        lines.append(
            f"- Trained on real Sentinel-1 SAR data: "
            f"**{'Yes' if model_info.get('is_real_data_trained') else 'No'}**"
        )
        lines.append(f"- Training dataset: {model_info.get('dataset_used_for_training')}")
        if model_info.get("dataset_dois"):
            dois = model_info["dataset_dois"]
            lines.append("- Dataset DOIs: " + ", ".join(f"{k}: https://doi.org/{v}" for k, v in dois.items()))
        lines.append(f"- Input channels: {model_info.get('input_channels')}  |  Preprocessing version: `{model_info.get('preprocessing_version')}`")
        if model_info.get("training_date"):
            lines.append(f"- Training date: {model_info.get('training_date')}")
        if model_info.get("dataset_split"):
            lines.append(f"- Dataset split (train/val samples): {model_info['dataset_split']}")
        lines.append(
            "- Evaluation status: "
            + (
                "trained on real data - see this checkpoint's own `evaluation_report.json`/`.md` "
                "(app/ml/evaluate_real.py output) for held-out test-set metrics; none are restated "
                "here to avoid this report going stale relative to that file."
                if model_info.get("is_real_data_trained") else
                "not evaluated against real Sentinel-1 SAR data - this checkpoint is a synthetic-"
                "trained prototype (see Model Card §6)."
            )
        )
        if model_info.get("limitations"):
            lines.append("- Model limitations:")
            for lim in model_info["limitations"]:
                lines.append(f"  - {lim}")

    lines.append("\n## 4. Slick Characterisation")
    if geo:
        lines.append(
            f"- Area: **{geo['area_km2']} km²**\n"
            f"- Length: **{geo['length_km']} km**  |  Width: **{geo['width_km']} km**\n"
            f"- Aspect ratio: {geo['aspect_ratio']}  |  Orientation: {geo['orientation_deg']}°\n"
            f"- Centroid: {geo['centroid_lat']}°, {geo['centroid_lon']}°\n"
            f"- Perimeter: {geo['perimeter_km']} km"
        )
    else:
        lines.append("No slick geometry available.")

    lines.append("\n## 5. Estimated Spill Age")
    if not geo or age.get("confidence") in (None, "unavailable"):
        lines.append(f"**{age.get('caveat', 'Age estimate unavailable / low confidence: no slick was characterised.')}**")
    else:
        lines.append(
            f"Estimated age: **~{age['hours']} hours** (range {age['hours_low']}-{age['hours_high']} h). "
            f"Confidence: **{age['confidence']}**. Method: {age['method']}.\n\n"
            f"> {age['caveat']}"
        )

    lines.append("\n## 6. Environmental Conditions")
    if env:
        lines.append(
            f"- Wind: {env['wind_speed_ms']} m/s from {env['wind_dir_deg']}°\n"
            f"- Current: {env['current_speed_ms']} m/s toward {env['current_dir_deg']}°\n"
            f"- Wave height: {env['wave_height_m']} m\n"
            f"- Data source: **{env['source']}**"
            + (f" (retrieved: {env['retrieved_at']})" if env.get("retrieved_at") else "")
        )
        if env.get("fallback_reason"):
            lines.append(f"\n> **Why not a higher-priority source:** {env['fallback_reason']}")
    else:
        lines.append("Not evaluated — no slick location was available to sample environmental conditions at.")

    lines.append("\n## 7. Hindcast Analysis")
    if drift.get("probable_origin"):
        lines.append("Backward trajectory (oldest to observed):\n")
        for p in drift["backward_track"]:
            lines.append(f"- {_fmt_pt(p)}")
        lines.append(f"- {_fmt_pt(drift['observed'])}")
    else:
        lines.append("Not computed — no slick was detected to hindcast from.")
    for note in drift.get("notes", []):
        lines.append(f"\n> {note}")

    lines.append("\n## 8. Estimated Spill Origin")
    origin = drift.get("probable_origin")
    if origin:
        lines.append(
            f"**Probable origin:** {origin['lat']}°, {origin['lon']}°  \n"
            f"**Probable origin time:** {origin['timestamp']}  \n"
            f"**Origin confidence:** {drift['origin_confidence']} (heuristic - shrinks with hindcast horizon length)  \n"
            f"**Environment data source used for drift:** {drift['environment_source']}"
        )
    else:
        lines.append("Not available — no slick was detected in this scene.")

    lines.append("\n## 9. Forward Forecast")
    if drift.get("forward_track"):
        for p in drift["forward_track"]:
            lines.append(f"- {_fmt_pt(p)}")
    else:
        lines.append("Not computed — no slick was detected to forecast from.")

    lines.append("\n## 10. AIS Traffic Reconstruction")
    if ais.get("ais_source") == "NOT_RUN":
        lines.append(f"Not run: {ais['reference_dataset']}")
    else:
        lines.append(
            f"- AIS source: **{ais['ais_source']}** (reference: {ais['reference_dataset']})\n"
            f"- Raw records ingested: {ais['total_raw_records']}\n"
            f"- After cleaning: {ais['cleaned_records']}\n"
            f"- Within {ais['search_radius_km']} km of estimated origin: {ais['within_spatial_filter']}\n"
            f"- Within ±{ais['time_window_hours']} h investigation window: {ais['within_final_filter']}\n"
            f"- Unique vessels in final candidate pool: {ais['unique_vessels_in_window']}\n"
            f"- Investigation window: {ais['window_start']} -> {ais['window_end']}"
        )

    lines.append("\n## 11. Candidate Vessel Analysis")
    if not candidates:
        lines.append("No candidate vessels — AIS attribution was not run for this investigation." if ais.get("ais_source") == "NOT_RUN" else "No candidate vessels found within the search parameters.")
    for i, c in enumerate(candidates[:10], start=1):
        cd = c.to_dict()
        lines.append(f"\n### {i}. {cd['vessel_name'] or cd['mmsi']} (MMSI {cd['mmsi']}) - Score {cd['final_score']}/100")
        lines.append(f"- Distance to estimated origin: {cd['min_distance_km']} km  |  Time delta from origin: {cd['time_delta_hours']} h")
        if cd.get("distance_from_detection_km") is not None:
            lines.append(
                f"- Distance to raw detection location: {cd['distance_from_detection_km']} km  |  "
                f"Time delta from detection: {cd.get('time_delta_from_detection_hours')} h"
            )
        lines.append(
            f"- Data completeness: {cd['data_completeness_pct']}%  |  "
            f"Confidence in this score: **{cd['confidence_level']}** (data-quality confidence, not a probability of guilt)"
        )
        if cd.get("data_warnings"):
            lines.append("- Missing-data warnings: " + "; ".join(cd["data_warnings"]))
        for e in cd["evidence_summary"]:
            lines.append(f"  - {e}")

    lines.append("\n## 12. Vessel Ranking")
    lines.append("| Rank | Vessel | MMSI | Distance (origin) | Time delta | Score | Confidence | Data completeness |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(candidates, start=1):
        cd = c.to_dict()
        lines.append(
            f"| {i} | {cd['vessel_name'] or '-'} | {cd['mmsi']} | {cd['min_distance_km']} km | "
            f"{cd['time_delta_hours']} h | {cd['final_score']} | {cd['confidence_level']} | {cd['data_completeness_pct']}% |"
        )

    lines.append("\n## 13. Evidence Breakdown (Top Candidate)")
    if candidates:
        top = candidates[0].to_dict()
        lines.append(f"**{top['vessel_name'] or top['mmsi']} - Final Score: {top['final_score']}/100**\n")
        for b in top["breakdown"]:
            lines.append(f"- {b['component'].replace('_',' ').title()}: {b['points']}/{b['max_points']} — {b['explanation']}")

    lines.append("\n## 14. Attribution Confidence")
    lines.append(
        "Attribution scores are a **transparent, weighted spatio-temporal correlation index**, "
        "not a statistical probability of guilt and not a legal finding. **AIS-based vessel "
        "ranking is probabilistic investigative assistance** intended to prioritise which vessels "
        "warrant further human investigation - it is not a determination of fault. The "
        "highest-ranked vessel is described as the **highest-priority candidate** for further "
        "investigation, based on the available evidence."
    )

    lines.append("\n## 15. Limitations")
    lines.append(
        "- SAR dark-spot detection can false-positive on low-wind areas, biogenic slicks, and other look-alikes.\n"
        f"- Detection confidence for this investigation is a {_confidence_wording(detector_provenance)} "
        "(not a calibrated statistical probability); classical scores are a plausibility index from "
        "shape/texture, ML scores are the prototype classifier's own uncalibrated softmax output.\n"
        "- The drift model is a simplified advection model (centroid transport only; no spreading/diffusion).\n"
        "- Environmental and/or AIS data in this run may be synthetic or cached rather than live-sourced "
        "(see Data Source Indicator above) - this build did not have network access to live Sentinel-1, "
        "NOAA/Copernicus, or MarineCadastre endpoints.\n"
        "- Spill age is a low-confidence, single-scene heuristic.\n"
        "- AIS data can contain gaps, spoofing, or errors; absence of AIS proximity does not exonerate a vessel, "
        "and presence does not prove responsibility.\n"
        "- AIS-based vessel ranking is probabilistic investigative assistance, not a legal or "
        "statistical determination of fault.\n"
        "- This system is an investigative decision-support tool, not a legal proof engine."
    )

    lines.append(
        "\n---\n**All findings are decision-support indicators and not a legal determination "
        "of fault or responsibility.**"
    )

    return "\n".join(lines)


def generate_csv_report(inv: Investigation) -> str:
    """Flat key/value CSV summary of the same provenance/limitations facts
    the Markdown/PDF report states in prose - model provenance, dataset DOI,
    model version, processing method, confidence, evaluation status,
    detection limitations, and the AIS-ranking-is-probabilistic disclaimer
    (Part H requirement) - for spreadsheet-based downstream consumption."""
    import csv
    import io

    sat = inv.satellite
    detector_provenance = sat.get("detector_provenance", "CLASSICAL_DARK_SPOT")
    model_info = sat.get("model_provenance_info") or {}

    rows = [
        ("investigation_id", inv.id),
        ("generated_at_utc", inv.created_at),
        ("mode", inv.mode),
        ("detector_provenance", detector_provenance),
        ("processing_method", sat.get("method", "n/a")),
        ("confidence_pct", round(sat.get("confidence", 0.0) * 100, 1)),
        ("confidence_wording", _confidence_wording(detector_provenance)),
        ("model_name", model_info.get("model_name", "n/a")),
        ("model_version", model_info.get("model_version", "n/a")),
        ("is_real_data_trained", model_info.get("is_real_data_trained", False)),
        ("dataset_used_for_training", model_info.get("dataset_used_for_training", "n/a")),
        ("dataset_dois", "; ".join(f"{k}: https://doi.org/{v}" for k, v in (model_info.get("dataset_dois") or {}).items()) or "n/a"),
        ("preprocessing_version", model_info.get("preprocessing_version") or "n/a"),
        ("training_date", model_info.get("training_date") or "n/a"),
        (
            "evaluation_status",
            (
                "trained on real data - see this checkpoint's own evaluation_report.json/.md"
                if model_info.get("is_real_data_trained")
                else "synthetic-trained prototype - not evaluated against real Sentinel-1 SAR data"
            ) if model_info else "n/a (classical detector - not a trained model)",
        ),
        ("model_limitations", " | ".join(model_info.get("limitations") or []) or "n/a"),
        (
            "ais_ranking_disclaimer",
            "AIS-based vessel ranking is probabilistic investigative assistance, not a legal or "
            "statistical determination of fault.",
        ),
        ("top_candidate_vessel", (inv.candidates[0].to_dict()["vessel_name"] or inv.candidates[0].to_dict()["mmsi"]) if inv.candidates else "n/a"),
        ("top_candidate_score", inv.candidates[0].to_dict()["final_score"] if inv.candidates else "n/a"),
    ]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["field", "value"])
    writer.writerows(rows)
    return buf.getvalue()


def generate_pdf_report(inv: Investigation, out_path: str) -> str:
    """Render the investigation to a PDF at `out_path`.

    Portability note (Section 3 of the SIH hardening brief): `out_path` is
    used exactly as given via `pathlib.Path`, so both forward-slash and
    backslash-style paths work correctly on Windows, Linux, and macOS. The
    parent directory is created automatically if it doesn't exist yet -
    previously, callers (including the test suite) had to pre-create it
    themselves, and a missing directory raised a raw FileNotFoundError from
    inside reportlab instead of a clear, actionable error.
    """
    import os
    from pathlib import Path
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    out_path = str(Path(out_path))
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    md = generate_markdown_report(inv)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)

    doc = SimpleDocTemplate(out_path, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm)
    story = []
    for raw_line in md.split("\n"):
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        safe = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace("**", "").replace("*", ""))
        if line.startswith("# "):
            story.append(Paragraph(safe.lstrip("# "), h1))
        elif line.startswith("## "):
            story.append(Paragraph(safe.lstrip("# "), h2))
        elif line.startswith("### "):
            story.append(Paragraph(f"<b>{safe.lstrip('# ')}</b>", body))
        elif line.startswith("|"):
            continue  # tables rendered separately below for readability
        else:
            story.append(Paragraph(safe, body))

    # append a clean ranking table at the end for readability
    if inv.candidates:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Vessel Ranking (table)", h2))
        data = [["Rank", "Vessel", "MMSI", "Distance (km)", "Time delta (h)", "Score", "Confidence", "Data %"]]
        for i, c in enumerate(inv.candidates, start=1):
            cd = c.to_dict()
            data.append([
                str(i), cd["vessel_name"] or "-", cd["mmsi"], str(cd["min_distance_km"]),
                str(cd["time_delta_hours"]), str(cd["final_score"]), cd["confidence_level"],
                str(cd["data_completeness_pct"]),
            ])
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d5c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(t)

    doc.build(story)
    return out_path
