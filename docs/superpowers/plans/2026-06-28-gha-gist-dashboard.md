# GitHub Actions + Gist + Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run AdMaven automation on two GitHub repos with staggered cron schedules, pull destination links from a GitHub Gist, and push run stats to Supabase for a live GitHub Pages dashboard.

**Architecture:** Two public GitHub repos each run 2 parallel GHA jobs on a staggered 2-hour cron (Repo 1 at :00, Repo 2 at :30). Each job reads destination links from a shared GitHub Gist, runs `auto_admaven.py`, and POSTs per-run stats to Supabase. A static GitHub Pages dashboard polls Supabase and renders live stats.

**Tech Stack:** GitHub Actions, Python 3.11, Playwright/Chromium, GitHub Gist API, Supabase (Postgres + REST API), GitHub Pages (vanilla HTML/JS)

## Global Constraints

- Repo must be public for unlimited GHA minutes
- Each job: `--count 200 --concurrency 10`
- Repo 1 cron: `0 0,2,4,6,8,10,12,14,16,18,20,22 * * *` (every 2h at even hours)
- Repo 2 cron: `0 1,3,5,7,9,11,13,15,17,19,21,23 * * *` (every 2h at odd hours)
- 2 parallel jobs per repo → 4 total jobs firing per hour across both repos
- Supabase free tier (500 MB, 2 GB bandwidth)
- Dashboard auto-refreshes every 10 seconds
- No secrets committed — all credentials via GHA Secrets

---

## Phase 1: GitHub Actions Workflow (Parallel Jobs)

### Task 1: Workflow file for Repo 1

**Files:**
- Create: `.github/workflows/admaven.yml`

**Interfaces:**
- Produces: GHA workflow that runs 2 parallel jobs, each calling `python3 run.py --admaven`
- Consumes: `config/Proxies.txt` (from repo), `ADMAVEN_PROXY` secret (optional override)

- [x] **Step 1: Create the workflow file**

```yaml
# .github/workflows/admaven.yml
name: AdMaven Automation

on:
  schedule:
    - cron: '0 0,2,4,6,8,10,12,14,16,18,20,22 * * *'   # Repo 1: even hours
  workflow_dispatch:
    inputs:
      count:
        description: 'Runs per job'
        default: '200'
      concurrency:
        description: 'Concurrent browsers'
        default: '10'

jobs:
  run-job-1:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install playwright
          playwright install chromium
          playwright install-deps chromium

      - name: Run AdMaven job 1
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          GIST_ID: ${{ secrets.GIST_ID }}
          GIST_TOKEN: ${{ secrets.GIST_TOKEN }}
        run: |
          COUNT=${{ github.event.inputs.count || '200' }}
          CONC=${{ github.event.inputs.concurrency || '10' }}
          python3 run.py --admaven --count $COUNT --concurrency $CONC --logs --no-proxy

      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: run-logs-job1-${{ github.run_number }}
          path: services/admaven/logs/run_logs.jsonl

  run-job-2:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install playwright
          playwright install chromium
          playwright install-deps chromium

      - name: Run AdMaven job 2
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          GIST_ID: ${{ secrets.GIST_ID }}
          GIST_TOKEN: ${{ secrets.GIST_TOKEN }}
        run: |
          COUNT=${{ github.event.inputs.count || '200' }}
          CONC=${{ github.event.inputs.concurrency || '10' }}
          python3 run.py --admaven --count $COUNT --concurrency $CONC --logs --no-proxy

      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: run-logs-job2-${{ github.run_number }}
          path: services/admaven/logs/run_logs.jsonl
```

- [x] **Step 2: Verify workflow syntax locally**

```bash
pip install actionlint  # or brew install actionlint
actionlint .github/workflows/admaven.yml
```
Expected: no errors

- [x] **Step 3: Commit**

> **Note:** Final implementation differs from plan — both repos use the same `* * * * *` cron (every hour) with a `REPO_SLOT` secret gate (slot 1 = even hours, slot 2 = odd hours). Count randomised to 42–52 per job, concurrency 15, random 0–30 min sleep before run, Evomi residential proxy instead of `--no-proxy`.

---

### Task 2: Workflow file for Repo 2 (staggered offset)

**Files:**
- Create: `.github/workflows/admaven.yml` (in the second repo)

**Interfaces:**
- Same as Task 1 except cron offset is `30 */2 * * *`

- [x] **Step 1: Copy the workflow from Task 1, change only the cron line**

```yaml
on:
  schedule:
    - cron: '0 1,3,5,7,9,11,13,15,17,19,21,23 * * *'   # Repo 2: odd hours
```

Everything else identical to Task 1.

- [x] **Step 2: Push to Repo 2**

```bash
# In repo 2's directory
git add .github/workflows/admaven.yml
git commit -m "feat: add GHA admaven workflow (staggered at :30)"
git push
```

- [x] **Step 3: Manually trigger both workflows in GitHub UI**

Go to `Actions → AdMaven Automation → Run workflow` in both repos.
Verify both jobs start, install playwright, and run without import errors.
Check the uploaded artifact contains `run_logs.jsonl`.

---

## Phase 2: GitHub Gist for Dynamic Links

### Task 3: Gist reader utility

**Files:**
- Create: `core/gist.py`
- Modify: `services/admaven/scripts/auto_admaven.py` (add `--gist` flag)

**Interfaces:**
- Produces: `fetch_lines(gist_id, token=None) -> list[str]` — returns non-empty, non-comment lines from the Gist
- Consumes: `GIST_ID` env var, `GIST_TOKEN` env var (optional, for private gists)

- [ ] **Step 1: Create `core/gist.py`**

```python
# core/gist.py
import os
import urllib.request
import json

def fetch_lines(gist_id: str = None, token: str = None) -> list[str]:
    """Fetch non-empty, non-comment lines from a GitHub Gist."""
    gist_id = gist_id or os.environ.get("GIST_ID", "")
    token   = token   or os.environ.get("GIST_TOKEN", "")
    if not gist_id:
        raise ValueError("GIST_ID not set")

    url = f"https://api.github.com/gists/{gist_id}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    # Use the first file in the gist
    first_file = next(iter(data["files"].values()))
    raw = first_file.get("content", "")
    return [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")]
```

- [ ] **Step 2: Test the reader manually**

```bash
# Create a test gist at gist.github.com with a few URLs, one per line
# Then run:
GIST_ID=<your-gist-id> python3 -c "
from core.gist import fetch_lines
lines = fetch_lines()
print(lines)
"
```
Expected: list of URLs printed

- [ ] **Step 3: Add `--gist` flag to `auto_admaven.py`**

In `auto_admaven.py`, add to `argparse`:
```python
ap.add_argument("--gist", action="store_true",
                help="load destination URLs from GitHub Gist (GIST_ID env var)")
```

In `main_async`, after the pool setup, add:
```python
if args.gist:
    from core.gist import fetch_lines
    urls = fetch_lines()
    print(f"[gist]  loaded {len(urls)} URL(s)")
else:
    # existing destinations.txt logic
    urls = [...]  # keep existing behaviour
```

Then iterate over `urls` instead of a single URL when spawning.

- [ ] **Step 4: Update the workflow env + run command**

In `.github/workflows/admaven.yml`, change the run step:
```yaml
run: |
  python3 run.py --admaven --count $COUNT --concurrency $CONC --logs --no-proxy --gist
```

- [ ] **Step 5: Commit**

```bash
git add core/gist.py services/admaven/scripts/auto_admaven.py .github/workflows/admaven.yml
git commit -m "feat: load destination URLs from GitHub Gist"
```

---

## Phase 3: Live Dashboard (Supabase + GitHub Pages)

### Task 4: Supabase table + push stats after each run

**Files:**
- Create: `core/stats.py`
- Modify: `services/admaven/scripts/auto_admaven.py` (call `push_run` after each instance)

**Interfaces:**
- Produces: `push_run(entry: dict)` — POSTs one row to Supabase `runs` table
- Consumes: `SUPABASE_URL`, `SUPABASE_KEY` env vars

- [ ] **Step 1: Create the Supabase table**

In Supabase dashboard → SQL Editor, run:
```sql
create table runs (
  id         bigserial primary key,
  ts         timestamptz default now(),
  repo       text,
  job        int,
  device     text,
  ip         text,
  url        text,
  success    boolean,
  skipped    boolean,
  bw_kb      float
);

-- Allow unauthenticated inserts (anon key is enough)
alter table runs enable row level security;
create policy "insert_open" on runs for insert with check (true);
create policy "select_open" on runs for select using (true);
```

- [ ] **Step 2: Create `core/stats.py`**

```python
# core/stats.py
import json
import os
import urllib.request

_URL = None
_KEY = None

def _init():
    global _URL, _KEY
    _URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
    _KEY = os.environ.get("SUPABASE_KEY", "")

def push_run(entry: dict):
    """POST a single run row to Supabase. Silently skips if creds not set."""
    _init()
    if not _URL or not _KEY:
        return
    payload = json.dumps(entry).encode()
    req = urllib.request.Request(
        f"{_URL}/rest/v1/runs",
        data=payload,
        method="POST",
    )
    req.add_header("apikey", _KEY)
    req.add_header("Authorization", f"Bearer {_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        print(f"[stats]  push failed: {e}")
```

- [ ] **Step 3: Call `push_run` in `run_instance` (auto_admaven.py)**

After building `entry` for `write_log`, add:
```python
from core.stats import push_run
push_run({
    **entry,
    "repo": os.environ.get("GITHUB_REPOSITORY", "local"),
    "job":  int(os.environ.get("GITHUB_JOB_INDEX", "0")),
})
```

- [ ] **Step 4: Verify locally**

```bash
SUPABASE_URL=https://xxx.supabase.co SUPABASE_KEY=<anon-key> python3 -c "
from core.stats import push_run
push_run({'ts': '2026-06-28T00:00:00+05:30', 'device': 'test', 'ip': '1.2.3.4',
          'url': 'http://test.com', 'success': True, 'skipped': False,
          'bw_kb': 100.0, 'repo': 'test', 'job': 1})
print('ok')
"
```
Then check Supabase → Table Editor → `runs` for the new row.

- [ ] **Step 5: Commit**

```bash
git add core/stats.py services/admaven/scripts/auto_admaven.py
git commit -m "feat: push per-run stats to Supabase"
```

---

### Task 5: GitHub Pages live dashboard

**Files:**
- Create: `docs/dashboard/index.html`
- Create: `.github/workflows/pages.yml`

**Interfaces:**
- Consumes: Supabase anon key (hardcoded in HTML — anon key is public-safe with RLS)
- Produces: Live dashboard at `https://<user>.github.io/<repo>/dashboard/`

- [ ] **Step 1: Create `docs/dashboard/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AdMaven Live Dashboard</title>
  <style>
    body { font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 24px; }
    h1   { color: #58a6ff; }
    .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 24px 0; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
    .card .label { font-size: 11px; color: #8b949e; text-transform: uppercase; }
    .card .value { font-size: 28px; font-weight: bold; margin-top: 4px; }
    .green { color: #3fb950; }
    .red   { color: #f85149; }
    .yellow{ color: #d29922; }
    table  { width: 100%; border-collapse: collapse; font-size: 13px; }
    th     { text-align: left; padding: 8px; border-bottom: 1px solid #30363d; color: #8b949e; }
    td     { padding: 6px 8px; border-bottom: 1px solid #21262d; }
    tr:hover td { background: #161b22; }
    .badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; }
    .badge.ok  { background: #1a4a1a; color: #3fb950; }
    .badge.fail{ background: #4a1a1a; color: #f85149; }
    .badge.skip{ background: #3a3a1a; color: #d29922; }
    #updated   { color: #8b949e; font-size: 12px; margin-bottom: 16px; }
  </style>
</head>
<body>
  <h1>⚡ AdMaven Live Dashboard</h1>
  <div id="updated">Loading...</div>

  <div class="stat-grid">
    <div class="card"><div class="label">Total Runs</div><div class="value" id="s-total">—</div></div>
    <div class="card"><div class="label">Succeeded</div><div class="value green" id="s-ok">—</div></div>
    <div class="card"><div class="label">Skipped</div><div class="value yellow" id="s-skip">—</div></div>
    <div class="card"><div class="label">Avg BW / Run</div><div class="value" id="s-bw">—</div></div>
  </div>

  <table>
    <thead>
      <tr><th>Time</th><th>Repo</th><th>Device</th><th>IP</th><th>Status</th><th>BW</th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
  const SUPABASE_URL = "REPLACE_WITH_SUPABASE_URL";
  const SUPABASE_KEY = "REPLACE_WITH_SUPABASE_ANON_KEY";

  async function load() {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/runs?order=ts.desc&limit=200`,
      { headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` } }
    );
    const rows = await res.json();

    const total   = rows.length;
    const ok      = rows.filter(r => r.success && !r.skipped).length;
    const skipped = rows.filter(r => r.skipped).length;
    const avgBw   = rows.reduce((a, r) => a + (r.bw_kb || 0), 0) / (total || 1);

    document.getElementById("s-total").textContent = total;
    document.getElementById("s-ok").textContent    = ok;
    document.getElementById("s-skip").textContent  = skipped;
    document.getElementById("s-bw").textContent    = (avgBw / 1024).toFixed(2) + " MB";
    document.getElementById("updated").textContent = "Last updated: " + new Date().toLocaleTimeString();

    const tbody = document.getElementById("rows");
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${new Date(r.ts).toLocaleTimeString()}</td>
        <td>${(r.repo || "").split("/")[1] || r.repo}</td>
        <td>${r.device || "—"}</td>
        <td>${r.ip || "—"}</td>
        <td><span class="badge ${r.skipped ? 'skip' : r.success ? 'ok' : 'fail'}">
          ${r.skipped ? "skipped" : r.success ? "✓ ok" : "✗ fail"}
        </span></td>
        <td>${((r.bw_kb || 0) / 1024).toFixed(2)} MB</td>
      </tr>`).join("");
  }

  load();
  setInterval(load, 10000);  // refresh every 10s
</script>
</body>
</html>
```

- [ ] **Step 2: Replace the two placeholder values in the HTML**

```
REPLACE_WITH_SUPABASE_URL  → your Supabase project URL (e.g. https://abc.supabase.co)
REPLACE_WITH_SUPABASE_ANON_KEY → your anon/public key from Supabase → Settings → API
```

- [ ] **Step 3: Create `.github/workflows/pages.yml`**

```yaml
name: Deploy Dashboard

on:
  push:
    paths:
      - 'docs/dashboard/**'
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v4
      - uses: actions/upload-pages-artifact@v3
        with:
          path: docs/dashboard
      - uses: actions/deploy-pages@v4
        id: deployment
```

- [ ] **Step 4: Enable GitHub Pages in repo settings**

GitHub repo → Settings → Pages → Source: **GitHub Actions**

- [ ] **Step 5: Push and verify dashboard loads**

```bash
git add docs/dashboard/index.html .github/workflows/pages.yml
git commit -m "feat: add live dashboard on GitHub Pages"
git push
```

Open `https://<your-username>.github.io/<repo-name>/` — should show the dashboard with stats auto-refreshing every 10s.

- [ ] **Step 6: Add Supabase secrets to both repos**

GitHub repo → Settings → Secrets → Actions → New repository secret:
- `SUPABASE_URL` = `https://xxx.supabase.co`
- `SUPABASE_KEY` = anon key
- `GIST_ID` = gist ID from Phase 2
- `GIST_TOKEN` = GitHub personal access token with `gist` scope

---

## Execution Order Summary

| Phase | Task | Deliverable |
|-------|------|-------------|
| 1 | Task 1 | Repo 1 workflow — 2 parallel jobs, cron :00 |
| 1 | Task 2 | Repo 2 workflow — 2 parallel jobs, cron :30 |
| 2 | Task 3 | Gist reader + `--gist` flag, dynamic link loading |
| 3 | Task 4 | Supabase push after each run |
| 3 | Task 5 | GitHub Pages live dashboard, 10s refresh |
