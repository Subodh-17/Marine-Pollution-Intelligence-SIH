# Phase 4.2 — Production Release & Deployment Checklist

**Repository**: `Marine-Pollution-Intelligence-SIH`  
**Target Environment**: Render.com Web Service (`marine-pollution-intelligence-api`)  
**Frontend Environment**: Vercel (`marine-pollution-intelligence-sih.vercel.app`)  
**Date**: October 2026  

---

## 1. Overview & Security Guidance

This checklist details the exact operational steps required to transition the live Render backend from ephemeral SQLite to durable managed PostgreSQL.

> [!IMPORTANT]
> **Zero Credential Exposure**: Never paste passwords, tokens, or raw connection strings into terminal logs, Git commits, or chat sessions. Render securely stores and masks environment variables in the Dashboard.

---

## 2. Step-by-Step Production Configuration on Render

### Step 1: Provision Managed PostgreSQL on Render
1. Navigate to the [Render Dashboard](https://dashboard.render.com).
2. Click **New +** in the top navigation bar and select **PostgreSQL**.
3. Fill in the database details:
   - **Name**: `marine-pollution-db`
   - **Database**: `marine_pollution`
   - **User**: `marine_user`
   - **Region**: **Frankfurt (EU Central)** (Ensure this matches the region of `marine-pollution-intelligence-api`).
   - **PostgreSQL Version**: `16` (or latest supported standard).
   - **Plan**: Free Tier (or Starter).
4. Click **Create Database**.

### Step 2: Retrieve the Internal Connection String
1. Wait for the database status to transition to **Available**.
2. Scroll to the **Connections** section on the database details page.
3. Locate **Internal Database URL** (format: `postgresql://marine_user:...@dpg-...-a:5432/marine_pollution`).
4. Click the copy icon.
   - *Note: Using the internal URL keeps database traffic inside Render's private network with zero egress bandwidth charges and lower latency.*

### Step 3: Configure Environment Variables in the Web Service
1. Open the existing Web Service: `marine-pollution-intelligence-api`.
2. Select **Environment** from the left-hand navigation menu.
3. Add or update the following environment variables:
   - **`DATABASE_URL`**: Paste the Internal Database URL copied in Step 2.
   - **`DATABASE_ENABLED`**: Ensure the value is set to `"true"`.
   - **`DATABASE_MANDATORY`**: Leave unset or set to `"false"` initially during the first deployment to allow graceful fallback while testing connection health.
4. Click **Save Changes**.

---

## 3. Post-Deployment Verification & Validation

Render will automatically trigger a redeployment of the Web Service with the new environment variables and the updated `buildCommand` (`pip install -r requirements.txt -r requirements-postgres.txt`).

### Verification Steps:
1. **Inspect Startup Deployment Logs**:
   - Check Render build logs: verify `Successfully installed psycopg2-binary`.
   - Check application startup logs: verify `database_ready` with `{"backend": "postgresql"}`.
2. **Execute Live Investigation**:
   - In the frontend application or via cURL, trigger an investigation:
     ```bash
     curl -X POST "https://marine-pollution-intelligence-api.onrender.com/api/spill/detect?mode=demo"
     ```
   - Verify that the returned JSON payload includes `"persisted": true`.
   - Note the returned `id` (e.g. `test-inv-01`).
3. **Verify Durability Across Restart**:
   - In the Render Dashboard, click **Manual Deploy -> Deploy latest commit** or **Restart Service**.
   - After the service finishes restarting, query the saved investigation:
     ```bash
     curl "https://marine-pollution-intelligence-api.onrender.com/api/spill/test-inv-01"
     ```
   - Verify that the investigation returns HTTP 200 with all telemetry, AIS candidates, and drift tracks intact.
4. **Lock In Mandatory Persistence**:
   - Once the restart-persistence test passes, update the environment variable in Render:
     - **`DATABASE_MANDATORY`**: `"true"`
   - Click **Save Changes**.
   - The application is now fully locked into mandatory enterprise durability.

---

## 4. Rollback Procedure

If the managed PostgreSQL database encounters infrastructure disruption or connection issues:
1. In the Web Service **Environment** settings on Render:
   - Remove or empty the `DATABASE_URL` variable.
   - Set `DATABASE_MANDATORY` to `"false"`.
2. Click **Save Changes**.
3. The API will immediately redeploy and fall back to in-process SQLite without application downtime.
