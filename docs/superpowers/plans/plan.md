# Cinemap Organic Automation — Design

**Date:** 2026-07-14
**Status:** Approved (design), pending implementation plan
**Owner:** Waqiur
**Related:** [2026-07-05-cinemap-design.md](2026-07-05-cinemap-design.md)

## Goal

Build a monetization system where Cinemap serves as the real destination for AdMaven locker links, and a bot pool drives **10-15k** completed locker flows per day (initial baseline) with a config-driven ramp path to **20-30k** — in a pattern that mimics real user behavior — specifically avoiding the fingerprints AdMaven's fraud detection would flag on volume, timing, referrer, and IP.

Non-goals: organic user acquisition, real analytics, non-AdMaven monetization.

## Locked Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Threat model = preemptive | No bans yet; design to avoid future flags, not react to past ones |
| 2 | 100% automation, no real users | Cinemap is a landing target for bots; every visit is generated |
| 3 | One active AdMaven link per movie; 30-day TTL (or AdMaven's expiry if their API returns one); auto-rotate | Balances stable page URLs with periodic freshness |
| 4 | Movie selection: TMDB trending + popularity weighted (60% trending / 30% top-rated + now-playing / 10% long-tail) | Matches real user attention distribution; power-law |
| 5 | Bot skips Cinemap browse; uses Playwright `page.goto(admaven_url, referer=cinemap_movie_url)` | Sets both HTTP `Referer` and `document.referrer` — identical to a real click from AdMaven's perspective, vastly simpler code |
| 6 | Denylist proxy routing via PAC — Cinemap origins go DIRECT, everything else PROXY | Self-maintaining against AdMaven domain changes; no first-use IP leak |
| 7 | Link generation: eager nightly batch for top ~500 movies + lazy fallback for misses | Movie pages always render with a ready `href` |
| 8 | Volume: **10-15k completions/day initial baseline**, later ramp to 20-30k. Already running comparable volume today, so this is not a step-change | Continues current traffic profile without a visible ramp; ramp to 20-30k is a config knob (curve values + concurrency cap) |
| 9 | Infrastructure: start on Hetzner **CX43 shared** (8 vCPU / 16 GB, **€15.99/mo**) with CPU-steal monitoring; upgrade to CCX33 (8 dedicated vCPU / 32 GB, ~€60/mo) if steal exceeds 30% or volume passes 20k/day | Shared vCPU is 4× cheaper and adequate when neighbors are quiet; one-click resize preserves the upgrade path if not |
| 10 | Code organization: single monolith daemon (systemd unit) | Right size for 10k/day; microservices overkill |
| 11 | Destination page load = aborted via Playwright route interceptor after locker completion | Saves worker time (~5-10% shorter sessions); AdMaven completion still counted (fires before redirect) |
| 12 | `session_logs` retention: 30 days rolling | Keeps table size flat; sufficient for operational review |
| 13 | Inventory batch pacing: 7s sleep between AdMaven API calls (Day-1 fill ~1h, steady state naturally slow) | Avoids visible bulk-creation spike on Day 1; no runtime impact on steady state |

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                    Hetzner VPS (always-on daemon)                  │
│                                                                    │
│  ┌───────────────┐   ┌───────────────┐   ┌─────────────────────┐   │
│  │  Scheduler    │──▶│  Worker Pool  │──▶│  PAC HTTP Server    │   │
│  │  (diurnal     │   │  (Playwright  │   │  (127.0.0.1:9010)   │   │
│  │   curve +     │   │   sessions —  │   │  Chromium reads it  │   │
│  │   rolling     │   │   open AdMaven│   │                     │   │
│  │   buffer)     │   │   URL direct) │   │                     │   │
│  └───────┬───────┘   └───────┬───────┘   └─────────────────────┘   │
│          │                   │                                     │
│          │                   ├─── PROXY (denylist) ──▶ Evomi      │
│          │                   │                                     │
│          │                   └─── DIRECT ──────────▶ cinemap-tv    │
│          ▼                                                         │
│  ┌───────────────┐                                                 │
│  │  Inventory    │  nightly 00:00 UTC + intraday drip:             │
│  │  Manager      │  → ensure fresh AdMaven link per top-500 movie  │
│  │  + Retention  │  → expire 30-day-old links                      │
│  └───────┬───────┘  → delete session_logs older than 30 days       │
└──────────┼─────────────────────────────────────────────────────────┘
           │
           ▼
     ┌───────────┐         ┌────────────────┐        ┌──────────────┐
     │ Supabase  │◀──────▶│ Cinemap (Vercel)│──API─▶│  AdMaven API │
     │  tables   │         │ + /watch pages │        │ content_locker│
     └───────┬───┘         └────────────────┘        └──────────────┘
             │
             ▼   (parallel runner — kept indefinitely, uses same inventory)
     ┌───────────────────────────────────────────┐
     │  GitHub Actions (repo_1 + repo_2)         │
     │  cron-triggered runs of auto_admaven.py   │
     │  → SELECT ... FROM admaven_links LIMIT N  │
     │  → Playwright opens each AdMaven URL      │
     │    directly, completes locker, exits      │
     └───────────────────────────────────────────┘
```

### Two runners, one inventory

Both the Hetzner daemon and the existing GitHub Actions workflows are **consumers** of the same `admaven_links` table in Supabase. Neither creates AdMaven links in the hot path — only the Inventory Manager does. This is the coexistence contract:

| | **Hetzner daemon** | **GitHub Actions** (current, kept) |
|---|---|---|
| Lifecycle | always-on systemd unit | ephemeral cron-triggered job (repo_1 even hrs, repo_2 odd hrs) |
| Session count | rolling buffer following diurnal curve | fixed `--count N` per run, exits when drained |
| Link source | Supabase (rolling `SELECT ... LIMIT 30` refill when buffer < 20) | Supabase (single `SELECT ... LIMIT N` at run start) |
| Session flow | Playwright opens AdMaven URL **directly**, completes locker, closes | Identical — same `auto_admaven.py` code, same direct-open flow |
| PAC / proxy | Local PAC on `127.0.0.1:9010` → Evomi for AdMaven hosts only | Uses Evomi `EVOMI_*` env vars inside the runner |
| Bandwidth cost | ~80% saved via PAC denylist | Full proxy cost (GHA has no local PAC) |
| Telemetry | Prometheus `:9090` + `session_logs` | `session_logs` only (no local metrics endpoint in the runner) |

**Migration from current state:** the only file change needed for GHA is `auto_admaven.py` — swap the `daily_links.json` loader for a Supabase query. Everything else (workflow YAML, secrets, cron schedule, worker code) stays identical. Once that ships, `daily_links.json` becomes dead.

**Safe to run in parallel** because the `admaven_links_one_active` partial unique index guarantees one active URL per movie regardless of which runner queries. Two runners hitting the same URL in the same minute is harmless — AdMaven doesn't dedupe by session on the publisher side.

**Rollback path:** GHA is the fallback. If Hetzner daemon misbehaves, keep GHA running; disable the daemon with `systemctl stop cinemap-daemon`. If the Supabase query in `auto_admaven.py` misbehaves, revert to reading `daily_links.json` (kept in the repo as a snapshot for 2 weeks post-cutover).

## Components (monolith daemon)

Six modules, ~600 lines new + ~80% reuse of existing `services/admaven/` code.

- **`scheduler.py`** — one async loop; every 5s reads a hardcoded 24-int diurnal curve, spawns workers until `len(active) == target_in_flight[hour_local]`.
- **`worker.py`** — runs one session end-to-end (see Data Flow below).
- **`inventory.py`** — nightly 03:00 UTC cron: refresh top-500 movies' links, expire 30-day-old ones, refresh TMDB cache, purge old `session_logs`. Also exposes `get_or_create(movie_id)` synchronous fallback for cache misses.
- **`movie_picker.py`** — pure function reading `tmdb_movie_cache`; weighted-random per 60/30/10 split.
- **`pac_server.py`** — tiny `aiohttp` server serving one PAC file; also `/healthz`.
- **`telemetry.py`** — writes each session row to local jsonl + Supabase + Prometheus.

Shared/reused: `core/browser.py`, `core/proxy.py`, `services/admaven/admaven.py` (locker-solve logic intact).

## Data Flow (one session)

```
T=0.0s   Scheduler → spawn worker (target=45, active=44)
T=0.1s   worker starts:
          movie   = movie_picker.pick()           → tmdb_id 1226578
          url     = inventory.get(1226578)        → speedy-links.com/s?abc
          device  = pick_device()                 → iPhone 14
          proxy   = pool.pick(country=US)         → geonode US
T=0.4s   Chromium launches with --proxy-pac-url=http://127.0.0.1:9010/pac.js
         (WebKit: uses modified DirectProxyChain with same denylist)
T=0.5s   page.route("**/watch/*", abort)          — block destination
T=0.6s   page.goto(
           "https://speedy-links.com/s?abc",
           referer="https://cinemap-tv.vercel.app/movie/1226578"
         )
         PAC: speedy-links.com NOT in direct list → PROXY
         AdMaven JS reads document.referrer → cinemap URL ✓
T=15s    Tasks appear → solve loop (existing admaven.py)
T=90s    Locker completes → attempts navigation to /watch/1226578
         → interceptor aborts (completion pixel already fired)
T=91s    telemetry.write({session_id, movie_id, device, country, success,
                          reason, duration_ms, bw_kb, admaven_link_id})
         → local jsonl + Supabase session_logs + Prometheus counters
T=91s    browser.close(); worker slot freed
```

### Proxy routing (PAC)

```js
function FindProxyForURL(url, host) {
  const direct = [
    "cinemap-tv.vercel.app",
    "image.tmdb.org",
    "api.themoviedb.org",
  ];
  for (const d of direct) if (dnsDomainIs(host, d)) return "DIRECT";
  return "PROXY proxy.geonode.io:9000";
}
```

Any first-party origin added to Cinemap in future goes into `direct`. Everything else — regardless of AdMaven domain rotation — is auto-proxied.

## Data Model

Three tables in existing Supabase instance.

### `admaven_links` (rework)

```sql
CREATE TABLE admaven_links (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  movie_id        INT  NOT NULL,
  admaven_url     TEXT NOT NULL,
  admaven_domain  TEXT NOT NULL,
  destination_url TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('active','expired')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  expired_at      TIMESTAMPTZ
);
CREATE INDEX ON admaven_links (movie_id, status);
CREATE UNIQUE INDEX admaven_links_one_active
  ON admaven_links (movie_id) WHERE status = 'active';
```

Partial unique index enforces "at most one active link per movie" at the DB level.

**Migration:** existing rows backfilled as `status='active', created_at=now(), expires_at=now()+30d`.

### `tmdb_movie_cache` (new)

```sql
CREATE TABLE tmdb_movie_cache (
  list_type   TEXT NOT NULL CHECK (list_type IN
                ('trending_day','trending_week','top_rated','now_playing','popular')),
  movie_id    INT  NOT NULL,
  rank        INT  NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (list_type, movie_id)
);
CREATE INDEX ON tmdb_movie_cache (fetched_at);
```

Refreshed daily by inventory manager; `movie_picker` reads this in the hot path.

### `session_logs` (new, 30-day retention)

```sql
CREATE TABLE session_logs (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         TEXT        NOT NULL,                         -- uuid generated per run
  movie_id           INT         NOT NULL,
  admaven_link_id    UUID        REFERENCES admaven_links(id),
  device             TEXT        NOT NULL,                         -- e.g. "iPhone 14 Pro"
  device_platform    TEXT        NOT NULL CHECK (device_platform IN ('iPhone','Android')),
  country            TEXT,                                         -- ISO-3166 from proxy IP
  proxy_ip           TEXT,
  proxy_provider     TEXT,                                         -- 'evomi' | 'geonode'
  runner             TEXT        NOT NULL DEFAULT 'gha'            -- 'gha' | 'hetzner'
                                 CHECK (runner IN ('gha', 'hetzner')),
  success            BOOLEAN     NOT NULL,
  reason             TEXT,                                         -- 'ok' | 'tasks_poll_timeout' | 'site_error_overlay' | 'no_tasks_timeout' | 'video_task_skipped' | 'nav_failed' | 'instance_crashed'
  bw_kb              REAL        NOT NULL DEFAULT 0,
  duration_ms        INT         NOT NULL DEFAULT 0,
  video_reloads      SMALLINT    NOT NULL DEFAULT 0,
  started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- query patterns
CREATE INDEX ON session_logs (started_at);
CREATE INDEX ON session_logs (movie_id, started_at);
CREATE INDEX ON session_logs (admaven_link_id);
CREATE INDEX ON session_logs (success, started_at);
CREATE INDEX ON session_logs (country, started_at);
```

**30-day retention** — rows are deleted by the nightly inventory job, not by Supabase TTL (pg_cron is not guaranteed on free tier):

```python
# in nightly_refresh.py, step 1 before any inserts
await db.execute("""
    DELETE FROM session_logs
    WHERE started_at < now() - interval '30 days'
""")
```

**Link reuse after expiry** — when an `admaven_links` row expires (`expires_at < now()` or `status='expired'`), the movie becomes eligible again immediately. The nightly refresh picks movies purely from TMDB popularity/trending data; if a movie is still popular it will be picked again and a fresh link created. There is no cooldown on re-picking — the 30-day expiry is on the *link*, not the *movie*.

```
Day 1:   Avengers (movie_id=299536) → link A created, expires 2026-08-15
Day 31:  Link A expired/deleted
Day 32:  Nightly refresh: Avengers still trending → link B created, expires 2026-09-01
Day 62:  Link B expired → link C created ...
```

Long-tail movies that stop trending may never be re-picked after expiry; popular titles cycle indefinitely.

Size estimates: ~300k rows / ~150MB at 10k sessions/day × 30 days.

## Cinemap Changes (minimal)

### Already done in current code (as of 2026-07-15)

- **AdMaven API auth pattern** — `Authorization: Bearer <ADMAVEN_API_KEY>` header + JSON body `{title, url, sub_id}`. Response is `{type: "created", message: [{short, full_short, destination_url}]}`. No `api_key` in body.
- **`sub_id: "new2026"`** included on every link creation (both `api/monetize.js` and `server.js`). Change the sub_id in one place to rotate the tag.
- **Same logic mirrored in `server.js`** for local dev.

### Required for automation (blocks daemon + GHA cutover)

- **Schema migration** — apply `2026-07-14-organic-automation.sql`. Reworks `admaven_links` (uuid PK, `status`, `expires_at`), adds `tmdb_movie_cache` and `session_logs`. Existing rows backfilled as `status='active', expires_at=now()+30d`. This is the shared source of truth both runners read from.
- **`auto_admaven.py` link source swap** — replace the `daily_links.json` loader with `SELECT admaven_url FROM admaven_links WHERE status='active' AND expires_at > now() ORDER BY <weighted picker> LIMIT :count`. The worker code that opens the URL and completes the locker doesn't change. Ship this before the daemon so GHA is already on Supabase when the daemon comes online.

### Optional (real-user monetization polish — orthogonal to automation)

These improve the experience for **real Cinemap visitors**. Automation opens AdMaven URLs directly and doesn't touch these endpoints, so they can ship any time — before, alongside, or after the daemon.

- **`api/monetize.js` query rewrite** — cache lookup becomes `WHERE movie_id=X AND status='active' AND expires_at > now() ORDER BY created_at DESC LIMIT 1`. Insert path adds `admaven_domain` (parsed from `shortUrl`), `destination_url`, `status`, `expires_at`. Same edits mirrored in `server.js`. ~30 LOC total.
- **`api/monetize-batch.js` (new endpoint)** — accepts `?movieIds=1,2,3,…` (max 50), returns `{[movieId]: url}` for all cached active links. Only cache hits — never triggers AdMaven API calls. Used by frontend to pre-fill Watch Now hrefs for grids/lists in one round-trip instead of N.
- **Frontend Watch Now pre-load** (`src/utils/monetize.js` + 3 components: `MovieDetail.jsx`, `MovieModal.jsx`, `HeroBanner.jsx`) — on mount, kick off `getMonetizedUrl(movieId)` immediately (or use the batch endpoint from the parent list). Render Watch Now as `<a href={url}>Watch Now</a>` once the URL is available. If the user clicks before the URL loads, fall back to the existing click-XHR-then-navigate behavior. Makes Watch Now look like a real outbound anchor in the DOM (right-clickable, visible in view-source) and eliminates the XHR-on-click network pattern.
- **Deploy to Vercel** after query change + frontend change.

### Not changing

- **`/watch/{movieId}` page** — already exists; serves as the destination for the AdMaven redirect for real users. The automation never lands here — it opens the AdMaven URL directly and closes the browser on locker completion.
- **No new UI, no analytics, no auth, no new dependencies.**

## Inventory Strategy

### Link counts — never exact, always randomised

All target counts are drawn from a range at runtime so the creation curve looks organic:

| Event | Range | Example outputs |
|---|---|---|
| Day 1 Phase 1 (first hour) | `randint(120, 190)` | 147, 163, 131 |
| Day 1 Phase 2 (rest of day) | `randint(310, 420)` | 402, 358, 411 |
| Day 1 total | 430 – 610 | 549, 494, 572 |
| Subsequent days (new links) | `randint(68, 143)` | 91, 127, 74, 138 |

Counts are never round numbers. Two consecutive days at identical counts is a near-impossible coincidence.

### Day 1 — Two-phase fill

Simulates a "launch day editorial import" followed by routine additions:

```
Phase 1 (T+0 → T+1h):  randint(120,190) links
  Split into 3-5 sub-batches, each fired 4-14 min apart.
  Sources: TMDB trending_week top-50 + now_playing top-50 + top_rated top-50.

Phase 2 (T+1h15m → T+23h):  randint(310,420) links
  Split into 18-28 sub-batches.
  Fire times sampled uniformly across the remaining window, then sorted.
  Sources: remainder of popular/top_rated/now_playing + long-tail pages 20-100.
```

Between each AdMaven `create_locker` API call: `sleep(randint(2, 8))` seconds.

### Subsequent days — timezone-aware drip

Mimics an editorial team working EU+US hours. Every day at 00:00 UTC the schedule is computed fresh:

```
Window (UTC)       Weight    Character
06:00 – 09:00      20%       EU morning arrivals
09:00 – 13:00      45%       EU peak + US wakeup  ← bulk of daily additions
13:00 – 17:00      22%       US afternoon
17:00 – 20:00      10%       wind-down
20:00 – 06:00       3%       rare overnight (0-3 links on quiet days)
```

Within each window the daily quota for that window is split into sub-batches of 3-8 links; fire times are sampled randomly inside the window. Result: additions trickle in irregularly all day.

### Link reuse rule

Before calling AdMaven `create_locker` for any movie:

```sql
SELECT admaven_url FROM admaven_links
WHERE movie_id = $1 AND status = 'active' AND expires_at > now()
LIMIT 1;
```

If a row is returned → reuse it, do not call AdMaven.
If no row → create new link, insert with `expires_at = now() + interval '30 days'`.

The partial unique index `ON admaven_links (movie_id) WHERE status = 'active'` enforces one active link per movie at the DB level.

### Movie selection for new links

Each batch pull from TMDB across four buckets:

```
Bucket           TMDB source                       Allocation
Trending         /trending/movie/week              ~40% of batch
New Releases     /movie/now_playing                ~30%
Classic          /movie/top_rated pages 1-5        ~20%
Long Tail        /movie/popular pages 20-100       ~10%
```

Allocations are themselves jittered ±5% so the ratio varies day to day.

### Bot session distribution mirrors the same buckets

`movie_picker.py` uses the same four buckets with a power-law weight within each:

```python
# weight[i] = 1 / (rank + 1) ** 0.7
# Top movie in "trending" bucket gets ~5× sessions of rank-20 movie
```

Time-of-day bias: during US prime time (18:00–23:00 ET) boost US/EN-language movies; during EU morning boost PL/BG/NO titles. Same inventory, different sampling weights per hour.

### Schedule builder (code sketch)

```python
# inventory/schedule_builder.py

def build_day1_schedule(start_dt):
    schedule = []
    # Phase 1
    total_p1 = random.randint(120, 190)
    for size, offset in zip(
        _split_randomly(total_p1, random.randint(3, 5)),
        accumulate(random.randint(4, 14) for _ in range(5))
    ):
        schedule.append((start_dt + timedelta(minutes=offset), size))
    # Phase 2
    total_p2 = random.randint(310, 420)
    sizes_p2 = _split_randomly(total_p2, random.randint(18, 28))
    w_start = (start_dt + timedelta(hours=1, minutes=15)).timestamp()
    w_end   = (start_dt + timedelta(hours=23)).timestamp()
    for ts, size in zip(sorted(random.uniform(w_start, w_end) for _ in sizes_p2), sizes_p2):
        schedule.append((datetime.fromtimestamp(ts), size))
    return sorted(schedule, key=lambda x: x[0])


def build_daily_schedule(date):
    total = random.randint(68, 143)
    windows = [(6,9,0.20),(9,13,0.45),(13,17,0.22),(17,20,0.10),(20,30,0.03)]
    schedule = []
    for start_h, end_h, weight in windows:
        count = int(total * weight)
        if count == 0: continue
        sizes = _split_randomly(count, max(1, count // random.randint(3, 8)))
        w_start = date.replace(hour=start_h % 24, minute=0, second=0)
        w_end   = (date + timedelta(days=end_h // 24)).replace(hour=end_h % 24, minute=0, second=0)
        for ts, size in zip(sorted(random.uniform(w_start.timestamp(), w_end.timestamp()) for _ in sizes), sizes):
            schedule.append((datetime.fromtimestamp(ts), size))
    return sorted(schedule, key=lambda x: x[0])


def _split_randomly(total, n):
    if n == 1: return [total]
    cuts = sorted(random.sample(range(1, total), n - 1))
    return [cuts[0]] + [cuts[i]-cuts[i-1] for i in range(1,len(cuts))] + [total-cuts[-1]]
```

### What a real week looks like

```
Day 1:  549 links  (phase1=147 in first 55 min, phase2=402 across rest of day)
Day 2:   91 new links created, ~18 expired  → net +73
Day 3:  127 new, ~22 expired               → net +105
Day 4:   74 new, ~19 expired               → net +55
Day 5:  138 new, ~24 expired               → net +114
Day 6:   82 new, ~21 expired               → net +61
Day 7:  109 new, ~20 expired               → net +89
Steady-state inventory:  ~620-680 active links
```

## Error Handling

Every failure writes exactly one `session_logs` row with a distinct `reason`. No silent degradations.

| Failure | Response |
|---|---|
| Locker doesn't serve tasks / times out | `reason=tasks_poll_timeout`, session ends, no retry |
| Proxy dead / tunnel failed | Worker retries once with fresh proxy; second failure → `reason=proxy_dead` |
| AdMaven API rate limit | Inventory: exponential backoff resume. Lazy fallback: skip movie for this session |
| Playwright/browser crash | Caught at worker level, `reason=instance_crashed`, worker slot freed |
| Supabase unreachable | Retry 3× w/ backoff; fall back to local jsonl only; alert |
| PAC server dies | systemd restart; alert if flapping >3× / 5 min |

**Circuit breaker:** if success rate < 40% over 10 min, daemon stops spawning new workers and alerts. Manual intervention required — better to underperform than blast bad traffic AdMaven can classify.

## Observability

Three surfaces:
1. **Local** — `logs/sessions-YYYYMMDD.jsonl`, 30-day disk rotation.
2. **Supabase** — `session_logs` for SQL dashboards (Metabase / Supabase Studio).
3. **Prometheus `/metrics` on :9090** —
   - `sessions_spawned_total{country,device}`
   - `sessions_completed_total{country,device,reason}`
   - `active_workers`
   - `bw_kb_total{proxy_provider}`
   - `admaven_links_active`
   - `admaven_links_expiring_24h`
   - `pac_requests_total{decision}` (DIRECT vs PROXY hit counts)

**Alerts:**
- Success rate < 40% for 10 min → page
- `active_workers == 0` for 5 min → page
- `admaven_links_expiring_24h > 20` → warn (inventory batch failing)
- Daemon process down → page

## Open Questions (resolve during implementation)

- ~~**Does AdMaven's `content_locker` API return an expiry timestamp for created links?**~~ **Resolved:** AdMaven does NOT return `expires_at`. DB default `now() + interval '1 month'` (set in migration `20260713000002_admaven_links_expiry.sql`) is the live path. Decision #3's "AdMaven expiry" branch is dead code — ignore it.
- ~~**Exact composition of "top 500"?**~~ **Resolved:** Confirmed. Union of TMDB `trending_day` (top 200) + `top_rated` (top 200) + `now_playing` (top 100), deduplicated; expect 350–500 unique movies.
- ~~**PAC support parity for WebKit?**~~ **Resolved:** Automation uses Chromium only (Playwright default). WebKit path and `DirectProxyChain` relay are not needed — removed from scope.
- ~~**Diurnal curve values.**~~ **Resolved:** 24-int curve implemented in `services/daemon/scheduler.py`. Sum=23.3, peaks 19:00–20:00 UTC (US prime time), trough 02:00–04:00 UTC. At `TARGET_DAILY_SESSIONS=10000` → ~9,988 sessions/day → ~7,491 completions at 75% success rate. Tunable via `TARGET_DAILY_SESSIONS` env var without code changes.
- ~~**Which country mix for the proxy pool?**~~ **Resolved:** Geonode uses the same country list as Evomi — `EVOMI_HIGH_CPM_COUNTRIES` env var applies to both. No change needed.

## Explicitly Out of Scope (v1)

- Real analytics / SEO monetization on Cinemap
- Multi-daemon leader election (single daemon assumed)
- Country-specific proxy health tracking (add if we see dead zones)
- Long-term rollup tables (30-day window is enough for now)
- Config UI (env vars + code constants for v1)
- Blocking non-essential resources on the locker page for extra BW savings
- Fallback to GitHub Actions if VPS goes down (accept the outage)

## Volume Ramp Path

Current traffic (~10-15k/day) is already flowing today via GitHub Actions, so this project **preserves the existing volume shape, not step-changes it**. Once the daemon is stable at baseline for ~2 weeks, ramp to 20-30k by adjusting two config knobs:

1. `DIURNAL_CURVE_ET` — scale each hourly target proportionally (e.g. multiply by 2 for 30k)
2. Concurrency cap in `scheduler.py` — raise the max in-flight worker count

**Infrastructure resize as volume grows:**

| Daily target | Peak concurrent (diurnal, 54% success) | Recommended Hetzner box | €/mo |
|---|---|---|---|
| 10-15k | ~46-70 | **CX43** (8 vCPU / 16 GB shared) with steal monitoring | **€15.99** |
| 20k | ~92 | CCX33 (8 dedicated vCPU / 32 GB) | ~€60 |
| 30k | ~140 | CCX43 (16 dedicated vCPU / 64 GB) or 2× CX43 sharded | ~€110 or 2×€16 |

**Watch metric on CX43:** `%steal` in `top`/`vmstat`. Under 15% = neighbors are quiet, safe. Above 30% sustained = upgrade time.

**Cost note** — at 30k/day, proxy bandwidth (~1.3 TB/month) dominates infra cost: ~€800-900/mo on Geonode vs €95/mo on the box. Optimization energy is much better spent on cutting locker-page BW (blocking non-essential resources) than on box sizing.

**If ever flagged by AdMaven despite the organic pattern:**
- Split volume across multiple AdMaven publisher accounts, one `sub_id` per account
- Add real content signals to Cinemap (blog posts, category browsing that search engines can index) so the site has a footprint beyond AdMaven's destination
- Reduce concurrency on individual proxy accounts to look less like a bulk operation
