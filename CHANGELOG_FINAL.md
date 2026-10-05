# Marine Pollution Intelligence — Consolidated Upgrade Report

Covers three successive upgrade requests against this project (a general
reliability/audit pass, a deployment-readiness pass with real ML
checkpoints introduced, and a cloud-deployment-configuration pass). This
report reflects the **final state** after all three; where a later round
superseded an earlier fix, only the final, kept version is described.

A note on method, since it matters for how much to trust this document:
every claim below marked "verified" was checked by actually running the
code in this environment (starting the real backend, hitting real
endpoints, running the real test suites, and — for PostgreSQL — installing
and running a real local PostgreSQL 16 server via apt). Claims marked
"reviewed" were checked by reading the code carefully but not executed
here (mainly things this sandbox has no way to execute, like an actual
Vercel/Render deployment). Nothing below is asserted without one of those
two backings.

---

## Round 4 — ONNX Runtime CPU production inference (no PyTorch)

**Goal:** run the *genuine, already-trained* Zenodo checkpoints in
production on Render Free (512 MB RAM, 0.1 CPU) without installing
PyTorch. No retraining, no substitute models, no architecture changes.

**Approach:** export the existing promoted checkpoints to ONNX offline
(PyTorch needed only for that one step) and serve them with ONNX Runtime's
CPU execution provider. Preprocessing (`sar_percentile_1_99_minmax_v1`),
postprocessing (softmax / `sigmoid > 0.5`), the dual-pol requirement, the
provenance enums, and the classical fallback are all unchanged.

**Added:** `app/ml/onnx_engine.py` (torch-free inference),
`app/ml/export_onnx.py` (offline export), `app/ml/verify_onnx.py`
(ONNX checks + torch-vs-ONNX parity), `tests/test_onnx_inference.py`.

**Changed:** `app/ml/detector.py` (ONNX-first runtime selection with
PyTorch fallback, runtime-dispatched forward passes, memory guard,
runtime-accurate `method` tag), `app/main.py` (`/api/ml/status` reports
`runtime`/`onnx_available`/`available`; history, metrics and `/ready` now
key off engine availability rather than torch presence),
`requirements.txt` (+`onnxruntime`), `requirements-ml.txt` (+`onnx`,
`onnxscript`, export-only), `MlTrainingTab.jsx` (+ inference-runtime row;
availability no longer keyed to torch), `docs/DEPLOYMENT.md` §5.

**Verified by execution** (this environment has `onnxruntime` 1.24.4 but
cannot install `torch`): ONNX CPU sessions load and run the real weights;
classifier returns 3 finite probabilities summing to 1; segmenter output
shape always equals input shape at 64x64 / 96x128 / 128x128 / 256x256;
full `detect()` returns `ML_REAL_ZENODO_TRAINED` with
`method=ml_real_onnx_spillunet` in ~145 ms at 256x256; stale-checksum,
missing-manifest and wrong-training-marker cases all refuse the real
label; oversized scenes and simulated session failures fall back without
fabricating a mask.

**NOT verified here:** `torch.onnx.export` itself, and therefore
torch-vs-ONNX parity — both require PyTorch. The parity tests skip with an
explicit reason rather than passing silently. Run
`python -m app.ml.export_onnx && python -m app.ml.verify_onnx` locally.

**Update, later pass — this gap has since been closed.** A later pass had
a sandbox where `pip install torch` succeeded (see MODEL_CARD.md §6
"Re-exercised end-to-end" and §6d for the full account), so
`python -m app.ml.export_onnx` and `python -m app.ml.verify_onnx` were
both actually run, against the real promoted checkpoints, producing
`classifier_real_best.onnx`, `spill_unet_real_best.onnx`, and
`onnx_export_manifest.json` (now present in `backend/data/ml_checkpoints/`
— not just described as something a future run would produce). PyTorch
vs ONNX Runtime parity was measured, not skipped: across four input sizes,
classifier max|Δ| ranged 2.94e-09–5.31e-08 and segmenter max|Δ| ranged
2.18e-09–2.49e-08, both many orders of magnitude inside the 1e-4
tolerance, with 100% argmax/mask agreement — see MODEL_CARD.md §6d for the
full table and for a real bug this surfaced and fixed (the export
manifest's `opset` field was recording the *requested* opset rather than
the one actually embedded in the file when the two diverged on a newer
torch version). All 36 tests in `tests/test_onnx_inference.py` now run
(not skip) and pass.

**Measured memory** (peak RSS, one forward pass through both models):
256x256 ~98 MB, 512x512 ~219 MB, 768x768 ~423 MB, 1024x1024 ~709 MB.
1024x1024 exceeds Render Free's 512 MB, so `ML_MAX_INFERENCE_PIXELS`
(default 512x512) routes larger scenes to the classical fallback rather
than risking an OOM kill.

**Packaging:** the previous release archive contained a 68 MB
`backend/data/app.db` runtime database; removed.

---

## A. Changelog

### Fixed (genuine bugs found and corrected)

1. **Internal server temp-path leaked into a client-facing error** —
   `app/ais/adapters.py`. An AIS CSV missing required columns raised a
   `ValueError` embedding the server's randomly-named temp file path
   (`/tmp/tmpXXXXXXXX.csv`), which propagated into both the synchronous
   400 response and the async job's `job.error` field. Fixed at the
   source so both paths are covered by one change. (The equivalent bug in
   the satellite-upload path had already been fixed independently before
   this round.)
2. **No restore-on-refresh** — `frontend/src/App.jsx`. The backend already
   persisted investigations (SQLite/Postgres) and already exposed
   `GET /api/spill/{id}`, but the frontend had no way to use it — a
   browser refresh lost the investigation entirely. Added a
   `sessionStorage`-based restore effect: stores the active investigation
   id after each successful run, silently restores it on mount, silently
   clears it if the backend no longer has it (never shows an error banner
   for a stale local pointer).
3. **PostgreSQL persistence layer had a silent data-corruption bug** —
   `app/persistence/db.py`. The initial implementation forgot
   `cursor_factory=RealDictCursor` on the psycopg2 cursor, so `dict(row)`
   calls silently misbehaved (a `(long_json_string, short_owner_string)`
   tuple looks enough like a malformed key-value pair to trip `dict()`'s
   "sequence element has wrong length" error rather than an obviously-
   wrong-type one). Several call sites' existing "log and return
   None/0/[]" error handling masked this as "no data found" rather than
   surfacing the real cause. Found and fixed via the new
   `test_persistence_postgres.py` suite, run against a real local
   Postgres server — not caught by code review alone.
4. **Database password leaked into logs and a public API response** —
   `app/persistence/db.py`. A misconfigured `DATABASE_URL`'s connection
   failure message embedded the full URL, including the plaintext
   password, in both the startup log line and the unauthenticated
   `GET /ready` endpoint's JSON response. Found live while testing the
   new startup-lifespan database check (see item 8) — this was not a
   pre-existing bug, it was introduced and caught within the same
   development session, but is reported here because it reached a
   runnable state before being fixed. Fixed with a `_redact_credentials`
   helper (masks the password, keeps the username for diagnostic value)
   applied to every message this module raises or logs. Verified live
   (password confirmed absent from both the log and the response) and
   covered by a regression test.
5. **Missing implied-speed sanity check in AIS ingestion** — identified as
   a gap in round 2 (self-reported SOG deltas were tracked as behavioral
   evidence, but nothing checked *implied* speed between consecutive
   pings against a physical ceiling, so a corrupted GPS fix or
   duplicate-MMSI collision could silently distort scoring). **Superseded
   by round 3's own independently-built solution** before this fix was
   finished: round 3's `app/ais/pipeline.py`/`behavior.py` already
   implements the same 60-knot-ceiling check, but surfaces it as an
   explicit `POSITION_JUMP` evidence flag (keeping the ping, showing the
   caveat) rather than silently dropping the ping — a better design for
   this project's "never hide evidence" ethos. My own competing
   drop-based implementation was deliberately **not** merged, to avoid
   two conflicting mechanisms; their version was verified via its own
   existing 4 tests.
6. **Stale documentation**: README claimed "pytest reports 1 skipped
   test"; actual count had grown to 14 (all torch-gated) as ML-path tests
   were added over successive rounds. Updated to describe the invariant
   ("all skips are torch-only, confirm with `pytest -rs`") rather than a
   number that will go stale again.
7. **`.gitignore` did not exclude any `.env` file** — a real risk given
   round 3 added a documented `cp .env.example .env` workflow; a
   real `.env` (or `frontend/.env.production`) containing
   `AUTH_SECRET_KEY` or a Postgres connection string could have been
   committed by accident. Added proper exclusions for both backend and
   frontend env files, plus `node_modules/`, `dist/`, and the (never
   shipped) real dataset directory, none of which were excluded before.

### Added (new capability, not bug fixes)

8. **Startup-time database check** — `app/main.py`'s FastAPI app now uses
   a `lifespan` handler that calls `init_db()` once at startup (only if
   `DATABASE_ENABLED`), logging a clear `database_ready` or
   `database_unavailable_at_startup` line — visible in a cloud platform's
   deploy logs — without ever crashing the app on a broken DB config
   (consistent with this project's existing "ML unavailable is
   informational, not fatal" precedent). `GET /ready`'s `checks` dict now
   also includes a live `database` field for the same reason.
9. **PostgreSQL support** — `app/persistence/db.py` now supports
   `DATABASE_URL` (postgres/postgresql scheme) as an alternative to the
   SQLite default, sharing one schema/query definition via a thin `?`→`%s`
   placeholder + `RealDictCursor` shim rather than a full ORM (justified:
   this project's entire persisted surface is five simple queries against
   two tables). **Genuinely tested**, not just written against docs: a
   real local PostgreSQL 16 server was installed via `apt-get` in this
   environment specifically to verify this against, and 7 new tests in
   `test_persistence_postgres.py` (round-trip, the `ON CONFLICT` upsert
   specifically, restart-survival, and a deliberately-unreachable-host
   failure case) run against it, with a graceful skip (verified in both
   directions) when psycopg2/Postgres aren't available. The SQLite default
   path is provably unaffected — the full pre-existing test suite passes
   byte-for-byte unchanged.
10. **`VITE_API_BASE_URL` production frontend config** —
    `frontend/src/api.js` now resolves its axios `baseURL` from
    `import.meta.env.VITE_API_BASE_URL` when set, falling back to the
    existing relative `/api` (Vite dev proxy) otherwise. Verified by
    actually building twice — once with the env var set (confirmed the
    URL appears correctly inlined in the built JS bundle) and once
    without (confirmed the default build is byte-for-byte unaffected).
11. **`docs/DEPLOYMENT.md`** — new deployment guide for Vercel
    (frontend) + Render (backend), covering build/start commands,
    required environment variables, PostgreSQL setup, CORS (bidirectional
    — both frontend and backend need updating), health/readiness
    endpoints, ML checkpoint memory/disk budgeting, auth setup, and a
    troubleshooting table. Explicitly flags what was verified here vs.
    what follows platform documentation without an actual account to test
    against (see the note at the top of that file).
12. **`requirements-postgres.txt`** and **`frontend/.env.example`** — new,
    following the existing `requirements-ml.txt` pattern (optional,
    install-when-needed) and the existing root `.env.example` pattern
    respectively.
13. **7 new coordinate-transform tests** (`test_coordinate_transforms.py`,
    round 2) checking round-trips and — more importantly — *axis
    direction* (row→south, col→east) for the pixel↔lat/lon affine
    transform, since a transposed lat/lon pair would still produce a
    plausible-looking coordinate for this app's Arabian Sea operating
    area without a direction-aware check. **Superseded/subsumed** by
    round 3's own more thorough `test_coordinate_validation.py` (which
    additionally covers antimeridian wrapping via an actual
    `destination_point` function neither I nor round 1/2 had identified
    existed) — kept as a complementary, non-duplicate addition rather
    than replaced, since it also exercises `characterize_slick()`
    end-to-end (a different function) via an asymmetric-mask direction
    check that round 3's file doesn't cover.

### Investigated and found to already be correct (no change needed)

- GeoTIFF CRS/geotransform handling (`app/satellite/geotiff_metadata.py`):
  correctly refuses to guess-reproject a projected CRS rather than
  mis-treating easting/northing as lat/lon; already had 10 tests.
- GeoJSON export coordinate order (`app/main.py`): both exports correctly
  use `[lon, lat]` per RFC 7946.
- AIS column-alias matching, timestamp parsing, and dedup
  (`app/ais/adapters.py`, `pipeline.py`): solid, already handled before
  any of these three rounds.
- ML safety property (`app/main.py`/`app/ml/detector.py`): verified live,
  with real ML checkpoint files present on disk but PyTorch not
  installed, that `GET /api/ml/status` correctly reports
  `torch_available: false` / `classifier_loaded: false` /
  `model_info: null` and never claims inference happened — and that the
  frontend (`MlTrainingTab.jsx`) gates its display on `torch_available`
  first, before even looking at the `provenance` field.
- Request-ID middleware, CORS env-configurability, upload byte-streamed
  size limits: already implemented before these rounds; documented
  accurately in `docs/DEPLOYMENT.md` rather than re-implemented.

---

## B. Tests executed and results

| Suite | Result |
|---|---|
| Backend `pytest` (full suite) | **289 passed, 14 skipped**, 0 failed. All 14 skips confirmed torch-gated only (`pytest -rs`), not genuine failures. |
| Backend `test_persistence_postgres.py` alone | **7 passed** against a real local PostgreSQL 16 server; confirmed **skips cleanly** (verified explicitly, in both directions) when `psycopg2` is uninstalled. |
| Frontend `vitest run` | **38 passed**, 0 failed (36 pre-existing + 2 new session-restore tests added in round 2). |
| Frontend `vite build` | Clean (one pre-existing, non-blocking chunk-size warning; unrelated to any change made here). |
| Live E2E (this session, final round) | Backend starts → `/health` 200 → `/ready` 200 with correct `checks.database` → demo investigation via `POST /api/spill/detect?mode=demo` → 200 → all 5 export formats (Markdown/PDF/CSV/spill-GeoJSON/vessel-GeoJSON) → 200 → real satellite upload (bundled sample GeoTIFF) → 200. |
| Live E2E (round 2, superseded findings corrected) | Full demo→AIS-alias-CSV→export cycle exercised; two apparent bugs (a "path traversal" file escape, an AIS-mode-ignored issue) were both traced to **my own test-scripting mistakes**, not application bugs, and reported as such rather than left ambiguous. |
| Broken-`DATABASE_URL` behavior (this round) | Verified live twice: app stays up (`/health` still 200), error surfaces clearly in both the startup log and `/ready`, and — after the fix — contains no plaintext password either place. |

---

## C. Files modified / added (final state, this round)

**Modified:**
- `backend/app/ais/adapters.py` — path-leak fix (carried from round 2)
- `backend/app/persistence/db.py` — PostgreSQL support, redaction fix
- `backend/app/config.py` — `database_url` setting, `_env_str_optional` helper
- `backend/app/main.py` — lifespan startup DB check, `/ready` database field
- `backend/tests/test_api.py` — 2 regression tests (path-leak, async job) — carried from round 2
- `README.md` — skipped-test count doc fix
- `frontend/src/App.jsx` — sessionStorage restore-on-refresh — carried from round 2
- `frontend/src/api.js` — `VITE_API_BASE_URL` support
- `frontend/src/test/smoke.test.jsx` — 2 new restore tests + `sessionStorage.clear()` isolation — carried from round 2
- `.env.example` — `DATABASE_URL` documentation
- `.gitignore` — env files, `node_modules/`, `dist/`, dataset dir

**Added:**
- `backend/tests/test_coordinate_transforms.py` (round 2, kept as complementary to round 3's own file)
- `backend/tests/test_persistence_postgres.py` (this round, 7 tests + regression test for the redaction fix)
- `backend/requirements-postgres.txt`
- `frontend/.env.example`
- `docs/DEPLOYMENT.md`

**Deliberately NOT modified**, despite being in scope for a general audit,
because a better/independent fix already existed in a later-uploaded
version of the project: `backend/app/ais/pipeline.py`,
`backend/app/ais/behavior.py`, `backend/tests/test_coordinate_validation.py`,
`frontend/src/components/InteractiveMap.jsx`, `TimelineTab.jsx`.

**Not touched, per the hard constraints in every round's brief:** any file
under `backend/data/ml_checkpoints/`, `backend/data/zenodo_sar/` (never
present), or `backend/app/ml/train.py`'s actual training logic.

---

## D. Remaining limitations

- **No actual Vercel/Render deployment was performed or could be** — this
  sandbox has no such accounts. `docs/DEPLOYMENT.md` follows both
  platforms' documented conventions precisely and every claim in it that
  *could* be checked locally was (backend startup, health/ready
  responses, CORS enforcement, the Postgres path, the built-bundle URL
  inlining) — but "matches the docs" and "confirmed working on a live
  Vercel/Render account" are different claims, and only the former is
  made here.
- **PostgreSQL was tested against a local server, not a managed cloud
  instance** (Render Postgres, Neon, etc.). The connection mechanics
  (psycopg2, `DATABASE_URL` parsing, SSL requirements some managed
  providers enforce by default) were not exercised against a
  TLS-enforcing managed provider specifically — worth a smoke test against
  your actual chosen provider before relying on it for a real deployment.
- **PyTorch/real ML inference was not runnable in this sandbox** at all
  across any of the three rounds: the default PyPI Linux wheel requires
  several GB of CUDA dependencies just to `import`, even for CPU-only use,
  and this sandbox has no route to a genuine CPU-only wheel index. What
  *was* verified live is the safety property that matters most — the app
  never pretends inference happened without a loaded model — but the
  actual forward pass through the real checkpoint files has not been
  exercised here.
- **Rate limiting was deliberately not implemented** — judged out of
  scope for a judged hackathon demo with a small, known audience (see
  `docs/DEPLOYMENT.md` §7); recommended mitigation for a genuinely public
  deployment is an edge/CDN layer (Vercel/Cloudflare) rather than an
  in-app dependency.
- **Antimeridian/dateline wrapping**: exists and is tested (round 3's
  `destination_point` + `test_destination_point_wraps_correctly_across_the_antimeridian`),
  reviewed but not independently re-verified against a real trans-Pacific
  scenario here, since this app's realistic operating envelope (Arabian
  Sea) never approaches it.
- **`init_db()`'s startup check is synchronous** and runs once at process
  start; a Postgres server that goes down *after* a successful startup
  and later `/ready` calls will be caught by `/ready`'s live check (it
  re-runs `init_db()` each time), but nothing actively polls this in the
  background — acceptable for this project's request-driven architecture,
  worth knowing if you build alerting on top of `/ready`.

---

## E. Exact commands to run the final application

### Local SIH demo (no cloud, no Postgres, no ML checkpoints required)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```
Open the URL Vite prints (typically `http://localhost:5173`). Click
**RUN DEMO INVESTIGATION** — this requires no internet connectivity, no
database configuration, and no ML checkpoints.

### Running the test suites

```bash
# Backend
cd backend
pytest -q                          # 289 passed, 14 skipped (torch-gated) expected

# Frontend
cd frontend
npx vitest run                     # 38 passed expected
npm run build                      # should complete with one known chunk-size warning
```

### Optional: PostgreSQL locally (to try the deployment-config path yourself)

```bash
# Requires a running PostgreSQL server. Example using apt (Debian/Ubuntu):
sudo apt-get install postgresql postgresql-contrib
sudo -u postgres createdb mpi_test
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"

cd backend
pip install -r requirements-postgres.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mpi_test
uvicorn app.main:app --port 8000
# Check the startup log for "database_ready" (backend: postgresql), and:
curl http://localhost:8000/ready

# Run the Postgres-specific tests against it:
export TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/mpi_test
pytest tests/test_persistence_postgres.py -v
```

### Cloud deployment (Vercel + Render)

See `docs/DEPLOYMENT.md` for the full walkthrough (build/start commands,
every environment variable, CORS setup, and a troubleshooting table). In
brief:

```bash
# Render (backend) - configure in the Render dashboard, not via CLI here:
#   Root directory: backend
#   Build command:  pip install -r requirements.txt
#   Start command:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
#   Env vars: CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app
#             (+ DATABASE_URL if using Postgres, AUTH_SECRET_KEY if AUTH_ENABLED=true)

# Vercel (frontend) - configure in the Vercel dashboard:
#   Root directory: frontend
#   Framework: Vite (build: npm run build, output: dist)
#   Env var: VITE_API_BASE_URL=https://your-backend.onrender.com/api
```
