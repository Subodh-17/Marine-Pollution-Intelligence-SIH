# Deployment Guide

Deploying the React/Vite frontend to **Vercel** and the FastAPI backend to
**Render** (or any equivalent VPS/PaaS that runs a long-lived process -
Railway, Fly.io, a plain systemd unit on a VPS, etc. all work the same
way, since none of this depends on a Render-specific feature).

**Honesty note on what's verified here.** Everything below that can be
checked without an actual Vercel/Render account has been: the backend
starts, `/health` and `/ready` respond, CORS is enforced when configured,
and PostgreSQL persistence genuinely works against a real local
PostgreSQL 16 server (see `backend/tests/test_persistence_postgres.py`).
What has **not** been verified is this exact configuration against a real
Vercel or Render account, since this development environment has no such
accounts and no route to sign up for one - the settings below follow each
platform's own documented conventions precisely, but "I followed the
docs correctly" and "I watched it deploy successfully" are different
claims, and this is the former. Budget time for first-deploy
troubleshooting accordingly, and see "Troubleshooting" below for the
failure modes this codebase's own design anticipates.

If you don't need any of this - the SIH demo runs entirely on localhost
with zero cloud dependency - see the root `README.md`'s "Quickstart"
instead. Nothing here is required for the local demo to work.

---

## 1. Architecture summary

```
 ┌─────────────────────┐        HTTPS         ┌──────────────────────────┐
 │  Vercel              │  ──────────────────▶ │  Render (or any VPS)     │
 │  React/Vite static   │   VITE_API_BASE_URL   │  FastAPI + uvicorn       │
 │  build (frontend/)   │ ◀──────────────────── │  (backend/)              │
 └─────────────────────┘     JSON responses     └────────────┬─────────────┘
                                                               │
                                                    DATABASE_URL (optional)
                                                               │
                                                               ▼
                                                  ┌──────────────────────┐
                                                  │  PostgreSQL          │
                                                  │  (Render/Neon/etc.)  │
                                                  │  - or SQLite file on │
                                                  │    Render's own disk │
                                                  │    if you skip this  │
                                                  └──────────────────────┘
```

Two independently-deployed services, talking over plain HTTPS/JSON - no
shared filesystem, no server-side rendering, no WebSocket dependency.

---

## 2. Backend on Render

### Build & start commands

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` (add `-r requirements-postgres.txt` too if using `DATABASE_URL`). **Do not add `-r requirements-ml.txt`** - that installs PyTorch, which production does not need and Render Free cannot fit; ML inference runs on ONNX Runtime CPU from the base requirements - see §5 |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |

`$PORT` is injected by Render (and most PaaS providers) at runtime - do
not hardcode a port. `--host 0.0.0.0` is required; the local-dev default
of binding only to `127.0.0.1` (see `vite.config.js`'s comment on this
exact IPv4/IPv6 distinction) would make the service unreachable from
Render's own load balancer.

### Environment variables

Copy the relevant lines from the repo root's `.env.example`. At minimum
for a working deployment:

| Variable | Example / recommended production value | Why |
|---|---|---|
| `APP_ENV` | `production` | Informational (`GET /health`'s `env` field); does not itself change behavior. |
| `CORS_ALLOWED_ORIGINS` | `https://your-frontend.vercel.app` | **Do not leave this as `*` in production** - see §4. Comma-separate multiple origins (e.g. a Vercel preview-deployment domain too). |
| `DATABASE_ENABLED` | `true` | Keeps investigations across restarts/redeploys. |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/dbname` | Only if using Postgres (§3). Omit entirely to fall back to a SQLite file on Render's own disk - see the caveat below. |
| `AUTH_ENABLED` | `false` for a public demo, `true` if you want the login gate | See §6. |
| `AUTH_SECRET_KEY` | a real random secret, generated once | **Required** if `AUTH_ENABLED=true` - see §6 for how to generate one. Never commit this; set it only in Render's dashboard. |
| `MAX_UPLOAD_SIZE_MB` | `50` (default) or lower for a memory-constrained instance | See §7. |

Everything else in `.env.example` has a working default and does not need
to be set for a basic deployment.

**SQLite-on-Render caveat:** Render's free/starter web service plans use
an **ephemeral filesystem** - anything written to disk (including a
SQLite file at the default `DATABASE_PATH`) is wiped on every redeploy
and on some restarts. If you don't set `DATABASE_URL`, the app will still
run correctly (investigations just won't survive a redeploy - the
in-memory store, and thus the live demo, is completely unaffected either
way). For persistence across redeploys, either set `DATABASE_URL` (§3) or
attach a [Render persistent disk](https://render.com/docs/disks) and
point `DATABASE_PATH` at a path inside it.

### Health & readiness

Configure Render's health check to hit `GET /health` (liveness - "is the
process up at all", never blocked by ML/DB state). `GET /ready` is
available separately for your own monitoring/alerting if you want a
richer signal (it reports `ml_available` and a per-component `checks`
dict, including database connectivity, without ever marking the service
"not ready" just because an optional component like ML or the database is
degraded - see the comment above `/ready` in `app/main.py` for why that's
a deliberate choice, not an oversight).

---

## 3. PostgreSQL setup

Only needed if you want investigations to survive redeploys/restarts
without a Render persistent disk. Skip this entire section to run on
SQLite (the default) instead - nothing else about the deployment changes.

1. Create a Postgres instance - Render's own managed Postgres, or any
   provider (Neon, Supabase, a self-hosted server) that gives you a
   connection string.
2. Set `DATABASE_URL` on the backend service to that connection string
   (`postgresql://user:password@host:5432/dbname`).
3. Add `psycopg2-binary` to the build: either add
   `-r requirements-postgres.txt` to the build command, or
   `pip install -r requirements.txt -r requirements-postgres.txt`.
4. No manual migration step is needed - `init_db()` runs automatically at
   application startup (see `app/main.py`'s lifespan handler) and creates
   the schema (`CREATE TABLE IF NOT EXISTS ...`) if it doesn't already
   exist. This is idempotent and safe to run on every deploy.
5. A broken `DATABASE_URL` (wrong host/credentials/network) does **not**
   crash the app at startup - by this project's own design (see
   `app/persistence/db.py`'s module docstring), persistence failures
   degrade gracefully rather than taking down an otherwise-working demo.
   It DOES log a clear `database_unavailable_at_startup` error - check
   Render's deploy logs after any change to `DATABASE_URL`.

This was developed and tested against a real local PostgreSQL 16 server
(`backend/tests/test_persistence_postgres.py`, 7 tests covering round-trip
save/load, the `ON CONFLICT` upsert specifically, restart-survival, and a
deliberately-broken connection string) - it is not speculative code
written only against psycopg2's documentation.

---

## 4. Frontend on Vercel

### Build settings

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework preset | Vite |
| Build command | `npm run build` (Vercel's default for Vite) |
| Output directory | `dist` (Vercel's default for Vite) |

### Environment variables

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://your-backend.onrender.com/api` (your Render backend's URL, **including** the `/api` suffix) |

Set this under Vercel's Project Settings → Environment Variables, scoped
to at least the **Production** environment (add it to Preview too if you
want preview deployments to hit the same backend). This is read only at
**build** time - Vite inlines `import.meta.env.VITE_API_BASE_URL` into
the built JS bundle, so changing it in Vercel's dashboard requires a new
deployment (Vercel does this automatically on the next push; it will not
retroactively apply to an already-built deployment without one).

Leaving `VITE_API_BASE_URL` unset in a Vercel deployment is a supported
but **not recommended** configuration: the app falls back to a relative
`/api`, which resolves against Vercel's own domain (where nothing is
listening) and fails obviously in the browser's Network tab - a clearer
failure than silently defaulting to `localhost`, but still a
misconfiguration you'll want to fix by setting the variable above.

### CORS is bidirectional - remember to update the backend too

Setting `VITE_API_BASE_URL` alone is not sufficient: the Render backend
must also have `CORS_ALLOWED_ORIGINS` including your exact Vercel domain
(§2). A request that reaches the right URL but gets a CORS rejection
looks, from the browser, like "the app is broken" with no visible HTTP
error in the UI - always the first thing to check (browser DevTools →
Network tab → look for a CORS error specifically, distinct from a 4xx/5xx
response) if the deployed frontend can't reach the deployed backend.

---

## 5. ML inference in a cloud deployment (ONNX Runtime CPU)

This project never ships, downloads, or retrains model weights - see the
hard constraints in `MODEL_CARD.md` and the root `README.md`.

**Production does not need PyTorch.** The trained SpillUNet segmenter and
SceneClassifier are served through ONNX Runtime's CPU execution provider
(`app/ml/onnx_engine.py`). PyTorch remains an offline-only dependency
(`requirements-ml.txt`) used for training, evaluation, and the one-time
ONNX export - never for serving. This is what makes genuine ML inference
fit on a 512 MB / 0.1 CPU Render Free web service, where the default PyPI
`torch` wheel (which pulls in multiple GB of CUDA runtime dependencies even
for CPU-only use) does not fit at all.

### What is actually shipped

- The **real trained checkpoints** (`*_real_promoted.pth` / `.pt`), which
  were trained previously on the Zenodo Sentinel-1 SAR Oil Spill Dataset.
- The **exported ONNX graphs** (`classifier_real_best.onnx`,
  `spill_unet_real_best.onnx`) plus `onnx_export_manifest.json`.
- **Not** the Zenodo training dataset itself. It is never bundled,
  downloaded, or required at runtime (see `docs/DATASET_SETUP.md`).

### Producing the ONNX artifacts (one-time, local/CI, needs PyTorch)

```bash
pip install -r requirements.txt -r requirements-ml.txt
cd backend
python -m app.ml.export_onnx     # exports from the promoted checkpoints
python -m app.ml.verify_onnx     # ONNX CPU checks + torch-vs-ONNX parity
```

`export_onnx.py` reads the same checkpoint precedence list the runtime
uses, so it always exports whatever the engine would actually serve. It
does not modify the source `.pth`/`.pt` files. Commit the resulting
`.onnx` files and the manifest.

### Render configuration

- Build command stays `pip install -r requirements.txt`. **Do not** add
  `-r requirements-ml.txt` on Render - that is the PyTorch install, and it
  is neither needed nor affordable there.
- `onnxruntime` is in the base `requirements.txt` and is a small CPU-only
  wheel.

### Provenance is verified, not assumed

An `.onnx` file existing on disk proves nothing about how it was trained.
Before the runtime will report `ML_REAL_ZENODO_TRAINED`, it requires the
export manifest to record the real-Zenodo training marker AND re-hashes the
source checkpoint on disk to confirm it still matches the bytes that were
exported. A stale or unexplained ONNX file is refused outright and the
service falls back to the classical detector. `ML_REAL_ZENODO_TRAINED` is
only ever attached to a result after a forward pass actually returned a
finite, correctly-shaped output.

### Memory budget (measured, not estimated)

Peak resident memory for one forward pass through both models, measured
with a single-threaded, arena-disabled CPU session:

| Scene size | Peak RSS | Latency (fast dev CPU) |
|---|---|---|
| 256x256 | ~98 MB | ~130 ms |
| 512x512 | ~219 MB | ~700 ms |
| 768x768 | ~423 MB | ~1.3 s |
| 1024x1024 | ~709 MB | ~2.1 s |

Render Free caps at 512 MB for the whole container, so **1024x1024 scenes
will OOM-kill the service.** `ML_MAX_INFERENCE_PIXELS` (default
`262144`, i.e. 512x512) guards this: larger scenes are routed to the
documented classical fallback with an explicit reason rather than risking
an out-of-memory kill that would take down every other endpoint too. Raise
it (or set `0` to disable) only on a host with more RAM. Note 0.1 CPU is
heavily throttled - expect latencies well above the table above on Render
Free.

### Dual-polarization requirement is unchanged

Genuine ML inference requires real dual-pol VV + VH input. A single-band
upload is **not** channel-duplicated to fake a second polarization - it
uses the classical dark-spot detector and records why ML was unavailable.

### If ONNX is unavailable

Without `onnxruntime`, without the `.onnx` artifacts, or if a session fails
to load, the app runs correctly on the classical detector.
`GET /api/ml/status` honestly reports `available: false`, `runtime: null`,
and the specific load errors rather than pretending inference happened.
This is not a deployment failure mode to fix; it's the intended, safe
default.

### Segmentation threshold

`ML_SEGMENTATION_THRESHOLD` (default `0.5`) — the sigmoid decision boundary
for the binary oil/background mask. This is the one real, tunable
threshold this architecture has; see `README_ML_STATUS.md` for why no
lookalike-specific threshold was invented alongside it. **Never derive a
new value for this from the held-out test set** — that's the test-set
threshold-tuning the project's own rules prohibit; a validation-set
calibration experiment is still outstanding (documented, not silently
assumed done).

### Investigation provenance (Phase 21)

Every `real_satellite`-mode investigation now records, in
`Investigation.config_used`: `app_version`, `detector_version`,
`input_file_sha256` (streamed while the upload is written to disk — no
second read of the file), and, via `satellite.model_provenance_info`:
`inference_runtime` (`onnxruntime_cpu`/`pytorch`), `segmentation_threshold`,
checkpoint filenames + sha256, `preprocessing_version`, and `dataset_dois`.
The last two now populate correctly on the ONNX runtime path too — a real
gap found this round: `model_info` was silently `None` for them there
because the ONNX path never `torch.load()`s the raw checkpoint dict the
PyTorch path reads them from; fixed by falling back to the same sidecar
JSON (`spill_unet_real_model_metadata.json`) both paths already ship with.

### Runtime selection

`ML_RUNTIME=auto` (default) prefers ONNX and falls back to PyTorch when the
ONNX artifacts are absent - so a developer checkout that has never run the
export behaves exactly as it did before. `onnx` or `pytorch` force either
path explicitly.

### Checkpoint filename overrides

Inserting a real checkpoint under a filename other than the default
`*_real_promoted*` / `*_real_best.onnx` names it expects doesn't require
editing source - set the matching env var and it's tried first:

| Env var | Overrides |
|---|---|
| `ML_CLASSIFIER_CHECKPOINT` | classifier `.pth` filename (in `ML_CHECKPOINT_DIR`) |
| `ML_SEGMENTER_CHECKPOINT` | segmenter `.pt` filename |
| `ML_CLASSIFIER_ONNX` | classifier `.onnx` filename |
| `ML_SEGMENTER_ONNX` | segmenter `.onnx` filename |

All four are unset by default, which reproduces the exact original
`*_real_promoted*` / `*_real_best.onnx` precedence.

### Large-scene inference (tiling)

Scenes above the direct-inference budget are tiled rather than refused -
see `app/ml/tiling.py`.

| Env var | Default | Meaning |
|---|---|---|
| `ML_MAX_INFERENCE_PIXELS` | `262144` (512x512) | scenes at/below this run in one direct pass, exactly as before tiling existed |
| `ML_TILING_ENABLED` | `true` | set `false` to restore the old behavior (refuse anything over the direct budget) |
| `ML_TILE_SIZE` | `256` | matches `ML_TRAIN_IMAGE_SIZE`; must be divisible by 4 |
| `ML_TILE_OVERLAP` | `32` | pixels of overlap between adjacent tiles, averaged at the seam |
| `ML_MAX_TILED_INFERENCE_PIXELS` | `16777216` (4096x4096) | outer ceiling even with tiling on - bounds total tile count/request time, not just per-tile memory; **estimated, not measured on Render** |

The classifier is never tiled (a scene-level Oil/No-Oil/Lookalike decision
can't be meaningfully averaged from tile-level answers) - above the direct
budget it instead classifies a block-mean-downsampled whole-scene view, and
the result's `notes` say so explicitly.

---

## 6. Authentication in production

Disabled by default (`AUTH_ENABLED=false`) so the demo runs with zero
configuration. To enable it for a real deployment:

1. Generate a real random secret - do not use the `.env.example`
   placeholder value:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Set `AUTH_ENABLED=true` and `AUTH_SECRET_KEY=<that value>` on the
   Render backend. Never commit this value or put it in
   `VITE_API_BASE_URL`-style frontend env vars (it must never reach the
   browser).
3. Leave `AUTH_ALLOW_REGISTRATION` as documented in `.env.example` and
   change `DEMO_USERNAME`/`DEMO_PASSWORD` away from their defaults if
   registration is disabled and you're relying on the seeded demo
   account for the SIH presentation itself.
4. The existing frontend login flow (see `DEMO_GUIDE.md` and
   `app/auth.py`) already handles the token-based flow this needs; no
   frontend code changes are required to turn auth on or off, only the
   backend environment variables above.

For the SIH demo presentation itself, running with `AUTH_ENABLED=false`
(the default) is the recommended configuration - one less thing to go
wrong in front of judges, and it's an intentional, documented "local demo
mode never requires authentication" design goal of this project.

---

## 7. Production safeguards already in place

- **Upload limits**: `MAX_UPLOAD_SIZE_MB` (default 50) is enforced by
  streaming byte count as the upload is read, not by trusting the
  client's `Content-Length` header - see `app/main.py`'s
  `_save_upload_with_size_limit`. Lower this for a memory-constrained
  instance.
- **File type/extension validation**: `ALLOWED_AIS_EXTENSIONS` /
  `ALLOWED_SATELLITE_EXTENSIONS`, plus content-level validation (a
  renamed non-image file fails at the PIL/tifffile read step with a
  clear 422, not a 500).
- **Request IDs**: every response includes an `X-Request-ID` header,
  and every log line for that request is tagged with it - useful for
  correlating a user-reported error with your platform's log viewer.
- **Rate limiting**: **not implemented** as a dedicated middleware/library
  in this codebase. For a judged hackathon demo with a known, small
  audience this was judged out of scope (see "not over-engineering" in
  the upgrade brief this document was written against); if you deploy
  this somewhere genuinely public, put it behind a reverse proxy or CDN
  (Vercel/Cloudflare both offer this at the edge for free) rather than
  adding an in-app dependency for it.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Frontend loads but every request fails, Network tab shows the request going to the *Vercel* domain instead of Render | `VITE_API_BASE_URL` wasn't set at build time | Set it in Vercel's dashboard and trigger a new deployment (§4) |
| Frontend request reaches the right backend URL but fails with a CORS error in DevTools | Backend's `CORS_ALLOWED_ORIGINS` doesn't include the exact Vercel domain | Update `CORS_ALLOWED_ORIGINS` on Render and redeploy (§2) - must match scheme+host exactly, including `https://` |
| Backend deploy "succeeds" on Render but `/health` never responds | Start command missing `--host 0.0.0.0`, or hardcoded a port instead of `$PORT` | Fix the start command (§2) |
| Investigations disappear after every redeploy | Using the SQLite default on an ephemeral filesystem | Set `DATABASE_URL` (§3) or attach a Render persistent disk |
| `database_unavailable_at_startup` in Render logs | Wrong `DATABASE_URL` credentials/host, or Postgres instance not yet reachable when the backend started | Verify the connection string; the app itself keeps running in degraded (non-persistent) mode either way, so this is a data-durability issue, not a demo-blocking one |
| `GET /api/ml/status` shows `torch_available: false` even though you deployed with checkpoints | `requirements-ml.txt` wasn't installed, or the instance ran out of memory/disk installing PyTorch's CUDA dependencies | Confirm the build log actually installed torch successfully; see §5 |
| Login fails immediately after enabling `AUTH_ENABLED=true` | `AUTH_SECRET_KEY` not set (or changed since tokens were issued) | Set a real secret (§6) - do this *before* any user logs in, since changing it invalidates all previously-issued tokens |
