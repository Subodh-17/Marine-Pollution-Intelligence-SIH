# FINAL_IMPROVEMENT_REPORT.md

Scope note, stated up front and repeated at the end: this request specified
20 phases. This round completed a real, verified subset in depth rather
than a shallow pass over all 20 — see §12 for exactly what remains.

## 1. What was inspected

`backend/app/ml/` in full (detector.py, architectures.py, onnx_engine.py,
export_onnx.py, verify_onnx.py, train.py, evaluate_real.py, promote.py,
dataset.py), `backend/app/main.py`'s ML endpoints, `backend/app/config.py`,
`backend/data/ml_checkpoints/` (all `.pth`/`.pt`/`.json`/`.md`),
`backend/tests/test_onnx_inference.py`, `README.md`,
`backend/app/ml/README_ML_STATUS.md`, `docs/DEPLOYMENT.md`, and a targeted
repo-wide grep for absolute paths, TODO/FIXME, and stale ML-status claims.
AIS/drift/vessel-attribution/frontend/security got a lighter pass (grep-based,
not full read-through) — see §12.

## 2. What was changed

**New files:**
- `app/ml/tiling.py` — tiled segmenter inference + classifier downsampling
- `app/ml/paths.py` — shared repo-relative-path helper
- `scripts/sanitize_metadata_paths.py` — one-time metadata cleanup
- `backend/tests/test_tiling.py` — 15 tests
- `work/FINAL_AUDIT.md`, `work/PATH_SANITIZATION_LOG.json`
- `CHECKPOINT_INTEGRATION_GUIDE.md`, this file

**Modified:**
- `app/ml/detector.py` — env-var checkpoint-filename overrides (Phase 1);
  large scenes now tile/downsample instead of an outright refusal (Phase 3)
- `app/ml/evaluate_real.py`, `app/ml/train.py` — write repo-relative paths
  into metadata instead of `os.path.abspath()` (Phase 9 root cause, not
  just the symptom)
- `backend/data/ml_checkpoints/evaluation_report.json`/`.md`,
  `classifier_real_training_config.json`,
  `classifier_real_dataset_manifest.json`,
  `spill_unet_real_training_config.json`,
  `spill_unet_real_dataset_manifest.json` — 12 absolute-path fields
  sanitized to relative; every other field (metrics, hashes, epochs,
  dates) byte-for-byte unchanged
- `backend/tests/test_onnx_inference.py` — one test updated because Phase 3
  intentionally changed its premise (a scene that used to be refused now
  legitimately succeeds via tiling); split into three tests that each check
  one specific thing (guard-only, tiling-succeeds, outer-ceiling-still-fails)
- `docs/DEPLOYMENT.md` — documented the six new env vars

**Explicitly not modified:** no `.pth`/`.pt` file, no model architecture, no
class mapping, no preprocessing formula, no metric value.

## 3. ML pipeline changes

Checkpoint discovery is now overridable per-filename via
`ML_CLASSIFIER_CHECKPOINT`/`ML_SEGMENTER_CHECKPOINT`/`ML_CLASSIFIER_ONNX`/
`ML_SEGMENTER_ONNX` (unset → identical default behavior — verified).

## 4. Large-scene / tiled inference (Phase 3)

Segmenter tiles at a configurable size/overlap (default 256px/32px,
matching training resolution) and stitches via overlap-averaging; classifier
uses a block-mean-downsampled whole-scene view above the direct budget
(tiling a whole-scene classification decision has no principled aggregation
this codebase defines, so it wasn't invented). An outer ceiling
(`ML_MAX_TILED_INFERENCE_PIXELS`, default 4096×4096, **estimated not
measured on Render**) still applies. Verified end-to-end against the real
checkpoints: 64×64 unchanged (direct), 1024×1024 now runs genuine tiled ML
at full resolution (25 tiles) where it previously refused outright,
5000×5000 still refuses safely. Documented, not hidden: tiling introduces a
small boundary effect (measured max diff ~0.014 vs. a direct pass) from
per-tile zero-padding.

## 5. preprocessing / inference changes

None beyond the tiling integration itself — `sar_percentile_1_99_minmax_v1`,
channel order, softmax/sigmoid postprocessing, and the 0.5 threshold are
unchanged; tiling reuses the exact same `_forward_segmenter` per tile.

## 6. ONNX changes

None this round beyond what shipped in the previous session
(`onnx_engine.py`, `export_onnx.py`, `verify_onnx.py`) — verified still
working (26/26 ONNX tests pass with sandbox-built ONNX artifacts staged,
then removed before packaging — see §7 for why).

## 7. Backend changes

See §2. No API contract changes; `/api/ml/status` fields are unchanged from
the previous round.

## 8. Frontend changes

None this round. `npm test`/`npm run build`/`npm run lint` **could not
run** — no network, no `node_modules`, matching the constraint stated at
the start of this thread.

## 9. Test results

`backend/tests/test_onnx_inference.py` + `test_tiling.py`, run via a
pytest-compatible shim I wrote (real `pytest` is not installed in this
sandbox — no network to install it):

```
41 passed, 0 failed, 2 skipped
```

The 2 skips are the PyTorch-vs-ONNX parity tests (skip reason: "PyTorch not
installed — parity NOT verified in this environment"), not silent passes.
Run with sandbox-built `.onnx` artifacts staged (via a torch-free reader +
minimal ONNX writer I built last round, since neither `torch` nor `onnx`
can be installed here); those artifacts were removed before packaging —
they are not `torch.onnx.export` output and must not ship as production
models (see `CHECKPOINT_INTEGRATION_GUIDE.md` §5 for the real export step).

`pytest -q` (real pytest), `npm test -- --run`, `npm run build`,
`npm run lint`, and a real Render-Free pip-install simulation: **not run —
BLOCKED**, no network/pytest/node_modules in this sandbox.

## 10. Known limitations

- The tiling boundary effect (§4) is real and measured, not eliminated.
- `ML_MAX_TILED_INFERENCE_PIXELS`'s default is an estimate; actual safe
  ceiling on Render Free's 0.1 CPU is unmeasured (this sandbox has no
  representative CPU throttling to test against).
- `app/ml/paths.py`'s `portable_path()` falls back to an absolute path when
  a dataset/checkpoint lives genuinely outside the repo (e.g., your `D:`
  drive dataset) — this is by design (a relative path can't express that),
  not a bug, but it means a future evaluation run with the dataset outside
  the repo will still record an absolute path for that specific case.

## 11. What remains blocked until you insert checkpoints

- ONNX export (needs `torch`, not installed here)
- PyTorch-vs-ONNX parity verification against your specific weights
- Real Zenodo held-out evaluation re-run (needs `backend/data/zenodo_sar`,
  not present in this checkout)
- Render-Free pip-install simulation
- Full `pytest -q` / `npm test` / `npm run build` / `npm run lint`

`CHECKPOINT_INTEGRATION_GUIDE.md` gives the exact commands for all of these.

## 12. Not attempted this round (scope triage, stated plainly)

Phase 6 (evidence-fusion redesign), Phase 7 (lookalike suppression beyond
documenting the one real threshold — no lookalike-specific mechanism exists
in this architecture to make configurable; inventing one was explicitly
disallowed), Phase 11 (vessel-attribution explainability rewrite), Phase 12
(drift-model uncertainty modeling), Phase 13 (full API robustness audit),
Phase 14 (frontend audit/lint/build — also hard-blocked), Phase 17 (full
security audit beyond the targeted checks folded into Phase 9). Each is a
substantial standalone feature or audit, not a bug fix, and attempting all
of them in one pass risks exactly the unreviewed, unverified changes this
project's own `CHANGELOG_FINAL.md` history shows has been a repeated
problem. Recommend tackling one at a time in follow-up rounds.

## 13. Exact checkpoint placement instructions

See `CHECKPOINT_INTEGRATION_GUIDE.md` in full.

## 14. Confirmation

No model was retrained. `classifier_real_best.pth` /
`classifier_real_promoted.pth` sha256:
`409109a444196aafb5fc12aedb095bdde4e9062b0911373510535dabb052a75e` (both,
confirming they're still the identical bytes from the checkpoints you
provided). `spill_unet_real_best.pt` / `spill_unet_real_promoted.pt` sha256:
`af725b2587baed621b6ee264598a44dea008dc7214ccf454ccadc2fdd50e4361` (both).
Neither weight file was opened for writing at any point this round — only
read (for the checksum) or left untouched.
