# Smart India Hackathon (SIH) — Final Five-Minute Presentation Script

**Project Title:** Marine Pollution Intelligence & Spill-Aware Maritime Navigation Platform  
**Target Audience:** SIH Jury Panel (Maritime Domain Specialists, AI/ML Researchers, Defense/Coast Guard Evaluators, Software Architects)  
**Total Allocated Duration:** 5 Minutes (300 Seconds) + 3 Minutes Q&A  
**Production URLs:**
- **Live Frontend Application:** [marine-pollution-intelligence-sih.vercel.app](https://marine-pollution-intelligence-sih.vercel.app)
- **Live Production API:** [marine-pollution-intelligence-api.onrender.com](https://marine-pollution-intelligence-api.onrender.com)
- **GitHub Repository:** [github.com/Subodh-17/marine-pollution-intelligence](https://github.com/Subodh-17/marine-pollution-intelligence)

---

## Presentation Delivery Principles
1. **Never exaggerate or fabricate:** Cite measured benchmark statistics from the held-out 450 Zenodo Sentinel-1 SAR test scenes ($N=450$, Platt calibrated $T=1.0879$).
2. **Honest Legal Boundary:** State clearly that AIS vessel ranking produces *investigative prioritization leads*, not criminal proof of guilt.
3. **Judge Recommendation Highlight:** Explicitly highlight the **Spill-Aware Maritime Route Optimization and Fuel Intelligence System** implemented directly in response to hackathon evaluation feedback.
4. **Tight Coordination:** Speaker 1 narrates the problem, science, and AI; Speaker 2 operates the live dashboard.

---

## Detailed Minute-by-Minute Script & Action Timeline

```
  0:00 ─── 0:35 : The Crisis & Motivation (Indian EEZ & Maritime Scale)
  0:35 ─── 1:15 : Satellite SAR Physics & Dual-Stage AI Pipeline
  1:15 ─── 1:55 : Despeckling, SpillUNet & Platt-Calibrated Confidence
  1:55 ─── 2:35 : Hydrodynamic Drift & Probabilistic Gaussian Trajectory
  2:35 ─── 3:15 : AIS Spatio-Temporal Correlation (Investigative Leads)
  3:15 ─── 4:15 : Judge Feature: Spill-Aware Routing & Fuel Intelligence
  4:15 ─── 4:45 : Benchmark Verification, Safety Gates & Reproducibility
  4:45 ─── 5:00 : Regulatory Compliance (IMO / SOLAS) & Live Conclusion
```

---

### Segment 1: The Crisis & Maritime Scale (0:00 – 0:35) | 35 Seconds

**Screen Action:**
- Projector displays the live dashboard at [marine-pollution-intelligence-sih.vercel.app](https://marine-pollution-intelligence-sih.vercel.app).
- The map is centered on the Indian Exclusive Economic Zone (EEZ) showing Arabian Sea / Bay of Bengal shipping lanes.

**Speaker 1 (Opening):**
> "Respected jury members, over 90% of global trade and 70% of India's petroleum imports travel across our sea lanes. Every day, thousands of merchant vessels transit the Indian Exclusive Economic Zone—spanning 2.3 million square kilometers.
> 
> Contrary to popular belief, catastrophic tanker collisions account for less than 15% of marine oil pollution. The vast majority stems from illicit, deliberate bilge dumping under the cover of night. By the time coast guard vessels or patrol aircraft arrive, the slick has dispersed, evidence is compromised, and the offending vessel has escaped international waters.
> 
> Today, we present **Marine Pollution Intelligence**—an end-to-end, production-grade intelligence platform that closes this operational gap: from satellite ingestion and deep learning segmentation, to hydrodynamic drift modeling, Bayesian AIS attribution, and our newest capability: **Spill-Aware Maritime Route Optimization and Fuel Intelligence**."

---

### Segment 2: Satellite SAR Physics & Dual-Stage AI Pipeline (0:35 – 1:15) | 40 Seconds

**Screen Action:**
- Switch to the **Satellite Intelligence Tab**.
- Click **Select Benchmark Scene** $\rightarrow$ choose `Oil Spill Scene 1 (Zenodo Sentinel-1 Dual-Pol)`.
- The UI displays raw VV and VH radar backscatter bands.

**Speaker 1:**
> "Optical satellites are blinded by cloud cover and night. Our platform utilizes **Sentinel-1 C-Band Synthetic Aperture Radar (SAR)**. Oil slicks damp high-frequency capillary gravity waves—known as Bragg scattering—producing distinctive dark patches of attenuated radar backscatter.
> 
> However, raw SAR imagery suffers from two severe operational hurdles: multiplicative speckle noise and natural lookalikes—such as low-wind calm waters, upwelling zones, and biogenic algal slicks.
> 
> To solve this, we engineer a dual-stage detection architecture:
> 1. **Robust Preprocessing:** Local percentile-based normalization ($1^{\text{st}}-99^{\text{th}}$ min-max scaling) with Lee adaptive despeckling that preserves sharp slick edges.
> 2. **Dual-Model Inference:** A **SpillUNet segmentation model** extracts pixel-level polygon boundaries, while a **multi-class CNN classifier** distinguishes true petroleum slicks from lookalikes and clean sea."

---

### Segment 3: Detection, Characterization & Platt Calibration (1:15 – 1:55) | 40 Seconds

**Screen Action:**
- Click **Run Pipeline Analysis**.
- Watch progress checklist update: Ingestion $\rightarrow$ Normalization $\rightarrow$ SpillUNet $\rightarrow$ Classification.
- Show the segmented red polygon overlay on the Leaflet map and the **Classification Scorecard**.

**Speaker 2:**
> "The pipeline completes in under 2 seconds running on ONNX Runtime CPU microservices. 
> 
> Notice the scorecard:
> - **Predicted Class:** Oil Spill
> - **Calibrated Confidence:** $92.8\%$
> - **Estimated Area:** $20.02\text{ km}^2$, perimeter $96.18\text{ km}$, major axis length $16.85\text{ km}$.
> 
> Crucially, neural networks are notoriously overconfident. We calibrated our classifier using **Platt Temperature Scaling** ($T=1.0879$) fitted strictly on a held-out validation split. This reduced our Expected Calibration Error (ECE) from $8.66\%$ down to $7.57\%$, ensuring that when the system alerts coast guard commanders with $90\%$ probability, it reflects true frequentist accuracy."

---

### Segment 4: Hydrodynamic Drift Modeling (1:55 – 2:35) | 40 Seconds

**Screen Action:**
- Navigate to the **Timeline & Drift Tab**.
- Toggle on **Hindcast (Origin)** and **Forecast (Dispersion)** layers.
- Animate the expanding probability cones across 24 hours.

**Speaker 1:**
> "A static satellite image only shows where the oil is *now*. To find the polluter, we must determine where it came from; to protect our coastlines, we must predict where it is heading.
> 
> Our hydrodynamic engine integrates real-time Metocean wind vectors ($U_{10}$) and surface ocean currents ($U_{\text{current}}$):
> 
> $$\vec{V}_{\text{drift}} = \vec{U}_{\text{current}} + \alpha \mathbf{R}(\theta) \vec{U}_{\text{wind}}$$
> 
> where $\alpha \approx 0.03$ represents wind leeway factor and $\theta \approx 10^\circ$ accounts for the Coriolis deflection angle in the Northern Hemisphere.
> 
> Hindcasting traces the slick backwards along the advection path to locate the release origin point. Concurrently, an advection-diffusion stochastic model projects the 24-hour forward dispersion cone with a growing Gaussian uncertainty ellipse, warning port authorities and marine sanctuaries ahead of time."

---

### Segment 5: AIS Correlation & Investigative Attribution (2:35 – 3:15) | 40 Seconds

**Screen Action:**
- Switch to the **Vessel Ranking & Attribution Tab**.
- Show the ranked list of merchant ships: Top vessel `MMSI: 357123000` (*Stolt Tenacity*, Tanker).
- Click the vessel to open the **Evidence Detail Drawer**.

**Speaker 2:**
> "Using the estimated spill time and hindcast release point, the platform queries AIS (Automatic Identification System) vessel trajectories.
> 
> Each candidate vessel is evaluated across four spatio-temporal likelihood components:
> 1. **Temporal Proximity:** Closest Point of Approach (CPA) time delta relative to estimated spill timestamp.
> 2. **Spatial Distance:** Orthodromic distance from the vessel track to the hindcasted release origin.
> 3. **Vessel Risk Profile:** Type weighting (Crude Tanker vs Container Ship vs Fishing).
> 4. **Kinematic Anomalies:** Course alterations or slow-steaming characteristic of tank washing operations.
> 
> **Important Legal Boundary:** We explicitly state to judges and operators that this ranking produces **investigative leads**, not legal conviction. AIS signals can suffer clock jitter or spoofing. Our system provides actionable surveillance prioritization for maritime patrol aircraft to perform physical dye or fluorosensor sampling."

---

### Segment 6: Judge Feature — Spill-Aware Routing & Fuel Intelligence (3:15 – 4:15) | 60 Seconds

**Screen Action:**
- Click the **Route Planner Tab**.
- The UI automatically loads the detected spill zone (`inv_6883af7d`, $20.02\text{ km}^2$, 61 vertices) directly into the avoidance layer.
- Select:
  - **Origin:** Mumbai Port ($18.940^\circ\text{N}, 72.840^\circ\text{E}$)
  - **Destination:** Arabian Sea West ($19.050^\circ\text{N}, 70.400^\circ\text{E}$)
  - **Vessel Type:** Tanker (Service speed $14.5\text{ kts}$)
  - **Safety Buffer:** $1.5\text{ NM}$
- Click **Calculate Optimal Route**.
- Point out the **Fuel Indication Meter** and the **Comparative Metrics Table**.

**Speaker 1:**
> "In earlier hackathon evaluation rounds, the judges asked: *'Once an oil slick is detected, how do commercial vessels safely navigate around it without excessive fuel waste?'*
> 
> We engineered the **Spill-Aware Maritime Route Optimization and Fuel Intelligence System**:
> 1. **Automated Spill Integration:** As you see, the slick polygon detected in our SAR analysis is automatically fed into the maritime graph engine—no manual drawing required.
> 2. **Tangent-Waypoint Avoidance Graph:** The direct Great-Circle baseline ($138.90\text{ NM}$) cuts directly through the toxic spill zone. Our routing engine expands the polygon by the IMO-mandated safety buffer ($1.5\text{ NM}$) and calculates visibility tangents around the convex hull, finding the optimal spill-avoiding path ($163.96\text{ NM}$).
> 3. **Admiralty Fuel Physics:** Fuel consumption is computed using empirical naval architecture power laws:
> 
> $$F = \frac{\Delta^{2/3} \cdot V^3}{C_{\text{adm}}} \cdot \text{SFOC} \cdot t$$
> 
> 4. **Live Operational Output:**
>    - Extra Voyage Distance: $+25.06\text{ NM}$ ($+18.0\%$)
>    - Extra Voyage Duration: $+1.73\text{ hours}$
>    - Extra Bunker Fuel: $+4.69\text{ Metric Tonnes}$ ($5,453\text{ Litres}$)
>    - Additional Bunker Cost: $+\$3,048.42$ at $\$650/\text{MT}$
>    - Carbon Footprint Delta: $+14.61\text{ MT CO}_2$
> 
> Notice the **Interactive Fuel Indication Meter**: it visualizes remaining vessel fuel reserves against the safe reserve threshold, warning the master if a diversion exceeds safety margins."

---

### Segment 7: Benchmark Rigor, Safety Gates & Reproducibility (4:15 – 4:45) | 30 Seconds

**Screen Action:**
- Switch to the **ML Evaluation & System Status Tab**.
- Show the confusion matrix on 450 Zenodo scenes and checksum provenance.

**Speaker 2:**
> "We believe in scientific reproducibility. Our benchmarks are evaluated on **450 completely held-out Sentinel-1 SAR scenes from Zenodo Part III**:
> - **Oil Spill F1 Score:** $91.23\%$ (Precision $96.30\%$, Recall $86.67\%$).
> - **Lookalike Rejection Rate:** $96.00\%$ (only 5 false alarms out of 150 lookalike scenes).
> - **Zero Data Leakage:** Verified 0 scene-ID overlap and 0 SHA-256 hash collisions between train and test sets.
> - **Fail-Closed Safety Gate:** We rigorously tested multiscale segmentation. While it increased massive slick recall, it generated over 32,000 false positive pixels on lookalikes. Following fail-safe engineering principles, we kept multiscale disabled by default in production."

---

### Segment 8: Regulatory Compliance & Conclusion (4:45 – 5:00) | 15 Seconds

**Screen Action:**
- Highlight the **SOLAS Chapter V / IMO Resolution A.893(21)** Advisory Notice rendered at the bottom of the Route Planner.

**Speaker 1 (Closing):**
> "Every route recommendation embeds mandatory SOLAS Chapter V navigational disclaimers reminding mariners that master's discretion and nautical charts remain sovereign.
> 
> The platform is live on Render and Vercel, validated by 714 backend pytest suites and 85 frontend integration tests. 
> 
> **Detect $\rightarrow$ Characterize $\rightarrow$ Trace $\rightarrow$ Correlate $\rightarrow$ Avoid $\rightarrow$ Optimize $\rightarrow$ Estimate Fuel.**
> 
> Thank you, and we welcome your questions!"

---

## Technical Equipment & Demonstration Checklist
- [x] Primary laptop connected to HDMI projector (1920x1080).
- [x] Secondary backup laptop running local containerized mirror (`localhost:5173` and `localhost:8000`).
- [x] Production URLs pre-warmed 5 minutes before judging to avoid Render cold-start latency.
- [x] Browser tabs arranged: 1. Live Dashboard, 2. Route Planner, 3. ML Status, 4. Zenodo Manifest.
- [x] Laser pointer / presenter remote tested.
