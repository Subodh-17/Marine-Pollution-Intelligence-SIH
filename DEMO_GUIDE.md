# DEMO GUIDE — exact SIH demonstration sequence

## Setup (before the judges arrive)

```bash
# Terminal 1
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --port 8000

# Terminal 2
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. No further setup needed — the demo scenario is fully offline
and deterministic (the map's base tiles are the only piece that needs internet).

## The narrative (Section 39 of the brief)

1. **Open the console.** You land on an empty state: *"Detect the slick. Trace the origin. Rank
   the vessels."* — this is deliberate; it frames what's about to happen before you click
   anything. Point out the **theme toggle** in the header (sun/moon/monitor icons, top-right) —
   click through Light → Dark → System once to show the whole console re-themes instantly, with
   no flash and no lost state; leave it on whichever theme suits the room's lighting for the
   rest of the demo.

2. **Click "▶ RUN DEMO INVESTIGATION"** (top-right, or the empty-state button). Narrate while
   the staged progress checklist plays: *"This is running the real pipeline — SAR detection,
   drift hindcasting, and AIS attribution — against a deterministic offline scenario so the demo
   never depends on the venue's Wi-Fi."*

3. **Dashboard tab loads automatically.** Point out:
   - The **Data Source Indicator** strip — call out that it honestly labels the SAR scene and
     AIS feed as **"Demo Scenario"** (amber dot = synthetic; hover any chip for the full
     technical provenance string, e.g. `SYNTHETIC_DEMO`), and explain in one sentence *why* (no
     network access to Zenodo/MarineCadastre from the dev sandbox — see `DATA_PIPELINE.md`).
   - The **KPI cards**: detection confidence, spill area, estimated age, probable origin,
     candidate count, top candidate.
   - The **map**: toggle layers live — slick polygon (red), hindcast track + probable origin
     (amber, dashed), forecast track (violet, dashed), AIS candidate tracks (colored by rank),
     search radius (grey circle).

4. **Satellite tab.** Show the three-panel image chain: original SAR → despeckled/normalized →
   detection overlay. Point at the geometry table and read out area/length/width/orientation.
   Show the age estimate panel and read the caveat text aloud — *this is the "don't fabricate
   certainty" requirement made visible*.

5. **AIS tab.** Drag the **search radius** and **time window** sliders, click **Apply filters**,
   and watch the candidate funnel and map update live. This demonstrates the spatial+temporal
   filtering pipeline is real, not hardcoded — narrowing the radius to near-zero drops the
   candidate count to zero; widening it brings back all three named vessels.

6. **Ranking tab.** Point at the ranked table — **MV KONKAN STAR** should be #1. Click its row.

7. **Vessel detail drawer.** This is the payoff screen. Read the "why this vessel ranked #1"
   evidence bullets aloud, then walk through the score breakdown bars (spatial/temporal/
   trajectory/heading/behaviour/continuity) — every number is traceable to a specific piece of
   AIS evidence. Explicitly say the attribution-language line: *"highest-priority candidate,
   not a legal finding."*

8. **Timeline tab.** Show the drift-stage chain (T-24h → ... → PROBABLE ORIGIN → SATELLITE
   DETECTION → ... → T+48h) and the vessel-closest-approach axis beneath it.

9. **Report tab.** Click **Download PDF** — hand the judges a real, generated investigation
   report with all 15 required sections, closing with the Limitations section read verbatim.

## If a judge asks "is this real data?"

Answer directly: *"The pipeline — detection, drift physics, AIS filtering, scoring — is fully
real and unit-tested. The **inputs** in this demo are synthetic because this development
sandbox has no network access to Zenodo or MarineCadastre; the code is written against their
real, documented formats so a live extract drops in with zero changes — see `DATA_PIPELINE.md`
and `ARCHITECTURE.md`'s extension-points table."* This honesty is itself a feature, not a
weakness — Section 37 of the brief explicitly asks for scientific honesty over fabricated
certainty.

## Optional: "Use my AIS data"

If you have a real MarineCadastre CSV extract handy, click **"Use my AIS data"** in the header,
upload it, and re-run — it flows through the identical filtering/scoring/ranking pipeline
against the same synthetic SAR-derived origin point.

## Optional: authentication demo (API-only, off by default)

Auth is deliberately **off** (`AUTH_ENABLED=false`) for the standard demo above — nothing here
changes unless you turn it on. To show it working (there is no frontend login screen yet, so
this is a curl/API-docs walkthrough):

```bash
# Terminal 1 — start the backend with auth enabled
AUTH_ENABLED=true AUTH_SECRET_KEY=demo-only-secret uvicorn app.main:app --port 8000

# Log in as the seeded demo account (demo / demo1234 by default)
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234"}'
# → {"access_token": "...", "token_type": "bearer", "expires_in_seconds": 7200}

# Without a token, protected routes now 401:
curl -i -X POST "http://localhost:8000/api/spill/detect?mode=demo"
# → 401 Missing or invalid Authorization header.

# With the token, they work exactly as before, and the investigation is now owned by "demo":
curl -X POST "http://localhost:8000/api/spill/detect?mode=demo" \
  -H "Authorization: Bearer <token-from-above>"
```

Register a second user and confirm they get a `404` (not `403` — deliberately indistinguishable
from "doesn't exist") when requesting the first user's investigation id, to demonstrate
per-owner isolation.

## Optional: background jobs demo

```bash
curl -X POST "http://localhost:8000/api/jobs/investigation?mode=demo"
# → {"id": "...", "status": "queued", ...}

curl http://localhost:8000/api/jobs/<job-id>
# → poll until "status": "completed", then read result_investigation_id
```

The synchronous `POST /api/spill/detect?mode=demo` button in the actual demo UI is unaffected —
this is an additive API for callers who specifically want async behavior.

## Fallback if the frontend won't start

The backend alone is a complete demo via its interactive API docs:

```
http://localhost:8000/docs
```

`POST /api/spill/detect?mode=demo` returns the full investigation JSON in under a second — you
can walk judges through the same narrative reading raw JSON if absolutely necessary.
