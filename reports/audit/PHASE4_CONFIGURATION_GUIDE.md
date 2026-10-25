# Phase 4 — Production Configuration Guide: External PostgreSQL Setup

**Target Environment**: Render.com Web Service  
**Application**: Marine Pollution Intelligence API  
**Date**: October 2026  

---

## 1. Overview

The Marine Pollution Intelligence API currently falls back to a local SQLite database file on Render's ephemeral container filesystem when `DATABASE_URL` is omitted. To ensure investigations, AIS analyses, and user reports survive container redeployments and service sleep cycles, a managed PostgreSQL database should be connected.

The application codebase is already fully instrumented for PostgreSQL in `backend/app/persistence/db.py`. No code modifications are required.

---

## 2. Step-by-Step Configuration on Render

### Step 1: Create a Managed PostgreSQL Database
1. Log into your [Render Dashboard](https://dashboard.render.com).
2. Click **New +** in the top right corner and select **PostgreSQL**.
3. Configure the database details:
   - **Name**: `marine-pollution-db`
   - **Database**: `marine_pollution`
   - **User**: `marine_user`
   - **Region**: Same region as your Web Service (e.g. Frankfurt, Oregon).
   - **Plan**: Free or Starter.
4. Click **Create Database**.

### Step 2: Retrieve the Internal Database Connection String
1. Once the database status changes to **Available**, scroll down to the **Connections** section.
2. If your Web Service is hosted within the same Render region, copy the **Internal Database URL** (e.g. `postgresql://marine_user:...@dpg-...-a:5432/marine_pollution`).
   - *Note: Internal URLs do not consume public bandwidth and offer lower latency.*
3. If connecting from outside Render, copy the **External Database URL**.

### Step 3: Configure the Web Service Environment Variables
1. Navigate back to your API Web Service (`marine-pollution-intelligence-api`) in the Render Dashboard.
2. Select **Environment** from the left navigation menu.
3. Add or update the following environment variable:
   - **Key**: `DATABASE_URL`
   - **Value**: Paste the database connection string copied in Step 2.
4. Ensure `DATABASE_ENABLED` is set to `"true"`.
5. Click **Save Changes**.

---

## 3. Automatic Schema Initialization & Verification

Render will automatically trigger a redeployment of your Web Service with the new environment variables.

### How Initialization Works:
1. On startup, `backend/app/persistence/db.py:init_db()` runs automatically.
2. It detects the `postgresql://` URI scheme.
3. It creates the required tables if they do not already exist:
   - `investigations`: Stores full JSON payloads, user IDs, and timestamps.
   - `schema_migrations`: Tracks schema versions.
4. Connection pooling and transaction rollbacks are managed per connection request.

### Verification Checklist:
- [ ] Inspect Render service deployment logs: verify log entry indicates successful connection and `Database initialized`.
- [ ] Run an investigation via the UI or `POST /api/investigate`.
- [ ] In the Render Dashboard, trigger a **Manual Deploy -> Deploy latest commit** to force a container recycle.
- [ ] Fetch `GET /api/investigation/{id}` after the recycle: verify the investigation returns HTTP 200 with all fields intact.
