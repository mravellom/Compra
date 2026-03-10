# CompraVenta v2 — Scaled Architecture

## Target: 1,000,000 listings/day across 10+ marketplaces

---

## 1. Current vs Target

| Metric                  | Current        | Target          | Scale Factor |
|-------------------------|----------------|-----------------|--------------|
| Listings/day            | ~1,000         | 1,000,000       | 1,000×       |
| Marketplaces            | 3              | 10+             | 3×           |
| Master products         | ~200           | 500,000+        | 2,500×       |
| Opportunity scans/day   | Manual (POST)  | Continuous      | ∞            |
| Embedding throughput    | 2/sec (CPU)    | 2,000/sec (GPU) | 1,000×       |
| API latency (p99)       | 2-5s           | <200ms          | 25×          |
| Concurrent users        | 1              | 1,000+          | 1,000×       |

---

## 2. System Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │              CONTROL PLANE                       │
                    │  ┌───────────┐  ┌──────────┐  ┌──────────────┐ │
                    │  │ Scheduler │  │ Job Mgr  │  │ Health Check │ │
                    │  │ (APScheduler)│ (Priorities) │ (Watchdog)   │ │
                    │  └─────┬─────┘  └────┬─────┘  └──────┬───────┘ │
                    └────────┼─────────────┼───────────────┼─────────┘
                             │             │               │
                    ┌────────▼─────────────▼───────────────▼─────────┐
                    │              MESSAGE BUS (Redis Cluster)        │
                    │                                                 │
                    │  Streams:                                       │
                    │  ├── scrape_jobs      (orchestrator → scrapers) │
                    │  ├── raw_listings     (scrapers → normalizer)   │
                    │  ├── normalized       (normalizer → resolver)   │
                    │  ├── resolved         (resolver → enricher)     │
                    │  ├── enriched         (enricher → detector)     │
                    │  ├── opportunities    (detector → notifier)     │
                    │  ├── dead_letters     (failed messages)         │
                    │  └── metrics          (all → monitor)           │
                    │                                                 │
                    │  Keys:                                          │
                    │  ├── rate_limits:*    (per-domain counters)     │
                    │  ├── proxy_health:*   (proxy status)            │
                    │  ├── cache:fx_rates   (currency cache)          │
                    │  └── locks:*          (distributed locks)       │
                    └────────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────────┐
          │                  │                      │
    ┌─────▼──────┐    ┌──────▼───────┐    ┌────────▼────────┐
    │  SCRAPER   │    │  PROCESSOR   │    │  ANALYTICS      │
    │  FLEET     │    │  PIPELINE    │    │  ENGINE         │
    │            │    │              │    │                 │
    │ 20 workers │    │ 5 stages:    │    │ ┌─────────────┐│
    │ ├─ Amazon  │    │ ├─ Normalize ││   │ │ Opportunity ││
    │ ├─ ML_AR   │    │ ├─ Embed     ││   │ │ Detector    ││
    │ ├─ ML_MX   │    │ ├─ Resolve   ││   │ ├─────────────┤│
    │ ├─ eBay    │    │ ├─ Enrich    ││   │ │ Trend       ││
    │ ├─ Walmart │    │ └─ Store     ││   │ │ Analyzer    ││
    │ └─ ...     │    │              │    │ ├─────────────┤│
    └────────────┘    │ 10 workers   │    │ │ Category    ││
                      │ each stage   │    │ │ Ranker      ││
                      └──────────────┘    │ └─────────────┘│
                                          └────────────────┘
          │                  │                      │
          └──────────────────┼──────────────────────┘
                             │
                    ┌────────▼────────────────────────────────┐
                    │          POSTGRESQL (Partitioned)        │
                    │                                         │
                    │  Primary (Write)     Read Replicas (2)  │
                    │  ┌───────────┐       ┌───────────┐     │
                    │  │ pgvector  │──────▶│ Replica 1 │     │
                    │  │ pgbouncer │       │ (API)     │     │
                    │  └───────────┘       ├───────────┤     │
                    │                      │ Replica 2 │     │
                    │                      │ (Analytics│     │
                    │                      └───────────┘     │
                    │                                         │
                    │  Partitioned Tables:                    │
                    │  ├── product_listings (by marketplace)  │
                    │  ├── price_history    (by month)        │
                    │  ├── opportunities    (by status)       │
                    │  └── product_snapshots(by month)        │
                    └─────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │    API LAYER    │
                    │                 │
                    │ Load Balancer   │
                    │ ├── API-1      │
                    │ ├── API-2      │
                    │ └── API-3      │
                    │                 │
                    │ + Redis Cache   │
                    └─────────────────┘
```

---

## 3. Worker Architecture

### 3.1 Scraper Fleet

**Problem:** 1M listings/day = ~12 listings/second sustained.
At 0.5 req/sec per domain (rate limit), need parallelism across domains.

**Design:** Pool of stateless scraper workers, each assigned a
(marketplace, category, page_range) job from the `scrape_jobs` stream.

```
┌─────────────────────────────────────────────────┐
│              SCRAPER ORCHESTRATOR                │
│                                                  │
│  Input: marketplace_configs (YAML)               │
│  Output: scrape_jobs stream                      │
│                                                  │
│  Responsibilities:                               │
│  1. Generate jobs: (marketplace, category, page) │
│  2. Respect rate limits per domain               │
│  3. Distribute across proxy pool                 │
│  4. Track completion / retry failures            │
│  5. Adaptive scheduling (more jobs for hot cats) │
└─────────────────────────────────────────────────┘
         │
         │ scrape_jobs stream messages:
         │ {marketplace, category, page, proxy_id, priority}
         │
    ┌────▼────┐ ┌────────┐ ┌────────┐
    │Worker 1 │ │Worker 2│ │Worker N│   (N = 20 default)
    │         │ │        │ │        │
    │ Fetch   │ │ Fetch  │ │ Fetch  │   Each worker:
    │ Parse   │ │ Parse  │ │ Parse  │   1. Claim job from stream
    │ Publish │ │ Publish│ │ Publish│   2. Fetch page via StealthSession
    └─────────┘ └────────┘ └────────┘   3. Parse listings
                                        4. Publish to raw_listings
                                        5. ACK job
```

**Worker lifecycle:**
- Workers are stateless — crash and restart safely
- Each worker maintains one StealthSession (proxy + UA rotation)
- If blocked, worker reports proxy failure and claims next job
- Jobs have TTL — unclaimed jobs after 5 min are re-queued

### 3.2 Processor Pipeline (5 stages)

**Problem:** Sequential processing of 1M listings/day is impossible.
Embedding alone at 0.5s/listing = 500,000 seconds = 5.8 days.

**Solution:** Split into 5 independent stages connected by streams.
Each stage scales horizontally. Embedding stage uses GPU batching.

```
raw_listings → [NORMALIZE] → normalized → [EMBED] → embedded
    → [RESOLVE] → resolved → [ENRICH] → enriched → [STORE]

Stage 1: NORMALIZE  (CPU, fast — 10,000/sec)
  - Clean title, extract brand/model, classify category
  - Lightweight — single worker handles full load

Stage 2: EMBED  (GPU, batched — 2,000/sec with batch=256)
  - Batch 256 titles → single model.encode() call
  - GPU: 2,000/sec. CPU fallback: 50/sec (batch=32)
  - This is THE bottleneck — GPU required at scale

Stage 3: RESOLVE  (CPU + DB — 500/sec)
  - pgvector nearest-neighbor search
  - Match or create master_product
  - Needs DB connection but queries are indexed (HNSW)

Stage 4: ENRICH  (CPU + external APIs — 200/sec)
  - Fetch additional metadata if needed
  - Currency conversion
  - Seller reputation lookup from cache

Stage 5: STORE  (DB write — 1,000/sec with batch insert)
  - Batch INSERT into product_listings
  - Batch INSERT into price_history
  - Uses COPY for bulk loads
```

**Throughput math for 1M/day:**
```
1,000,000 / 86,400 = 11.6 listings/sec sustained
Peak (4 hour scrape window): 1,000,000 / 14,400 = 69.4/sec

Stage capacities (per worker):
  Normalize:  10,000/sec  → 1 worker sufficient
  Embed:       2,000/sec  → 1 GPU worker sufficient
  Resolve:       500/sec  → 1 worker sufficient
  Enrich:        200/sec  → 1 worker sufficient
  Store:       1,000/sec  → 1 worker sufficient

Headroom: 3× peak capacity with single workers.
Scale to 2-3 workers per stage for 5M/day.
```

### 3.3 Analytics Workers

**Problem:** Opportunity detection currently runs in the API process,
blocking HTTP requests for 10-30 seconds.

**Solution:** Separate analytics workers triggered by schedule or events.

```
┌──────────────────────────────────────────────┐
│           ANALYTICS WORKERS                   │
│                                               │
│  Worker 1: OPPORTUNITY DETECTOR               │
│    Trigger: Every 15 min (or on batch complete)│
│    Input: master_products with 2+ marketplaces │
│    Output: opportunities table (upsert)        │
│    Strategy: Process products in batches of 100│
│              Parallel fee calc across pairs     │
│                                               │
│  Worker 2: TREND ANALYZER                     │
│    Trigger: Every 6 hours                     │
│    Input: product_snapshots time series        │
│    Output: trend_score, velocity on master_products│
│                                               │
│  Worker 3: CATEGORY RANKER                    │
│    Trigger: After opportunity detection        │
│    Input: opportunities + master_products      │
│    Output: category_arbitrage_stats            │
│                                               │
│  Worker 4: LIFECYCLE TRACKER                  │
│    Trigger: Every 30 min                      │
│    Input: opportunity_history snapshots         │
│    Output: decay_rate, urgency_score           │
│                                               │
│  Worker 5: NOTIFIER                           │
│    Trigger: On new opportunity (stream)         │
│    Input: opportunities stream                  │
│    Output: Telegram/email notifications         │
│    Strategy: Fire-and-forget, no blocking API   │
└──────────────────────────────────────────────┘
```

---

## 4. Message Queue Design

### 4.1 Stream Topology

```
STREAM: scrape_jobs
  Group: scraper_fleet
  Format: {marketplace, category, page, priority, proxy_hint, created_at}
  Retention: 1 hour (jobs are ephemeral)
  Consumer count: 20

STREAM: raw_listings
  Group: normalizer_group
  Format: {title, price, currency, url, marketplace_id, ...metadata}
  Retention: 24 hours
  Consumer count: 2
  Backpressure: If pending > 50,000, pause scraper orchestrator

STREAM: normalized
  Group: embedder_group
  Format: {normalized_title, brand, model, category, original_data}
  Retention: 12 hours
  Consumer count: 2 (GPU workers)

STREAM: resolved
  Group: enricher_group
  Format: {master_product_id, listing_data, embedding}
  Retention: 12 hours
  Consumer count: 2

STREAM: dead_letters
  Group: none (manual inspection)
  Format: {original_stream, message_id, error, timestamp, retry_count}
  Retention: 7 days

STREAM: metrics
  Group: monitor_group
  Format: {worker_id, stage, event, duration_ms, timestamp}
  Retention: 48 hours
```

### 4.2 Backpressure & Flow Control

```
Scraper Orchestrator
  │
  ├── Check: XLEN raw_listings > 50,000?
  │     YES → Pause job generation for 60s
  │     NO  → Continue
  │
  ├── Check: XLEN normalized > 20,000?
  │     YES → Reduce scraper concurrency by 50%
  │     NO  → Normal concurrency
  │
  └── Check: DB connection pool utilization > 80%?
        YES → Pause all writes, drain queues
        NO  → Normal operation
```

### 4.3 Dead Letter Queue

```python
# When any stage fails to process a message after 3 retries:
async def send_to_dlq(redis, original_stream, msg_id, error):
    await redis.xadd("dead_letters", {
        "original_stream": original_stream,
        "message_id": msg_id,
        "error": str(error),
        "retry_count": "3",
        "timestamp": datetime.utcnow().isoformat(),
    })
    # ACK original to prevent re-processing
    await redis.xack(original_stream, group, msg_id)
```

---

## 5. Scraping Orchestration

### 5.1 Job Generation

```yaml
# marketplace_configs.yaml
marketplaces:
  amazon_mx:
    base_url: "https://www.amazon.com.mx"
    categories: 45
    pages_per_category: 10
    rate_limit: 0.4 req/sec
    priority: high
    proxy_required: true

  mercadolibre_ar:
    base_url: "https://www.mercadolibre.com.ar"
    categories: 60
    pages_per_category: 8
    rate_limit: 0.5 req/sec
    priority: high
    proxy_required: false

  mercadolibre_mx:
    base_url: "https://www.mercadolibre.com.mx"
    categories: 55
    pages_per_category: 8
    rate_limit: 0.5 req/sec
    priority: medium

  ebay:
    base_url: "https://www.ebay.com"
    categories: 30
    pages_per_category: 5
    rate_limit: 0.3 req/sec
    priority: low
    proxy_required: true

# Total jobs per cycle:
# amazon: 45 × 10 = 450 page fetches
# ml_ar:  60 × 8  = 480
# ml_mx:  55 × 8  = 440
# ebay:   30 × 5  = 150
# Total: 1,520 page fetches × ~50 listings/page = 76,000 listings
# Run 13 cycles/day = ~1M listings
```

### 5.2 Adaptive Scheduling

```
HIGH PRIORITY (every 30 min):
  - Categories with active opportunities (price-sensitive)
  - Trending categories (velocity_7d > 50)
  - New marketplace categories (first 7 days)

MEDIUM PRIORITY (every 2 hours):
  - All configured categories
  - Standard refresh cycle

LOW PRIORITY (every 6 hours):
  - Low-activity categories
  - Categories with no opportunities in 7 days

DISABLED:
  - Categories with 0 products after 3 cycles → auto-disable
```

### 5.3 Proxy Management

```
┌────────────────────────────────────────┐
│          PROXY ORCHESTRATOR            │
│                                        │
│  Pool: 50 rotating residential proxies │
│                                        │
│  Assignment strategy:                  │
│  ├── Amazon  → Residential (US/MX)    │
│  ├── ML_AR   → Datacenter (AR) OK     │
│  ├── ML_MX   → Datacenter (MX) OK     │
│  └── eBay    → Residential (US) only  │
│                                        │
│  Health tracking:                      │
│  ├── Success rate per proxy (>90% OK)  │
│  ├── Latency per proxy (<5s OK)        │
│  ├── Ban rate per proxy (<5% OK)       │
│  └── Auto-rotate on 3 consecutive fail │
│                                        │
│  Cost optimization:                    │
│  ├── Use datacenter for ML (cheaper)   │
│  ├── Residential only for Amazon/eBay  │
│  └── Budget: ~$200/month for 1M/day   │
└────────────────────────────────────────┘
```

---

## 6. Database Scaling

### 6.1 Table Partitioning

```sql
-- product_listings: partition by marketplace (even query distribution)
CREATE TABLE product_listings (
    id          BIGSERIAL,
    marketplace_id TEXT NOT NULL,
    ...
) PARTITION BY LIST (marketplace_id);

CREATE TABLE product_listings_amazon
    PARTITION OF product_listings FOR VALUES IN ('amazon');
CREATE TABLE product_listings_ml_ar
    PARTITION OF product_listings FOR VALUES IN ('mercadolibre_ar');
CREATE TABLE product_listings_ml_mx
    PARTITION OF product_listings FOR VALUES IN ('mercadolibre_mx');
CREATE TABLE product_listings_ebay
    PARTITION OF product_listings FOR VALUES IN ('ebay');
-- Add new partitions as marketplaces are added

-- price_history: partition by month (time-series optimization)
CREATE TABLE price_history (
    id          BIGSERIAL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ...
) PARTITION BY RANGE (recorded_at);

CREATE TABLE price_history_2026_01
    PARTITION OF price_history
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
-- Auto-create monthly partitions via pg_partman

-- opportunities: partition by status (hot/cold separation)
CREATE TABLE opportunities (
    id     BIGSERIAL,
    status TEXT NOT NULL DEFAULT 'active',
    ...
) PARTITION BY LIST (status);

CREATE TABLE opportunities_active
    PARTITION OF opportunities FOR VALUES IN ('active');
CREATE TABLE opportunities_expired
    PARTITION OF opportunities FOR VALUES IN ('expired');
CREATE TABLE opportunities_taken
    PARTITION OF opportunities FOR VALUES IN ('taken');
```

### 6.2 Connection Pooling (PgBouncer)

```ini
; pgbouncer.ini
[databases]
compraventa = host=primary port=5432 dbname=compraventa
compraventa_ro = host=replica1 port=5432 dbname=compraventa

[pgbouncer]
listen_port = 6432
pool_mode = transaction     ; release conn after each transaction
max_client_conn = 500       ; total client connections
default_pool_size = 30      ; connections per database per user
reserve_pool_size = 5       ; extra connections for burst
reserve_pool_timeout = 3    ; seconds to wait before using reserve
```

### 6.3 Read Replica Routing

```
WRITES (Primary):
  ├── INSERT product_listings
  ├── INSERT price_history
  ├── UPSERT opportunities
  └── UPDATE master_products

READS (Replica 1 — API):
  ├── GET /opportunities (list, detail, stats)
  ├── GET /categories
  ├── GET /discovery/trending
  └── GET /health

READS (Replica 2 — Analytics):
  ├── Opportunity detection (read phase)
  ├── Trend analysis queries
  ├── Category aggregation
  └── Monitoring dashboards
```

### 6.4 Index Strategy

```sql
-- Hot path: opportunity listing (API reads)
CREATE INDEX CONCURRENTLY idx_opp_active_score
    ON opportunities (opportunity_score DESC)
    WHERE status = 'active';

-- Hot path: product resolution (embedding search)
CREATE INDEX ON master_products
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 128);
    -- m=32 (up from 16): better recall for 500K+ vectors
    -- ef_construction=128: slower build, better quality

-- Hot path: listings per product per marketplace
CREATE INDEX CONCURRENTLY idx_listings_product_mp
    ON product_listings (master_product_id, marketplace_id, price);

-- Aggregation: price history for stability calculation
CREATE INDEX CONCURRENTLY idx_price_history_url_recent
    ON price_history (listing_url, recorded_at DESC)
    INCLUDE (price, currency);

-- Cleanup: find stale opportunities
CREATE INDEX CONCURRENTLY idx_opp_stale
    ON opportunities (created_at)
    WHERE status = 'active';
```

### 6.5 Data Retention

```
product_listings:  Keep 90 days, archive older to cold storage
price_history:     Keep 180 days, aggregate to daily after 30 days
opportunities:     Keep active indefinitely, archive expired after 30 days
product_snapshots: Keep 365 days, aggregate to weekly after 90 days
opportunity_history: Keep 90 days
dead_letters:      Keep 7 days
```

---

## 7. Monitoring

### 7.1 Metrics to Track

```
SCRAPING METRICS (per marketplace, per category):
  ├── scrape_jobs_total          (counter)
  ├── scrape_jobs_failed         (counter)
  ├── scrape_duration_seconds    (histogram)
  ├── listings_scraped_total     (counter)
  ├── proxy_success_rate         (gauge, per proxy)
  ├── proxy_latency_p99          (histogram)
  └── block_detections_total     (counter, by type)

PIPELINE METRICS (per stage):
  ├── messages_processed_total   (counter)
  ├── messages_failed_total      (counter)
  ├── processing_duration_ms     (histogram)
  ├── batch_size                 (histogram)
  ├── consumer_lag               (gauge) ← CRITICAL
  └── stream_length              (gauge)

DATABASE METRICS:
  ├── connection_pool_active     (gauge)
  ├── connection_pool_waiting    (gauge)
  ├── query_duration_p99         (histogram)
  ├── rows_inserted_total        (counter, per table)
  ├── table_size_bytes           (gauge, per table)
  ├── index_size_bytes           (gauge)
  └── replication_lag_bytes      (gauge)

BUSINESS METRICS:
  ├── active_opportunities       (gauge)
  ├── opportunities_created      (counter)
  ├── opportunities_expired      (counter)
  ├── avg_opportunity_score      (gauge)
  ├── avg_profit_usd             (gauge)
  ├── master_products_total      (gauge)
  ├── listings_per_product_avg   (gauge)
  └── notifications_sent         (counter)
```

### 7.2 Alerting Rules

```yaml
alerts:
  # Pipeline health
  - name: consumer_lag_critical
    condition: consumer_lag > 100,000 for 5m
    severity: critical
    action: Scale up processor workers

  - name: scrape_failure_rate_high
    condition: scrape_jobs_failed / scrape_jobs_total > 0.3 for 10m
    severity: warning
    action: Check proxy health, marketplace status

  - name: dead_letters_spike
    condition: rate(dead_letters) > 100/min for 5m
    severity: critical
    action: Page on-call, check parsing logic

  # Database health
  - name: db_connection_exhaustion
    condition: connection_pool_waiting > 10 for 2m
    severity: critical
    action: Scale pgbouncer pool, check slow queries

  - name: replication_lag
    condition: replication_lag_bytes > 100MB for 5m
    severity: warning
    action: Check replica health

  - name: table_bloat
    condition: table_size_bytes > 50GB
    severity: warning
    action: Run VACUUM, check retention policy

  # Business health
  - name: no_new_opportunities
    condition: rate(opportunities_created) == 0 for 2h
    severity: warning
    action: Check scraper output, opportunity thresholds

  - name: opportunity_quality_drop
    condition: avg_opportunity_score < 30 for 1h
    severity: info
    action: Review scoring weights, check data quality
```

### 7.3 Dashboard Layout

```
┌─────────────────────────────────────────────────────┐
│  SYSTEM HEALTH                              [LIVE]  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
│  │ Scrapers │ │ Pipeline │ │ Database │ │  API   ││
│  │   ● OK   │ │   ● OK   │ │   ● OK   │ │  ● OK ││
│  │ 18/20 up │ │ lag: 234 │ │ pool: 60%│ │ p99:80ms│
│  └──────────┘ └──────────┘ └──────────┘ └────────┘│
├─────────────────────────────────────────────────────┤
│  THROUGHPUT (24h)                                   │
│  ┌─────────────────────────────────────────┐       │
│  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │       │
│  │ Scraped: 1,023,456  Processed: 1,020,111│       │
│  │ Resolved: 987,234   Stored: 987,234     │       │
│  │ Drop rate: 3.5%     DLQ: 12 msgs        │       │
│  └─────────────────────────────────────────┘       │
├─────────────────────────────────────────────────────┤
│  OPPORTUNITIES                                      │
│  Active: 1,847  New (24h): 234  Expired: 89        │
│  Avg Score: 62.3  Avg Profit: $47.20  Avg ROI: 34% │
│  Top Category: Gaming Consoles (score: 78.5)        │
└─────────────────────────────────────────────────────┘
```

---

## 8. Failure Recovery

### 8.1 Failure Modes & Recovery

```
FAILURE: Scraper worker crash
  Detection: Worker heartbeat missing for 30s
  Impact: Unclaimed jobs in scrape_jobs stream
  Recovery: Redis consumer group auto-reassigns pending
            messages to other workers after claim timeout.
            Orchestrator detects gap and re-enqueues.
  Data loss: None (jobs are idempotent)

FAILURE: Processor worker crash
  Detection: Consumer group pending entries grow
  Impact: Messages stuck in "pending" state
  Recovery: Supervisor restarts worker.
            On startup, worker calls XAUTOCLAIM to reclaim
            messages pending > 60 seconds.
  Data loss: None (at-least-once delivery)

FAILURE: Redis crash / restart
  Detection: Connection refused from workers
  Impact: All streams lost (unless persistence enabled)
  Recovery: Workers reconnect with exponential backoff.
            Scraper orchestrator re-generates current cycle jobs.
            In-flight messages are re-scraped (idempotent).
  Prevention: Enable AOF persistence. Redis Sentinel for HA.

FAILURE: PostgreSQL primary down
  Detection: Connection errors from writers
  Impact: No writes. Pipeline backs up in Redis streams.
  Recovery: Promote replica to primary (automatic with Patroni).
            Workers reconnect via pgbouncer.
            Redis streams buffer ~2 hours of data safely.
  Data loss: Depends on replication lag at failover time.

FAILURE: Stale/corrupted embeddings
  Detection: Product resolution match rate drops below 50%
  Impact: Duplicate master_products. Fragmented clusters.
  Recovery: Run deduplication job:
            1. Find master_products with cosine_distance < 0.1
            2. Merge listings to canonical product
            3. Rebuild HNSW index
  Prevention: Monitor match rate. Alert on anomalies.

FAILURE: Marketplace blocks all proxies
  Detection: Block detection rate > 80% for marketplace
  Impact: No new data for that marketplace
  Recovery: 1. Rotate to fresh proxy pool
            2. Increase delays (reduce aggression)
            3. Fall back to Playwright/browser mode
            4. Alert ops team for manual intervention
  Mitigation: Never depend on single marketplace for
              opportunity detection.

FAILURE: Embedding model file corrupted / unavailable
  Detection: Model load fails on worker startup
  Impact: No new embeddings. Pipeline stalls at stage 2.
  Recovery: Workers retry model download 3×.
            Fall back to cached model from shared volume.
            Alert if all retries fail.
  Prevention: Pin model version. Store in container image.
```

### 8.2 Idempotency Guarantees

```
SCRAPING:
  Key: (marketplace_id, url)
  Strategy: UPSERT on product_listings.url (unique constraint)
  Effect: Re-scraping same URL updates price, doesn't duplicate

PRODUCT RESOLUTION:
  Key: (embedding cosine distance < threshold)
  Strategy: Nearest-neighbor match → reuse existing master_product
  Effect: Same product always resolves to same master, regardless
          of title variations

OPPORTUNITY DETECTION:
  Key: (master_product_id, buy_marketplace, sell_marketplace, status='active')
  Strategy: Unique constraint. UPDATE if exists, INSERT if new.
  Effect: Re-running detection updates scores, doesn't create duplicates

NOTIFICATIONS:
  Key: (opportunity_id, user_id, created_at date)
  Strategy: Check alerts_sent table before sending
  Effect: User never gets duplicate alerts for same opportunity
```

### 8.3 Graceful Degradation

```
Level 0: NORMAL
  All systems operational. Full throughput.

Level 1: DEGRADED — One marketplace down
  Action: Continue with remaining marketplaces.
          Mark affected opportunities as "stale" not "expired".
          Alert users: "Amazon data may be 2h delayed".

Level 2: DEGRADED — Redis unavailable
  Action: Scrapers buffer to local disk (JSON lines).
          API serves from DB cache (read-only mode).
          On Redis recovery, replay buffered data.

Level 3: DEGRADED — DB replica down
  Action: Route all reads to primary.
          Increase pgbouncer pool size temporarily.
          Disable analytics workers (reduce read load).

Level 4: CRITICAL — DB primary down
  Action: Promote replica. Read-only mode until promotion.
          Pipeline pauses (Redis buffers).
          API returns cached data with "data may be stale" header.

Level 5: CRITICAL — Full outage
  Action: Static maintenance page.
          All workers enter retry loop with backoff.
          Alert on-call immediately.
```

---

## 9. Implementation Phases

### Phase 1: Foundation (Current → 10K/day)
- [x] Redis Streams pipeline
- [x] pgvector product resolution
- [x] Basic opportunity detection
- [x] Stealth scraping (proxy, UA rotation)
- [ ] PgBouncer connection pooling
- [ ] Consumer lag monitoring
- [ ] Dead letter queue

### Phase 2: Scale (10K → 100K/day)
- [ ] Scraper orchestrator (job queue model)
- [ ] Pipeline stage separation (5 streams)
- [ ] GPU embedding batching
- [ ] Table partitioning (price_history, listings)
- [ ] Read replica for API
- [ ] Backpressure controls
- [ ] Prometheus + Grafana monitoring

### Phase 3: Production (100K → 1M/day)
- [ ] Horizontal worker scaling (Kubernetes)
- [ ] Redis Cluster (or Sentinel for HA)
- [ ] Automated partition management (pg_partman)
- [ ] Adaptive scrape scheduling
- [ ] Data retention automation
- [ ] Alerting (PagerDuty / Opsgenie)
- [ ] Load testing (Locust)

### Phase 4: Intelligence (1M+/day)
- [ ] ML-based product clustering (replace threshold)
- [ ] Price prediction models
- [ ] Demand forecasting
- [ ] Automated opportunity execution
- [ ] Multi-region deployment
