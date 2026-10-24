# Remediation Log: Marine Pollution Intelligence

**Audit Date**: 2026-10-24
**Current Branch**: `main`
**Base Commit**: `f98a4167cf7ea3900ca8e52dbbfa50fbc184083a`

---

## Remediation Iterations

| Iteration # | Finding ID | Description | Root Cause | Changed Files | Regression Test | Outcome / Evidence |
|---|---|---|---|---|---|---|
| 1 | SEC-01 | Re-analyzed AIS/Attribution Candidates Not Persisted to Database | `analyze_ais()` and `attribution_analyze()` mutated in-memory `inv` without persisting | `backend/app/main.py` | `test_ais_analyze_persists_to_database`, `test_attribution_analyze_persists_to_database` | PASSED. Re-analysis parameters survive cache wipe and backend restart. |
| 2 | SEC-02 | Cross-User AIS Upload State Bleed & File Unlink Race | Global `_LAST_UPLOADED_AIS` shared between all users; unlinking deleted other users' active temp files | `backend/app/main.py` | `test_multi_user_ais_upload_isolation` | PASSED. AIS uploads scoped per user with fallback for backwards compatibility. User A's files preserved. |
| 3 | SEC-03 | Missing Ownership Filter in Investigation Listing (`/api/investigations`) | `list_investigations(owner=None)` returned all users' investigations to any authenticated caller | `backend/app/main.py` | `test_investigations_listing_ownership_filter` | PASSED. Users only receive their own investigations under `auth_enabled=True`. |
| 4 | DB-01 | PostgreSQL `IntegrityError` Crash in User Registration | Catch block only caught `sqlite3.IntegrityError`; PostgreSQL raised uncaught `psycopg2.IntegrityError` | `backend/app/auth.py` | `test_duplicate_user_raises_auth_error_not_unhandled_crash` | PASSED. Catches database-agnostic integrity errors and converts to structured `AuthError`. |
| 5 | PERF-01 | Unbounded Memory Growth in `_STORE` | Monotonically growing dictionary held all investigations and preview images indefinitely | `backend/app/investigation/orchestrator.py` | `test_bounded_investigation_store_eviction` | PASSED. Implemented `_BoundedInvestigationStore` LRU cache (100 entries); evicts cleanly with on-demand DB reload. |
| 6 | DATA-01 | Incomplete JSON Sanitization in Rehydrated Investigation | `RehydratedInvestigation.to_dict()` lacked `_sanitize_for_json()` wrapper | `backend/app/investigation/orchestrator.py` | `test_rehydrated_investigation_sanitizes_non_finite_values` | PASSED. Non-finite values safely clamped to 0.0 in rehydrated objects. |
| 7 | UX-01 | React `setState` in `useEffect` & Non-Component Export Warnings | Synchronous `setResolvedTheme` in effect triggered cascading renders; non-component export broke Fast Refresh | `frontend/src/lib/theme.jsx` | `npm --prefix frontend run lint` | PASSED. Converted to `useSyncExternalStore`. 0 warnings and 0 errors across 40 files. |
| 8 | UX-02 | Recharts Dimension Warning in Headless Runner | Missing explicit min dimensions in `ResponsiveContainer` | `frontend/src/components/tabs/MlTrainingTab.jsx` | `npm --prefix frontend test` | PASSED. Added `minWidth={0} minHeight={240}`. All 52 frontend tests passing. |

---

## Collateral Risk & Safety Checks
- Git controls verified: Zero hard resets, zero force-pushes, zero credential exposure.
- No existing tests were disabled, modified to weaken assertions, or skipped.
- Full regression suite of 450 backend tests and 52 frontend tests passed completely.
