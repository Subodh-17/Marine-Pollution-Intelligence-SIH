# ROUTE PLANNER AND FUEL VALIDATION REPORT
**Marine Pollution Intelligence — Spill-Aware Maritime Navigation & Hydrodynamic Fuel Estimation**
**Date:** 2026-10-30 | **Status:** Validated, Verified & Defects Resolved

---

## 1. System Overview

The **Spill-Aware Maritime Route Optimization & Fuel Intelligence System** addresses the critical Smart India Hackathon operational requirement: connecting spaceborne oil spill detection directly to active maritime navigation and environmental risk mitigation.

When a marine oil slick is detected, localized, and forecasted by the platform's SAR and drift engines, merchant vessels operating in the maritime corridor must not only be warned of potential collisions, but also provided with **hydrodynamically optimized, fuel-conscious diversion routes**.

---

## 2. Navigational Geometry & Routing Algorithms

### 2.1 Baseline Great Circle Path
- Baseline routes are calculated using WGS-84 geodesic arc geometry (Vincenty formulation).
- For coastal passages along the Indian subcontinent, routes are structured through approved navigational waypoints and Traffic Separation Schemes (TSS).

### 2.2 Spill Avoidance Vectoring
1. **Dynamic Spill Buffer Zones:** Detected oil slicks are expanded by an operator-defined safety buffer (default: 2.5 NM, adjustable from 0.5 to 10.0 NM) to account for oceanographic drift uncertainty, wave dispersion, and volatile hydrocarbon evaporation zones.
2. **Intersection Detection:** Segment-polygon intersection checks verify if any leg of the baseline trajectory penetrates the buffered exclusion polygon.
3. **Detour Path Generation:** When an intersection occurs, the algorithm calculates a convex-hull tangent diversion routing around the seaward side of the slick, avoiding navigational shoals while preserving international maritime lane clearance.

### 2.3 Verified Bug Fix: "Identical Distance" Resolution
- **Prior Behavior:** In scenarios where the direct baseline was already safe and did not penetrate the spill zone, the system copied the baseline into the avoidance slot and returned `recommended_route_type="spill_avoiding"` with `extra_distance_nm=0.0`. This created cognitive confusion for operators.
- **Resolved Architecture:** The engine now returns `recommended_route_type="baseline_safe"` and sets `spill_avoiding_route=None`. The UI displays a clear green confirmation banner explaining that the direct baseline already maintains full safety clearance, avoiding unnecessary detours.

---

## 3. Naval Hydrodynamic Fuel Modeling

Fuel consumption is calculated on first-principle naval architecture equations rather than arbitrary linear approximations:

### 3.1 Propulsion Power (Admiralty Formula)
Effective shaft power $P_{\text{prop}}$ (in kilowatts) required to overcome hydrodynamic resistance at speed $V$ (in knots) is given by:
$$P_{\text{prop}} = \frac{\Delta^{2/3} \cdot V^3}{C_{\text{adm}}}$$
Where:
- $\Delta$ = Vessel total displacement (deadweight tonnage + lightship weight, in metric tonnes).
- $V$ = Vessel speed through water (in knots).
- $C_{\text{adm}}$ = Admiralty coefficient (calibrated from 420 to 480 based on hull form and block coefficient $C_b$).

### 3.2 Total Fuel Consumption & Greenhouse Gas Output
Total fuel rate $\dot{m}_{\text{fuel}}$ (in metric tonnes per hour) includes propulsion and auxiliary generator hotel loads:
$$\dot{m}_{\text{fuel}} = \left( P_{\text{prop}} \cdot \text{SFOC}_{\text{main}} + P_{\text{aux}} \cdot \text{SFOC}_{\text{aux}} \right) \times 10^{-6}$$
Where:
- $\text{SFOC}$ = Specific Fuel Oil Consumption (standardized at 172 g/kWh for marine two-stroke diesel engines).
- Fuel Density = 0.860 kg/L (for standard ISO 8217 Low Sulphur Heavy Fuel Oil / MDO).
- $\text{CO}_2$ Emission Factor = $3.114 \text{ MT CO}_2$ per metric tonne of fuel consumed (IMO Fourth GHG Study).
- Bunker Fuel Cost = Standardized at $\$650 / \text{MT}$.

---

## 4. Multi-Vessel Profile Validation Matrix

The routing engine supports six distinct vessel classes spanning international merchant shipping:

| Vessel Class | Length / Beam / Draft | Displacement ($\Delta$) | Service Speed | Main Engine Power | Hourly Fuel Consumption | Fuel Capacity | Range at Service Speed |
|---|---|---|---|---|---|---|---|
| **Aframax Crude Tanker** | 245m / 42m / 14.5m | 115,000 DWT | 14.5 kts | 13,500 kW | 2.32 MT/hr | 2,800 MT | ~17,500 NM |
| **Capesize Bulk Carrier** | 292m / 45m / 18.0m | 180,000 DWT | 13.0 kts | 16,000 kW | 2.75 MT/hr | 3,200 MT | ~15,100 NM |
| **Ultra Large Container (ULCV)** | 399m / 61m / 16.0m | 165,000 DWT | 19.0 kts | 55,000 kW | 9.46 MT/hr | 7,500 MT | ~15,000 NM |
| **Feeder Container Ship** | 168m / 27m / 8.5m | 22,000 DWT | 16.0 kts | 8,500 kW | 1.46 MT/hr | 1,200 MT | ~13,100 NM |
| **Chemical / Product Tanker** | 183m / 32m / 11.0m | 45,000 DWT | 14.0 kts | 7,800 kW | 1.34 MT/hr | 1,600 MT | ~16,700 NM |
| **Offshore Patrol Vessel (OPV)** | 105m / 14m / 4.0m | 2,500 DWT | 20.0 kts | 12,000 kW | 2.06 MT/hr | 450 MT | ~4,300 NM |

---

## 5. End-to-End Voyage Scenario Test: Mumbai to Mormugao

A validation test was executed across the Mumbai–Goa tanker transit route with a detected oil spill in the coastal corridor:
- **Baseline Direct Sea Path:**
  - Distance: 218.4 NM | Duration: 15.06 hrs | Baseline Fuel: 34.94 MT ($\$22,711$)
  - Spill Interaction: **COLLISION HAZARD (Crosses Slick)**
- **Spill-Avoiding Detour Path (2.5 NM Safety Buffer):**
  - Distance: 232.1 NM | Duration: 16.01 hrs | Detour Fuel: 37.14 MT ($\$24,141$)
  - Spill Interaction: **SAFE (Maintains 2.5 NM clearance)**
- **Trade-Off Variance:**
  - Extra Distance: $+13.7 \text{ NM}$ ($+6.3\%$)
  - Extra Duration: $+0.95 \text{ hrs}$ (57 minutes)
  - Extra Fuel Consumed: $+2.20 \text{ MT}$ ($+2,558.1 \text{ Litres}$)
  - Extra Bunker Cost: $+\$1,430.00$
  - Additional $\text{CO}_2$ Emitted: $+6.85 \text{ MT}$

### Outcome:
The vessel circumvents the hazardous slicks, preventing contaminated seawater intake into cooling heat exchangers and marine bio-fouling, for an incremental operational cost of only 6.3%.

---

## 6. Statutory Disclaimer & Compliance

Every route calculation output includes the mandatory international maritime disclaimer:
> **NOTICE: ADVISORY DECISION SUPPORT ONLY.**
> *In compliance with SOLAS Chapter V, Regulation 34 and IMO Resolution A.893(21), passage planning must take into account all relevant meteorological, navigational, and environmental factors. This system provides automated decision support; the Master of the vessel retains sole authority and responsibility for navigational safety, speed selection, and collision avoidance.*
