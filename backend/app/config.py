"""
Centralized runtime configuration.

Every value here can be overridden by an environment variable (see
`.env.example` at the project root for the full list, with descriptions,
units, and defaults). Every value also has a safe, working default, so the
application runs out of the box with **zero required configuration** - env
vars are an override mechanism, never a requirement.

This module deliberately contains ONLY plain, non-secret operational knobs
(timeouts, size limits, thresholds, feature toggles). It never reads or
stores API keys/secrets, and nothing in this codebase currently needs one
(see `.env.example` for why the map-tile/live-data provider slots are
present but unused). If a live data source requiring a credential is ever
wired in, that credential must be added as a new env var here and must
never be hardcoded or committed.

Values are parsed once (see `get_settings()` below) and cached, so
environment variables are read a single time at first use rather than on
every request - this is also a small, deliberate performance property (no
repeated `os.environ` / string-parsing work per request), consistent with
the "avoid unnecessary repeated work per request" production-readiness
goal that motivates the ML model-manager caching in `app/ml/detector.py`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env_str(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_str_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    return tuple(item.strip() for item in val.split(",") if item.strip())


def _env_float_tuple(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    try:
        parsed = tuple(float(item.strip()) for item in val.split(",") if item.strip())
        return parsed if parsed else default
    except ValueError:
        return default


def _env_optional_str(name: str) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else None


# Named (not just inlined) so app/main.py's startup check can compare
# against this exact value without duplicating the string - see the
# lifespan handler's insecure-secret warning.
INSECURE_DEFAULT_AUTH_SECRET_KEY = "dev-only-insecure-default-secret-change-me-for-any-real-deployment"

# Restrictive production allowlist: deployed Vercel frontend origin and
# explicitly permitted local development origins (Workstream A).
DEFAULT_CORS_ALLOWED_ORIGINS: tuple[str, ...] = (
    "https://marine-pollution-intelligence-sih.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _resolve_default_app_env() -> str:
    """Resolves environment identity without hardcoding a misleading status.
    Explicit APP_ENV always wins; falls back to production when running on
    Render (RENDER=true or RENDER_SERVICE_ID present) or when standard cloud
    environment indicators are present; defaults to 'development' locally."""
    val = os.environ.get("APP_ENV")
    if val not in (None, ""):
        return val.strip().lower()
    if os.environ.get("RENDER") == "true" or os.environ.get("RENDER_SERVICE_ID"):
        return "production"
    for candidate in ("ENVIRONMENT", "NODE_ENV"):
        c_val = os.environ.get(candidate, "").strip().lower()
        if c_val in ("production", "prod"):
            return "production"
    return "development"


@dataclass(frozen=True)
class Settings:
    """Snapshot of all configurable values, resolved once from the
    environment. See `.env.example` for the authoritative, documented list
    of every variable read here."""

    # ---- App / environment identity ----------------------------------
    app_env: str                       # "development" | "production" | "test"
    app_version: str
    log_level: str                     # DEBUG | INFO | WARNING | ERROR

    # ---- CORS -----------------------------------------------------------
    # Comma-separated list of allowed origins. Defaults to "*" to preserve
    # this prototype's existing (pre-hardening) behavior exactly - set
    # CORS_ALLOWED_ORIGINS explicitly for any real deployment.
    cors_allowed_origins: tuple[str, ...]

    # ---- Uploads ----------------------------------------------------------
    max_upload_size_mb: float
    allowed_ais_extensions: tuple[str, ...]
    allowed_satellite_extensions: tuple[str, ...]

    # ---- Processing ---------------------------------------------------------
    processing_timeout_seconds: float

    # ---- ML -----------------------------------------------------------------
    # Confidence below which the orchestrator discards an ML detection
    # result and falls back to the classical detector. See
    # app/ml/detector.py::ML_FALLBACK_CONFIDENCE_THRESHOLD for the full
    # rationale - this is a heuristic engineering cutoff, not a validated
    # operating point.
    ml_confidence_threshold: float
    ml_multiscale_segmentation: bool
    ml_multiscale_fusion_strategy: str

    # ---- AIS / attribution ---------------------------------------------------
    ais_search_radius_km_default: float
    ais_time_window_hours_default: float
    min_attribution_mask_px: int

    # ---- Drift hindcast/forecast duration -----------------------------------
    drift_backward_hours: tuple[float, ...]
    drift_forward_hours: tuple[float, ...]

    # ---- Map ------------------------------------------------------------------
    # Which basemap tile provider the frontend should try first. Purely
    # informational to the frontend (served back via GET /api/config);
    # the frontend already has its own provider fallback chain and this
    # only selects the preferred starting provider.
    map_tile_provider: str

    # ---- Persistence (Section 6) ---------------------------------------------
    # SQLite by default, zero extra infrastructure required locally. Set
    # DATABASE_PATH to override where the file lives (tests point this at a
    # pytest tmp_path). Set DATABASE_ENABLED=false to run fully in-memory
    # (e.g. some unit tests) - investigations then do NOT survive a restart.
    #
    # Set DATABASE_URL (a postgres:// or postgresql:// URL) to use a real
    # PostgreSQL server instead - e.g. for a cloud deployment where the
    # filesystem is ephemeral between deploys/restarts and SQLite's single
    # local file would not survive. When set, DATABASE_URL takes priority
    # over DATABASE_PATH entirely; DATABASE_ENABLED still governs whether
    # persistence happens at all, for both backends. See app/persistence/db.py
    # for the abstraction and docs/DEPLOYMENT.md for setup.
    database_enabled: bool
    database_path: str
    database_url: str | None

    # ---- Auth (Section 7) -----------------------------------------------------
    # Disabled by default so every existing route/demo/test keeps working
    # exactly as before with zero configuration. Set AUTH_ENABLED=true to
    # require a bearer token (via POST /api/auth/login) on investigation and
    # report/export routes - see app/auth.py and DEMO_GUIDE.md.
    auth_enabled: bool
    auth_secret_key: str
    auth_token_expire_minutes: float
    auth_demo_username: str
    auth_demo_password: str
    auth_allow_registration: bool

    # ---- Live AIS provider (Section 3) -----------------------------------------
    # Unset by default (no credentials/endpoint exist for this sandboxed
    # build - see app/ais/live_provider.py). When AIS_LIVE_PROVIDER_URL is
    # configured, mode="live_ais" attempts a real HTTP call before falling
    # back to the synthetic fleet.
    ais_live_provider_url: str | None
    ais_live_api_key: str | None
    ais_live_timeout_seconds: float
    ais_live_max_retries: int

    # ---- Live environmental data provider (Section 4) --------------------------
    env_live_provider_url: str | None
    env_live_api_key: str | None
    env_live_timeout_seconds: float
    env_live_max_retries: int
    live_data_max_age_minutes: float

    # ---- Upload hardening (Section 9) ------------------------------------------
    max_image_dimension_px: int

    # ---- Zenodo dataset integration -------------------------------------------
    # `enable_zenodo_data` remains a documented, inert flag: it is read nowhere
    # in the request-serving path and gates no runtime behavior. The synthetic
    # demo, classical detector, and shipped ML prototype all work fully offline
    # regardless of its value - training against the real Zenodo dataset is an
    # explicit, separate, offline CLI step (see docs/DATASET_SETUP.md,
    # app/ml/dataset.py, app/ml/train.py), never something the running API
    # server triggers on its own.
    enable_zenodo_data: bool

    # `dataset_root` points at where the user extracted the Zenodo Sentinel-1
    # SAR Oil Spill Dataset (Parts I-III) locally after downloading it
    # themselves - see docs/DATASET_SETUP.md. Read by the offline dataset
    # preparation / training / evaluation CLIs (app/ml/dataset.py, train.py,
    # evaluate_real.py), and also by the FastAPI app's read-only
    # `GET /api/ml/dataset-status` endpoint (see app/main.py) - but never at
    # import or server-startup time, so a missing/absent dataset never
    # affects `uvicorn app.main:app` startup or any other endpoint.
    # Deliberately NOT hardcoded to any developer's local machine path; the
    # default is a relative path so a fresh checkout has a sensible,
    # portable place to point a download at.
    dataset_root: str

    # How long `GET /api/ml/dataset-status` may serve a cached result before
    # re-running the expensive full filesystem + TIFF validation scan (see
    # app/ml/dataset.py::get_dataset_status_cached). Passing `?refresh=true`
    # (the ML Training page's "Refresh dataset status" button) always
    # bypasses this regardless of its value, and this setting is never read
    # anywhere except by that one endpoint - it has no effect on
    # `dataset_root` itself or on any offline CLI.
    dataset_status_cache_ttl_seconds: float

    # ---- ML training / evaluation (offline CLIs only, see app/ml/train.py,
    # app/ml/evaluate_real.py) - documented defaults for the real-data
    # training pipeline. None of these are read by the FastAPI app itself.
    ml_train_batch_size: int
    ml_train_epochs: int
    ml_train_lr: float
    ml_train_image_size: int
    ml_train_seed: int
    ml_checkpoint_dir: str

    @property
    def max_upload_size_bytes(self) -> int:
        return int(self.max_upload_size_mb * 1024 * 1024)


def _resolve_default_dataset_root() -> str:
    val = os.environ.get("DATASET_ROOT")
    if val not in (None, ""):
        return val
    for candidate in (
        os.path.join("data", "zenodo_sar"),
        os.path.join("backend", "data", "zenodo_sar"),
        r"D:\zenodo_sar",
    ):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join("data", "zenodo_sar")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the process-wide, cached Settings snapshot. Call
    `get_settings.cache_clear()` (e.g. in tests) to force re-reading the
    environment."""
    return Settings(
        app_env=_resolve_default_app_env(),
        app_version=_env_str("APP_VERSION", "0.1.0"),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        cors_allowed_origins=_env_str_list("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ALLOWED_ORIGINS),
        max_upload_size_mb=_env_float("MAX_UPLOAD_SIZE_MB", 50.0),
        allowed_ais_extensions=_env_str_list("ALLOWED_AIS_EXTENSIONS", (".csv",)),
        allowed_satellite_extensions=_env_str_list(
            "ALLOWED_SATELLITE_EXTENSIONS", (".tif", ".tiff", ".png", ".jpg", ".jpeg")
        ),
        processing_timeout_seconds=_env_float("PROCESSING_TIMEOUT_SECONDS", 60.0),
        ml_confidence_threshold=_env_float(
            # Back-compatible with the pre-existing env var name used
            # directly inside app/ml/detector.py.
            "ML_FALLBACK_CONFIDENCE_THRESHOLD", 0.5
        ),
        ml_multiscale_segmentation=_env_bool("ML_MULTISCALE_SEGMENTATION", False),
        ml_multiscale_fusion_strategy=_env_str("ML_MULTISCALE_FUSION_STRATEGY", "size_gated_closure").lower(),
        ais_search_radius_km_default=_env_float("AIS_SEARCH_RADIUS_KM_DEFAULT", 50.0),
        ais_time_window_hours_default=_env_float("AIS_TIME_WINDOW_HOURS_DEFAULT", 12.0),
        min_attribution_mask_px=_env_int("MIN_ATTRIBUTION_MASK_PX", 250),
        drift_backward_hours=_env_float_tuple("DRIFT_BACKWARD_HOURS", (6.0, 12.0, 18.0, 24.0)),
        drift_forward_hours=_env_float_tuple("DRIFT_FORWARD_HOURS", (6.0, 12.0, 24.0, 48.0)),
        map_tile_provider=_env_str("MAP_TILE_PROVIDER", "osm"),
        database_enabled=_env_bool("DATABASE_ENABLED", True),
        database_path=_env_str(
            "DATABASE_PATH",
            os.path.join(os.path.dirname(__file__), "..", "data", "app.db"),
        ),
        database_url=_env_optional_str("DATABASE_URL"),
        auth_enabled=_env_bool("AUTH_ENABLED", False),
        auth_secret_key=_env_str(
            "AUTH_SECRET_KEY",
            INSECURE_DEFAULT_AUTH_SECRET_KEY,
        ),
        auth_token_expire_minutes=_env_float("AUTH_TOKEN_EXPIRE_MINUTES", 120.0),
        auth_demo_username=_env_str("DEMO_USERNAME", "demo"),
        auth_demo_password=_env_str("DEMO_PASSWORD", "demo1234"),
        auth_allow_registration=_env_bool("AUTH_ALLOW_REGISTRATION", True),
        ais_live_provider_url=_env_optional_str("AIS_LIVE_PROVIDER_URL"),
        ais_live_api_key=_env_optional_str("AIS_LIVE_API_KEY"),
        ais_live_timeout_seconds=_env_float("AIS_LIVE_TIMEOUT_SECONDS", 10.0),
        ais_live_max_retries=_env_int("AIS_LIVE_MAX_RETRIES", 2),
        env_live_provider_url=_env_optional_str("ENV_LIVE_PROVIDER_URL"),
        env_live_api_key=_env_optional_str("ENV_LIVE_API_KEY"),
        env_live_timeout_seconds=_env_float("ENV_LIVE_TIMEOUT_SECONDS", 10.0),
        env_live_max_retries=_env_int("ENV_LIVE_MAX_RETRIES", 2),
        live_data_max_age_minutes=_env_float("LIVE_DATA_MAX_AGE_MINUTES", 180.0),
        max_image_dimension_px=_env_int("MAX_IMAGE_DIMENSION_PX", 8000),
        enable_zenodo_data=_env_bool("ENABLE_ZENODO_DATA", False),
        dataset_root=_resolve_default_dataset_root(),
        dataset_status_cache_ttl_seconds=_env_float("DATASET_STATUS_CACHE_TTL_SECONDS", 300.0),
        ml_train_batch_size=_env_int("ML_TRAIN_BATCH_SIZE", 4),
        ml_train_epochs=_env_int("ML_TRAIN_EPOCHS", 20),
        ml_train_lr=_env_float("ML_TRAIN_LR", 1e-3),
        ml_train_image_size=_env_int("ML_TRAIN_IMAGE_SIZE", 256),
        ml_train_seed=_env_int("ML_TRAIN_SEED", 42),
        ml_checkpoint_dir=_env_str(
            "ML_CHECKPOINT_DIR",
            os.path.join(os.path.dirname(__file__), "..", "data", "ml_checkpoints"),
        ),
    )
