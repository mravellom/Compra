# CompraVenta - Comprehensive Test Plan

**Version:** 1.0
**Date:** 2026-03-15
**Author:** QA Engineering / SRE
**System:** CompraVenta - Opportunity Radar

---

## Table of Contents

1. [Test Strategy Overview](#1-test-strategy-overview)
2. [Test Environments](#2-test-environments)
3. [Unit Testing Plan](#3-unit-testing-plan)
4. [Integration Testing](#4-integration-testing)
5. [End-to-End Tests](#5-end-to-end-tests)
6. [Data Quality Tests](#6-data-quality-tests)
7. [Opportunity Engine Validation](#7-opportunity-engine-validation)
8. [Performance Testing](#8-performance-testing)
9. [Stress Testing](#9-stress-testing)
10. [Fault Tolerance Testing](#10-fault-tolerance-testing)
11. [Security Testing](#11-security-testing)
12. [Observability Tests](#12-observability-tests)
13. [Automation Strategy](#13-automation-strategy)
14. [Release Checklist](#14-release-checklist)

---

## 1. Test Strategy Overview

### Philosophy

The testing strategy follows the **Test Pyramid** adapted for a distributed data pipeline:

```
        /  E2E  \          ← Few, expensive, full-pipeline
       / Integr. \         ← Cross-boundary validation
      /   Unit    \        ← Many, fast, deterministic
     /______________\
```

### Guiding Principles

| Principle | Description |
|-----------|-------------|
| **Financial Accuracy First** | Profit, ROI, and fee calculations are the highest-priority test targets. A 1% error in fees can cascade into thousands of bad recommendations. |
| **Data Pipeline Idempotency** | Every stage must be safely re-runnable. Tests must verify that re-processing the same listing produces the same result. |
| **Boundary Testing** | The system spans 8 marketplaces, 5 currencies, and 29 trade routes. Edge cases at marketplace/currency boundaries are critical. |
| **Deterministic by Default** | Unit and integration tests must not depend on network, external APIs, or timing. Use fixtures, mocks, and seeded data. |
| **Performance as a Feature** | The processor targets >15k listings/sec. Performance regressions are bugs. |

### Test Categories

| Category | Count Target | Execution Time | Trigger |
|----------|-------------|----------------|---------|
| Unit | 200+ | < 30s | Every commit |
| Integration | 80+ | < 2 min | Every commit |
| E2E | 15+ | < 5 min | PR merge |
| Performance | 10+ | < 10 min | Nightly / pre-release |
| Stress | 5+ | < 15 min | Pre-release |
| Security | 10+ | < 3 min | Weekly / pre-release |

---

## 2. Test Environments

### 2.1 Local Development

```
┌──────────────────────────────────────────────┐
│  Developer Machine                           │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ PostgreSQL│  │  Redis   │  │  Model    │  │
│  │ :5433     │  │  :6379   │  │ (CPU)     │  │
│  │ (Docker)  │  │ (Docker) │  │           │  │
│  └──────────┘  └──────────┘  └───────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ FastAPI   │  │ Angular  │  │ Processor │  │
│  │ :8000     │  │ :4200    │  │           │  │
│  └──────────┘  └──────────┘  └───────────┘  │
└──────────────────────────────────────────────┘
```

| Component | Configuration |
|-----------|--------------|
| PostgreSQL | Docker `pgvector/pgvector:pg16`, port 5433, DB `compraventa_test` |
| Redis | Docker `redis:7-alpine`, port 6379, DB index 1 (isolated) |
| Embeddings | CPU mode, batch size 64 |
| FX Rates | Mocked with fixed rates (USD/MXN=17.5, USD/ARS=900, etc.) |

### 2.2 CI Environment

```yaml
# docker-compose.test.yml
services:
  db_test:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: compraventa_test
      POSTGRES_PASSWORD: postgres
    ports: ["5433:5432"]
    tmpfs: /var/lib/postgresql/data  # RAM-backed for speed

  redis_test:
    image: redis:7-alpine
    ports: ["6379:6379"]
```

- **Isolated DB**: `compraventa_test` with fresh schema per test run (migrations applied at setup)
- **Redis DB 1**: Separate from dev data
- **No external network**: All FX rates, marketplace pages mocked
- **Embedding model**: Cached in CI layer to avoid download on each run

### 2.3 Staging / Production-Like

| Aspect | Configuration |
|--------|--------------|
| Data volume | 50k+ master products, 200k+ listings |
| PostgreSQL | Same version (PG16 + pgvector), 4GB RAM |
| Redis | Same version, persistence enabled |
| Scrapers | Run against saved HTML snapshots (not live) |
| FX Rates | Real API with fallback cache |
| Duration | Full pipeline run with timing assertions |

---

## 3. Unit Testing Plan

### 3.1 Scraper Parsing

**Module:** `scraper/price_parser.py`

| Test ID | Test Case | Input | Expected |
|---------|-----------|-------|----------|
| `UP-001` | Standard USD price | `"$199.99"` | `199.99` |
| `UP-002` | MXN with thousands dot | `"$1.299,00"`, marketplace=`mercadolibre_mx` | `1299.00` |
| `UP-003` | ARS with dot separator | `"$45.999"`, marketplace=`mercadolibre_ar` | `45999.0` |
| `UP-004` | Price range (take first) | `"$199 to $249"` | `199.0` |
| `UP-005` | Euro symbol | `"€89,99"` | `89.99` |
| `UP-006` | GBP symbol | `"£149.99"` | `149.99` |
| `UP-007` | Yen symbol | `"¥1,299"` | `1299.0` |
| `UP-008` | Empty string | `""` | `None` |
| `UP-009` | Non-numeric text | `"Free"` | `None` |
| `UP-010` | Negative price | `"-$10.00"` | `None` |
| `UP-011` | Zero price | `"$0.00"` | `None` or `0.0` (verify behavior) |
| `UP-012` | Whitespace/newlines | `"  $ 1,299 . 99  "` | `1299.99` |
| `UP-013` | CLP large numbers | `"$899.990"`, marketplace=`mercadolibre_cl` | `899990.0` |

**Module:** `scraper/dedup.py`

| Test ID | Test Case | Expected |
|---------|-----------|----------|
| `UD-001` | Identical listing produces same hash | `hash(A) == hash(A)` |
| `UD-002` | Different price produces different hash | `hash(A_100) != hash(A_200)` |
| `UD-003` | Title case insensitive | `hash("iPhone 15") == hash("iphone 15")` |
| `UD-004` | Different marketplace, same title | `hash(A_ebay) != hash(A_amazon)` |
| `UD-005` | Batch filter removes duplicates | `filter([A, A, B])` → `[A, B]` |
| `UD-006` | Filter preserves order | First occurrence kept |
| `UD-007` | TTL-based expiry | After 24h, same listing passes again |

**Module:** `scraper/schemas.py` (existing: 8 tests)

| Test ID | Test Case | Expected |
|---------|-----------|----------|
| `US-001` | Valid listing creation | No error |
| `US-002` | Invalid URL rejected | `ValidationError` |
| `US-003` | Negative price rejected | `ValidationError` |
| `US-004` | Missing required fields | `ValidationError` |
| `US-005` | `to_stream_dict()` returns all-string values | All `isinstance(v, str)` |
| `US-006` | Default condition is "new" | `listing.condition == "new"` |
| `US-007` | Seller rating bounds (0.0-5.0) | Values outside range rejected |
| `US-008` | `scraped_at` auto-populated | Timestamp present |

### 3.2 Normalization

**Module:** `processor/normalizer.py` (existing: 19 tests)

| Test ID | Test Case | Input | Expected |
|---------|-----------|-------|----------|
| `UN-001` | Lowercase conversion | `"Sony WH-1000XM4"` | `"sony wh1000xm4"` |
| `UN-002` | Accent removal | `"Audifonos Inalámbricos"` | `"audifonos inalambricos"` |
| `UN-003` | Stop word removal | `"Funda para iPhone 15 Pro"` | No `"para"` |
| `UN-004` | Hyphen normalization | `"WH-1000XM4"` == `"WH1000XM4"` | Same output |
| `UN-005` | Brand extraction - known | `"sony wh1000xm4"` | brand=`"sony"` |
| `UN-006` | Brand extraction - unknown | `"generic earbuds"` | brand=`None` |
| `UN-007` | Model extraction | `"apple iphone 15 pro max"` | model=`"iphone 15 pro max"` |
| `UN-008` | Product type detection | `"audifonos bluetooth"` | type=`"headphones"` |
| `UN-009` | Unicode normalization (NFC/NFD) | Various forms | Consistent output |
| `UN-010` | Empty string | `""` | `""` |
| `UN-011` | Special characters stripped | `"iPhone™ 15 Pro®"` | No `™®` |
| `UN-012` | Multiple spaces collapsed | `"sony   wh1000xm4"` | `"sony wh1000xm4"` |
| `UN-013` | Emoji removal | `"🔥iPhone 15🔥"` | `"iphone 15"` |
| `UN-014` | All 48 known brands recognized | Loop test | All match |
| `UN-015` | Model canonical forms | `"xm4"` → `"wh-1000xm4"` | Mapped correctly |

### 3.3 Embedding Generation

**Module:** `processor/embeddings.py`

| Test ID | Test Case | Expected |
|---------|-----------|----------|
| `UE-001` | Output dimension is 384 | `len(embedding) == 384` |
| `UE-002` | Output is normalized (L2 norm ≈ 1.0) | `np.linalg.norm(v) ≈ 1.0` |
| `UE-003` | Similar titles → high cosine similarity | `cos("iPhone 15 Pro", "iPhone 15 Pro Max") > 0.8` |
| `UE-004` | Different products → low similarity | `cos("iPhone 15", "Samsung TV 55") < 0.5` |
| `UE-005` | Batch output matches single | `batch([a,b])[0] == single(a)` |
| `UE-006` | Empty string handling | No crash, returns valid vector |
| `UE-007` | Very long title (500+ chars) | Truncated gracefully, valid vector |
| `UE-008` | Multilingual consistency | `cos("audifonos", "headphones") > 0.6` |
| `UE-009` | Deterministic output | Same input → same vector (no randomness) |
| `UE-010` | Batch size 1 works | No index errors |

### 3.4 Cosine Similarity Matching

**Module:** `processor/resolver.py` (existing: 10+ tests)

| Test ID | Test Case | Expected |
|---------|-----------|----------|
| `UR-001` | Exact match returns `is_new=False` | Similarity ≈ 1.0 |
| `UR-002` | Brand+model match at 0.60 threshold | Links to existing product |
| `UR-003` | Brand-only match at 0.75 threshold | Links to existing product |
| `UR-004` | Below threshold → new product | `is_new=True` |
| `UR-005` | Price ratio > 3.5x → rejected match | Creates new product |
| `UR-006` | Cache hit returns same result | `metrics.cache_hits += 1` |
| `UR-007` | Cache TTL expiry (600s) | Re-computes after expiry |
| `UR-008` | Batch resolve: N items → N results | No missing/extra |
| `UR-009` | Concurrent batch resolve (thread safe) | No data corruption |
| `UR-010` | Empty product index → all new | All `is_new=True` |
| `UR-011` | Stable hash: same title → same hash | Deterministic |
| `UR-012` | 10k products in index → correct top match | Verified with known data |

### 3.5 Profit Calculation

**Module:** `api/opportunity.py` — `calculate_profit()`

| Test ID | Test Case | Buy | Sell | Route | Expected Assertions |
|---------|-----------|-----|------|-------|---------------------|
| `UC-001` | Domestic Amazon→ML MX | $100 | $200 | MX→MX | `marketplace_fee ≈ 32.0`, `sell_tax ≈ 32.0` (16% IVA) |
| `UC-002` | Amazon US→ML MX (cross-border) | $100 | $200 | US→MX | import_tax > 0, international_shipping = 15.0 |
| `UC-003` | Amazon US→ML AR (high import) | $100 | $200 | US→AR | import_tax_rate = 0.50, shipping = 25.0 |
| `UC-004` | Amazon US→ML CL | $100 | $200 | US→CL | import_tax_rate = 0.19, shipping = 22.0 |
| `UC-005` | eBay domestic | $100 | $200 | US→US | commission = 13.12%, domestic_shipping = $8 |
| `UC-006` | AliExpress→ML MX | $50 | $150 | CN→MX | commission = 8%, cross-border fees |
| `UC-007` | Zero profit edge | $100 | $100 | MX→MX | net_profit < 0 (fees make it negative) |
| `UC-008` | Free shipping buy side | $100 | $200 | US→MX | domestic_shipping = 0 on buy |
| `UC-009` | Free shipping sell side | $100 | $200 | US→MX | domestic_shipping = 0 on sell |
| `UC-010` | Very high price ($19999) | $10000 | $19999 | US→MX | No overflow, fees proportional |
| `UC-011` | Very low price ($1.50) | $1.00 | $1.50 | MX→MX | Minimum fee handling |
| `UC-012` | ROI calculation | $100 | $300 | MX→MX | `roi = net_profit / buy_price` |
| `UC-013` | Margin calculation | $100 | $300 | MX→MX | `margin = net_profit / sell_price` |
| `UC-014` | All 8 marketplaces as sell side | Vary | $200 | Vary | Correct commission per marketplace |
| `UC-015` | Unknown marketplace fallback | $100 | $200 | `??→??` | Uses default cross-border fees |

### 3.6 Opportunity Scoring

**Module:** `api/opportunity.py` — `score_opportunity()` + `api/scoring.py`

| Test ID | Test Case | Expected |
|---------|-----------|----------|
| `UO-001` | Perfect opportunity (high profit, low competition) | Score > 85 |
| `UO-002` | Marginal opportunity ($1 profit, 1% ROI) | Score < 30 |
| `UO-003` | High profit but high competition (30+ competitors) | Score penalized by 10+ pts |
| `UO-004` | Score capped at 100 | Never exceeds 100 |
| `UO-005` | Score floor at 0 | Never goes negative |
| `UO-006` | Thin margin penalty (< 8%) | -10 to -15 points |
| `UO-007` | High price ratio penalty (> 6x) | -15 points |
| `UO-008` | Low seller rating penalty (< 3.0) | -5 points |
| `UO-009` | Confidence: score >= 75 → "high" | Correct label |
| `UO-010` | Confidence: score 50-74 → "medium" | Correct label |
| `UO-011` | Confidence: score < 50 → "low" | Correct label |
| `UO-012` | Weight sum equals 1.0 | `0.20+0.15+0.10+0.15+0.10+0.10+0.10+0.10 = 1.0` |
| `UO-013` | Cross-border risk scaling applied | Domestic < Easy < Medium < Hard |
| `UO-014` | Demand normalization | 0 sales → 0, many sales → ~100 |
| `UO-015` | ROI normalization curve | `roi=0.5 → ~76`, `roi=1.0 → ~92` |

---

## 4. Integration Testing

### 4.1 Scraper → Redis

| Test ID | Test Case | Setup | Validation |
|---------|-----------|-------|------------|
| `IS-001` | Batch publish 100 listings | Generate 100 `RawListing` objects | Redis XLEN increases by 100 |
| `IS-002` | Dedup filter before publish | 100 listings, 20 duplicates | Redis XLEN increases by 80 |
| `IS-003` | Stream format validation | Publish 1 listing | `XRANGE` returns all required fields as strings |
| `IS-004` | Large batch (10k listings) | Generate 10k listings | All arrive, no data loss |
| `IS-005` | Redis connection failure | Stop Redis, attempt publish | Graceful error, no crash |
| `IS-006` | Concurrent publishers | 3 scraper instances publish | All messages arrive, no corruption |

### 4.2 Redis → Processor

| Test ID | Test Case | Setup | Validation |
|---------|-----------|-------|------------|
| `IR-001` | Consumer group reads messages | Publish 50 listings to stream | Processor reads all 50 |
| `IR-002` | Message acknowledgment | Process 50 listings | All 50 ACKed, pending count = 0 |
| `IR-003` | Batch collection (256 items) | Publish 300 listings | 1 batch of 256, 1 batch of 44 |
| `IR-004` | Malformed message skipped | Publish 1 invalid + 9 valid | 9 processed, 1 skipped |
| `IR-005` | Backpressure: queue full (1024) | Flood stream with 5k messages | Reader pauses, no OOM |
| `IR-006` | Consumer group recovery | Kill processor mid-batch, restart | Pending messages re-delivered |

### 4.3 Processor → PostgreSQL

| Test ID | Test Case | Setup | Validation |
|---------|-----------|-------|------------|
| `IP-001` | New product created | Listing with no match | `master_products` row + `product_listings` row |
| `IP-002` | Existing product linked | Listing matching existing product | `product_listings.master_product_id` correct |
| `IP-003` | UPSERT idempotency | Process same listing twice | 1 row in `product_listings` (not 2) |
| `IP-004` | Price history recorded | Process listing | `price_history` row with correct price |
| `IP-005` | Parallel flush consistency | 256 listings in batch | All listings + all price_history rows present |
| `IP-006` | COPY protocol works | 1000 listings | `price_history` count = 1000 |
| `IP-007` | Embedding stored correctly | New product | `master_products.embedding` has 384 dimensions |
| `IP-008` | Transaction rollback on error | Inject DB error mid-batch | No partial writes |

### 4.4 PostgreSQL → Opportunity Engine

| Test ID | Test Case | Setup | Validation |
|---------|-----------|-------|------------|
| `IO-001` | 2-marketplace product detected | Product with listings on Amazon + ML MX | Opportunity created |
| `IO-002` | Single-marketplace ignored | Product with listings only on Amazon | No opportunity |
| `IO-003` | Stale listings excluded (>48h) | Listing scraped 72h ago | Not included in pairs |
| `IO-004` | Outlier price excluded | 5 listings + 1 at 10x price | Outlier excluded from analysis |
| `IO-005` | Seller rating < 1.5 excluded | Listing with rating 1.0 | Listing excluded |
| `IO-006` | Variant mismatch excluded | "iPhone 64GB" vs "iPhone 256GB" | Not paired |
| `IO-007` | Condition mismatch excluded | "new" vs "used" | Not paired |
| `IO-008` | Opportunity upsert (not duplicate) | Run scan twice, same data | Same opportunity count |
| `IO-009` | Expired opportunity cleanup | Mark listing stale, re-scan | Opportunity status → "expired" |
| `IO-010` | All 29 routes produce valid opportunities | Seed 1 product per route | 29 opportunities with correct route labels |

### 4.5 Opportunity Engine → API

| Test ID | Test Case | Setup | Validation |
|---------|-----------|-------|------------|
| `IA-001` | `GET /opportunities` returns list | 10 opportunities in DB | Response has 10 items |
| `IA-002` | Filter by `min_roi` | Opps with ROI 5%, 15%, 25% | `min_roi=10` → 2 results |
| `IA-003` | Filter by `max_buy_price` | Opps at $50, $150, $500 | `max_buy_price=200` → 2 results |
| `IA-004` | Filter by marketplace | Opps on amazon, ebay, ml_mx | `marketplace=amazon` → 1 result |
| `IA-005` | Sort by score descending | 3 opps, scores 80, 60, 90 | Order: 90, 80, 60 |
| `IA-006` | Pagination (limit/offset) | 25 opps | `limit=10&offset=10` → items 11-20 |
| `IA-007` | `GET /opportunities/stats` | 10 opps in DB | `total_opportunities=10`, averages correct |
| `IA-008` | `POST /opportunities/scan` triggers detection | Products in DB | `new_opportunities >= 0` |
| `IA-009` | `GET /opportunities/{id}` detail | Valid opp ID | Full breakdown returned |
| `IA-010` | `GET /opportunities/{id}` not found | Invalid ID | 404 response |

---

## 5. End-to-End Tests

### 5.1 Full Pipeline Test

```
Fixture HTML → Scraper Parse → Redis Stream → Processor → PostgreSQL → Scan → API Response → Verify
```

| Test ID | Scenario | Description |
|---------|----------|-------------|
| `E2E-001` | **Golden Path: US→MX Arbitrage** | Inject Amazon US listing ($100) + ML MX listing ($200) for same product. Run full pipeline. Verify opportunity appears with correct profit, fees, and route="US→MX". |
| `E2E-002` | **Domestic MX→MX** | Inject Amazon MX listing ($1500 MXN) + ML MX listing ($2500 MXN). Verify domestic route, no import tax. |
| `E2E-003` | **Multi-Country Same Product** | Inject listings on Amazon US, ML MX, ML AR, ML CL for "iPhone 15 Pro". Verify multiple opportunities created with correct routes. |
| `E2E-004` | **No Opportunity (Same Price)** | Inject listings at identical prices on 2 marketplaces. Verify no opportunity created (fees eat profit). |
| `E2E-005` | **Variant Mismatch Rejected** | Inject "iPhone 15 128GB" on Amazon + "iPhone 15 256GB" on ML. Verify no opportunity (variant filter). |
| `E2E-006` | **Stale Data Expiry** | Create opportunity, then age the listings to 72h. Re-scan. Verify opportunity marked "expired". |
| `E2E-007` | **Dashboard Stats Consistency** | Create 5 opportunities. Verify `/stats` matches actual DB aggregates exactly. |
| `E2E-008` | **Scan Idempotency** | Run `/scan` 3 times with no data changes. Verify opportunity count stays the same. |

### 5.2 E2E Test Implementation Pattern

```python
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_golden_path_us_to_mx(
    test_db, redis_client, processor_pipeline
):
    # 1. Seed master product
    product_id = await seed_master_product(test_db, "sony wh-1000xm5")

    # 2. Publish listings to Redis
    buy_listing = RawListing(
        title="Sony WH-1000XM5 Wireless Headphones",
        price=298.00, currency="USD",
        url="https://amazon.com/dp/B09XS7JWHH",
        marketplace_id="amazon_us",
        seller_rating=4.8, reviews_count=15000,
        condition="new", is_free_shipping=True,
    )
    sell_listing = RawListing(
        title="Audifonos Sony WH-1000XM5 Inalambricos",
        price=7499.00, currency="MXN",
        url="https://mercadolibre.com.mx/MLM-123456",
        marketplace_id="mercadolibre_mx",
        seller_rating=4.5, reviews_count=200,
        condition="new", is_free_shipping=False,
    )
    await publish_to_stream(redis_client, [buy_listing, sell_listing])

    # 3. Run processor
    await processor_pipeline.process_pending()

    # 4. Trigger scan
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/api/v1/opportunities/scan")
        assert resp.status_code == 200

    # 5. Verify opportunity
    resp = await client.get("/api/v1/opportunities")
    opps = resp.json()
    assert len(opps) >= 1
    opp = next(o for o in opps if o["route"] == "US→MX")
    assert opp["net_profit"] > 0
    assert opp["buy_marketplace"] == "amazon_us"
    assert opp["sell_marketplace"] == "mercadolibre_mx"
    assert opp["import_tax"] > 0  # Cross-border
    assert opp["opportunity_score"] > 0
```

---

## 6. Data Quality Tests

### 6.1 Listing Data Validation

| Test ID | Quality Rule | Detection Method | Action |
|---------|-------------|-----------------|--------|
| `DQ-001` | **Duplicate listings** | Same URL appears twice in `product_listings` | UNIQUE constraint on URL prevents; test UPSERT behavior |
| `DQ-002` | **Malformed titles** | Title is empty, all symbols, or < 3 chars | Skip at pipeline validation stage |
| `DQ-003` | **Unrealistic low price** | `price < $1.00 USD` | Reject in pipeline (`_MIN_PRICE_USD = 1.0`) |
| `DQ-004` | **Unrealistic high price** | `price > $20,000 USD` | Reject in pipeline (`_MAX_PRICE_USD = 20000.0`) |
| `DQ-005` | **Missing seller rating** | `seller_rating IS NULL` | Allow, but exclude from trust-based filters |
| `DQ-006` | **Rating out of range** | `seller_rating < 0 OR > 5.0` | Clamp or reject |
| `DQ-007` | **Negative review count** | `reviews_count < 0` | Reject |
| `DQ-008` | **Future scraped_at** | `scraped_at > now() + 1h` | Reject |
| `DQ-009` | **Invalid marketplace_id** | Not in known set of 8 marketplaces | Skip listing |
| `DQ-010` | **Currency mismatch** | MXN price on `amazon_us` | Flag, likely parsing error |

### 6.2 Variant Consistency Tests

| Test ID | Scenario | Input | Expected |
|---------|----------|-------|----------|
| `DV-001` | Storage variants | "iPhone 15 128GB" vs "iPhone 15 256GB" | `variants_compatible() → False` |
| `DV-002` | Same storage | "iPhone 15 128GB" vs "iPhone 15 128GB case azul" | Check bundle detection |
| `DV-003` | Capacity mismatch | "Powerbank 5000mAh" vs "Powerbank 20000mAh" | `variants_compatible() → False` |
| `DV-004` | Condition mismatch | `condition="new"` vs `condition="used"` | `variants_compatible() → False` |
| `DV-005` | Bundle vs single | "iPhone 15 + Funda" vs "iPhone 15" | `variants_compatible() → False` |
| `DV-006` | Accessory vs product | "Funda iPhone 15" vs "iPhone 15" | `variants_compatible() → False` |
| `DV-007` | Same product, different lang | "Audifonos Sony XM5" vs "Sony XM5 Headphones" | `variants_compatible() → True` |
| `DV-008` | Model number match | "WH-1000XM4" vs "WH1000XM4" | `variants_compatible() → True` |

### 6.3 Data Quality SQL Assertions

```sql
-- DQ-ASSERT-001: No orphan listings
SELECT COUNT(*) FROM product_listings
WHERE master_product_id NOT IN (SELECT id FROM master_products);
-- Expected: 0

-- DQ-ASSERT-002: No duplicate URLs per marketplace
SELECT url, marketplace_id, COUNT(*)
FROM product_listings
GROUP BY url, marketplace_id
HAVING COUNT(*) > 1;
-- Expected: 0 rows

-- DQ-ASSERT-003: All opportunities reference valid listings
SELECT COUNT(*) FROM opportunities o
WHERE NOT EXISTS (SELECT 1 FROM product_listings WHERE id = o.buy_listing_id)
   OR NOT EXISTS (SELECT 1 FROM product_listings WHERE id = o.sell_listing_id);
-- Expected: 0

-- DQ-ASSERT-004: Price sanity
SELECT COUNT(*) FROM product_listings
WHERE price <= 0 OR price > 100000;
-- Expected: 0

-- DQ-ASSERT-005: Opportunity profit consistency
SELECT COUNT(*) FROM opportunities
WHERE ABS(net_profit - (sell_price - buy_price - fees - shipping_cost)) > 0.02;
-- Expected: 0
```

---

## 7. Opportunity Engine Validation

### 7.1 Profit Calculation Matrix

Test all 8 sell-side marketplaces with consistent buy input ($100 USD from Amazon US):

| Sell Marketplace | Commission | Payment | VAT/IVA | Domestic Ship | Import Tax | Int'l Ship |
|-----------------|------------|---------|---------|---------------|------------|------------|
| `amazon` (MX) | 15% | 0% | 16% | $5 | 16% | $15 |
| `amazon_us` | 15% | 0% | 0% | $5 | 0% | $0 |
| `mercadolibre_mx` | 16% | 3.6% | 16% | $5 | 16% | $15 |
| `mercadolibre_ar` | 13% | 3.6% | 21% | $5 | 50% | $25 |
| `mercadolibre_cl` | 13% | 3.6% | 19% | $5 | 19% | $22 |
| `mercadolibre_co` | 14% | 3.6% | 19% | $5 | 20% | $20 |
| `ebay` | 13.12% | 0% | 0% | $8 | 0% | $0 |
| `aliexpress` | 8% | 0% | 0% | $5 | varies | varies |

### 7.2 Edge Cases

| Test ID | Scenario | Expected |
|---------|----------|----------|
| `OE-001` | Buy price = sell price | `net_profit < 0` (fees make it negative) |
| `OE-002` | Buy price > sell price | `net_profit < 0`, opportunity rejected |
| `OE-003` | Sell price barely above breakeven | Opportunity created but low score |
| `OE-004` | Both sides free shipping | No shipping fees charged |
| `OE-005` | $0.01 profit (below $1 minimum) | Opportunity rejected |
| `OE-006` | ROI = 501% (above 500% max) | Opportunity rejected |
| `OE-007` | Price ratio = 8.1x (above 8x max) | Opportunity rejected |
| `OE-008` | Price ratio = 7.9x (below 8x max) | Opportunity created, soft penalty applied |
| `OE-009` | Seller rating = 1.5 (exactly at minimum) | Included (boundary) |
| `OE-010` | Seller rating = 1.4 | Excluded |
| `OE-011` | Listing age = 47h (within 48h) | Included |
| `OE-012` | Listing age = 49h (beyond 48h) | Excluded |
| `OE-013` | Only 3 price points (below 4 min for IQR) | Outlier detection skipped |
| `OE-014` | Unknown route pair | Uses `DEFAULT_CROSS_BORDER` fees |
| `OE-015` | FX rate = 0 (API failure) | Graceful handling, no division by zero |

### 7.3 Scoring Regression Suite

Maintain a golden dataset of 20 opportunities with pre-calculated expected scores:

```python
GOLDEN_OPPORTUNITIES = [
    {
        "name": "High-profit US→MX headphones",
        "buy_price": 200, "sell_price": 500,
        "route": "US→MX", "competition": 5,
        "expected_score_range": (70, 85),
        "expected_confidence": "high",
    },
    {
        "name": "Marginal domestic MX flip",
        "buy_price": 100, "sell_price": 120,
        "route": "MX→MX", "competition": 25,
        "expected_score_range": (15, 35),
        "expected_confidence": "low",
    },
    # ... 18 more scenarios
]
```

---

## 8. Performance Testing

### 8.1 Load Profiles

| Profile | Listings/sec | Duration | Total Volume | Purpose |
|---------|-------------|----------|-------------|---------|
| **Baseline** | 1,000 | 60s | 60k | Verify basic throughput |
| **Target** | 10,000 | 60s | 600k | Production target validation |
| **Peak** | 50,000 | 30s | 1.5M | Burst capacity test |
| **Sustained** | 15,000 | 300s | 4.5M | Steady-state validation |

### 8.2 Metrics & Thresholds

| Metric | Baseline | Target | Peak | Alert Threshold |
|--------|----------|--------|------|-----------------|
| **Processor throughput** | > 1k/s | > 10k/s | > 30k/s | < 5k/s |
| **Redis stream lag** | < 100 msgs | < 1k msgs | < 10k msgs | > 50k msgs |
| **DB write latency (p95)** | < 10ms | < 50ms | < 200ms | > 500ms |
| **DB write latency (p99)** | < 50ms | < 100ms | < 500ms | > 1s |
| **Embedding batch time** | < 50ms/256 | < 100ms/256 | < 200ms/256 | > 500ms |
| **CPU usage (processor)** | < 30% | < 60% | < 85% | > 90% |
| **Memory usage** | < 500MB | < 1GB | < 2GB | > 3GB |
| **asyncio queue depth** | < 100 | < 500 | < 1024 | = 1024 (full) |
| **COPY flush time** | < 5ms | < 20ms | < 100ms | > 200ms |
| **Scan duration** | < 5s | < 15s | < 30s | > 60s |

### 8.3 Locust Load Test Script

```python
# tests/performance/locustfile.py
from locust import HttpUser, task, between

class OpportunityUser(HttpUser):
    wait_time = between(1, 3)

    @task(10)
    def list_opportunities(self):
        self.client.get("/api/v1/opportunities?limit=20")

    @task(5)
    def get_stats(self):
        self.client.get("/api/v1/opportunities/stats")

    @task(1)
    def trigger_scan(self):
        self.client.post("/api/v1/opportunities/scan")

    @task(3)
    def get_detail(self):
        self.client.get("/api/v1/opportunities/1")

    @task(8)
    def filtered_list(self):
        self.client.get(
            "/api/v1/opportunities?min_roi=10&max_buy_price=500&limit=20"
        )
```

### 8.4 Pipeline Throughput Test (k6)

```javascript
// tests/performance/pipeline_throughput.js
import redis from 'k6/experimental/redis';
import { check } from 'k6';

export const options = {
  scenarios: {
    publish: {
      executor: 'constant-arrival-rate',
      rate: 10000,          // 10k listings/sec
      timeUnit: '1s',
      duration: '60s',
      preAllocatedVUs: 50,
    },
  },
  thresholds: {
    'redis_publish_duration': ['p95<10'],
  },
};

export default function () {
  const listing = generateListing();
  const start = Date.now();
  redis.xadd('raw_listings_queue', '*', listing);
  const duration = Date.now() - start;
  check(duration, { 'publish < 10ms': (d) => d < 10 });
}
```

### 8.5 Database Performance Queries

```sql
-- Monitor active connections during load
SELECT count(*), state FROM pg_stat_activity
WHERE datname = 'compraventa' GROUP BY state;

-- Table bloat during heavy writes
SELECT relname, n_live_tup, n_dead_tup,
       round(n_dead_tup::numeric / GREATEST(n_live_tup, 1) * 100, 1) as dead_pct
FROM pg_stat_user_tables
WHERE schemaname = 'public' ORDER BY n_dead_tup DESC;

-- Index usage during scans
SELECT indexrelname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public' ORDER BY idx_scan DESC;
```

---

## 9. Stress Testing

### 9.1 Scenarios

| Test ID | Scenario | Method | Expected Behavior |
|---------|----------|--------|-------------------|
| `ST-001` | **Scraper spike (5x normal)** | Publish 75k listings in 15s burst | Redis absorbs burst; processor catches up within 60s; no data loss |
| `ST-002` | **Redis backlog (500k pending)** | Pause processor, accumulate 500k messages, resume | Processor drains backlog at >15k/s; backpressure threshold (50k) triggers warning; no OOM |
| `ST-003` | **DB slowdown (100ms latency)** | Add 100ms delay via `pg_sleep` or network shaping | Processor reduces batch throughput; write buffer grows; adaptive batch size adjusts down; no crash |
| `ST-004` | **Embedding model overload** | Send 10k listings with 500+ char titles | GPU/CPU batch time increases; pipeline adapts batch size; no timeout |
| `ST-005` | **Memory pressure** | Limit processor container to 512MB | Graceful degradation; batch sizes reduce; no OOM kill |
| `ST-006` | **Concurrent scans** | 10 simultaneous `POST /scan` requests | Only 1 executes, others wait or return "scan in progress" |
| `ST-007` | **Large product index (500k)** | Load 500k master products into resolver | Cosine similarity still < 5ms per batch; memory < 2GB |

### 9.2 Recovery Metrics

| Metric | Threshold |
|--------|-----------|
| Time to drain 50k backlog | < 10 seconds |
| Time to drain 500k backlog | < 60 seconds |
| Memory growth during burst | < 2x steady-state |
| Error rate during stress | < 0.1% |
| Data loss during stress | 0% |

---

## 10. Fault Tolerance Testing

### 10.1 Infrastructure Failures

| Test ID | Failure | Method | Expected Behavior | Recovery |
|---------|---------|--------|-------------------|----------|
| `FT-001` | **Redis crash** | `docker stop redis_test` | Scraper retries publish with backoff; Processor logs error, enters retry loop | Auto-reconnect when Redis restarts |
| `FT-002` | **Redis restart with data** | `docker restart redis_test` | Pending messages preserved (AOF/RDB); Consumer group resumes from last ACK | No data loss |
| `FT-003` | **PostgreSQL connection drop** | Kill DB connections via `pg_terminate_backend()` | Write buffer retries (3 attempts); Connection pool reconnects | Auto-recovery within 10s |
| `FT-004` | **PostgreSQL crash** | `docker stop db_test` | Processor buffers in-memory (up to 1024); API returns 503; No data corruption | Resume from buffer on DB restart |
| `FT-005` | **Processor crash mid-batch** | `kill -9 processor_pid` | Unacked messages stay in Redis pending list | On restart: re-read pending, re-process |
| `FT-006` | **Processor restart (clean)** | `kill -TERM processor_pid` | Graceful shutdown: flush buffer, ACK processed | Clean restart, no duplicates |
| `FT-007` | **Network partition (DB)** | `iptables` block port 5433 | Processor detects timeout, enters retry | Heals when network restored |
| `FT-008` | **Disk full on DB** | Fill tmpfs | INSERT fails with clear error | Processor pauses writes, alerts |
| `FT-009` | **FX rate API down** | Mock API returns 500 | Use cached rates; log warning | Stale rates with age tracking |
| `FT-010` | **Embedding model corrupted** | Delete model file | Lazy-load re-downloads; pipeline pauses during load | Auto-recovery after download |

### 10.2 Partial Pipeline Failures

| Test ID | Scenario | Expected |
|---------|----------|----------|
| `FT-011` | UPSERT succeeds, COPY fails | Transaction rollback; retry entire batch |
| `FT-012` | Embedding succeeds, resolve fails | Batch skipped; error logged; messages NACKed |
| `FT-013` | 1 of 256 listings has corrupt data | 255 processed; 1 logged and skipped |
| `FT-014` | DB pool exhausted (all connections busy) | New queries wait up to pool timeout; backpressure |

---

## 11. Security Testing

### 11.1 API Security

| Test ID | Vulnerability | Test Method | Expected |
|---------|--------------|-------------|----------|
| `SEC-001` | **SQL injection via filters** | `GET /opportunities?marketplace='; DROP TABLE--` | Parameterized query; no injection |
| `SEC-002` | **Path traversal** | `GET /opportunities/../../etc/passwd` | 404 or 422, no file disclosure |
| `SEC-003` | **Large payload DoS** | `POST /scan` with 10MB body | 413 or rejected; no memory spike |
| `SEC-004` | **Excessive pagination** | `GET /opportunities?limit=1000000` | Server caps limit (e.g., max 100) |
| `SEC-005` | **Negative offset** | `GET /opportunities?offset=-1` | 422 validation error |
| `SEC-006` | **Header injection** | Custom headers with `\r\n` | No response splitting |
| `SEC-007` | **CORS misconfiguration** | Cross-origin request from unknown domain | Rejected or properly configured |
| `SEC-008` | **Verbose error disclosure** | Trigger 500 error | No stack trace or DB info in response |

### 11.2 Rate Limiting

| Test ID | Test | Expected |
|---------|------|----------|
| `SEC-009` | 100 requests/sec to `/opportunities` | Responses served (no rate limit = document risk) |
| `SEC-010` | 10 requests/sec to `/scan` | Rate limited or queued (scan is expensive) |
| `SEC-011` | 1000 concurrent connections | Server handles gracefully (uvicorn worker limit) |

### 11.3 Scraper Fingerprint Risks

| Test ID | Risk | Mitigation Test |
|---------|------|----------------|
| `SEC-012` | Consistent User-Agent | Verify UA rotation across requests |
| `SEC-013` | Request rate too high | Verify rate limit ≤ 0.5 req/sec base |
| `SEC-014` | No proxy rotation | Verify proxy pool usage when configured |
| `SEC-015` | TLS fingerprint | Verify httpx TLS configuration varies |
| `SEC-016` | Referer/Origin headers | Verify marketplace-appropriate headers |

### 11.4 Data Security

| Test ID | Risk | Test |
|---------|------|------|
| `SEC-017` | DB credentials in API response | No `postgres` password in any endpoint response |
| `SEC-018` | Redis credentials exposed | No Redis connection string in logs or API |
| `SEC-019` | `.env` file accessible | Not served by frontend or API |
| `SEC-020` | Telegram bot token exposed | Not in API responses or frontend bundle |

---

## 12. Observability Tests

### 12.1 Logging Verification

| Test ID | Component | Log Event | Expected Content |
|---------|-----------|-----------|-----------------|
| `OB-001` | Processor | Batch processed | `batch_size`, `embed_time`, `resolve_time`, `flush_time` |
| `OB-002` | Processor | Error in listing | `listing_url`, `error_type`, `error_message` |
| `OB-003` | Processor | Throughput report | `msg/s`, `total_processed`, `queue_depth` |
| `OB-004` | API | Scan completed | `new_opportunities`, `duration_sec` |
| `OB-005` | API | Request served | HTTP method, path, status code, duration |
| `OB-006` | Scraper | Batch published | `marketplace`, `count`, `dupes_filtered` |
| `OB-007` | Scraper | Rate limit hit | `marketplace`, `wait_time` |
| `OB-008` | Resolver | Cache stats | `cache_hits`, `cache_misses`, `hit_rate` |

### 12.2 Metric Endpoints

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `processor_messages_total` | Counter | `status={ok,error,skipped}` | Processing volume |
| `processor_batch_duration_seconds` | Histogram | `stage={embed,resolve,flush}` | Stage latency |
| `processor_queue_depth` | Gauge | — | Backpressure indicator |
| `redis_stream_lag` | Gauge | — | Consumer lag |
| `opportunities_total` | Gauge | `status={active,expired}` | Business metric |
| `scan_duration_seconds` | Histogram | — | Scan performance |
| `api_request_duration_seconds` | Histogram | `method, path, status` | API latency |
| `db_pool_active_connections` | Gauge | `pool={read,write,copy}` | Connection health |

### 12.3 Alert Rules to Verify

| Alert | Condition | Severity |
|-------|-----------|----------|
| `ProcessorDown` | No messages processed for 5 min | Critical |
| `HighRedisLag` | Stream lag > 50k messages for 5 min | Warning |
| `ScanFailure` | Scan returns error 3x consecutive | Critical |
| `DBConnectionExhausted` | Active connections > 90% pool max | Warning |
| `HighErrorRate` | Processing error rate > 1% over 5 min | Warning |
| `NoNewOpportunities` | 0 new opportunities in 24h | Info |

---

## 13. Automation Strategy

### 13.1 Tools

| Tool | Purpose | Where Used |
|------|---------|-----------|
| **pytest** | Unit + integration + E2E tests | All Python components |
| **pytest-asyncio** | Async test support | Processor, API, resolver |
| **pytest-mock** | Mocking/patching | DB, Redis, external APIs |
| **pytest-cov** | Coverage reporting | All Python |
| **Playwright** | Browser-based scraper E2E | Scraper parsing tests |
| **httpx / AsyncClient** | API integration tests | FastAPI endpoints |
| **Locust** | HTTP load testing | API endpoints |
| **k6** | Pipeline throughput testing | Redis publish throughput |
| **Docker Compose** | Test environment orchestration | CI and local |
| **Jest / Karma** | Frontend unit tests | Angular components |
| **Cypress / Playwright** | Frontend E2E | Dashboard flows |

### 13.2 CI/CD Pipeline

```yaml
# .github/workflows/test.yml
name: CompraVenta Test Suite

on:
  push:
    branches: [main, v4]
  pull_request:
    branches: [main]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: pytest tests/ -m "not load and not e2e" --cov=. --cov-report=xml -x
      - uses: codecov/codecov-action@v4

  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_DB: compraventa_test, POSTGRES_PASSWORD: postgres }
        ports: ["5433:5432"]
        options: --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 5
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: --health-cmd "redis-cli ping" --health-interval 5s --health-timeout 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: |
          for f in migrations/*.sql; do
            PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d compraventa_test -f "$f"
          done
      - run: pytest tests/ -m "integration" -x

  e2e-tests:
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests]
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.test.yml up -d
      - run: pytest tests/ -m "e2e" -x --timeout=120
      - run: docker compose -f docker-compose.test.yml down

  performance-tests:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: [e2e-tests]
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.test.yml up -d
      - run: pip install locust
      - run: locust -f tests/performance/locustfile.py --headless -u 50 -r 10 -t 60s --host http://localhost:8000
      - run: docker compose -f docker-compose.test.yml down
```

### 13.3 Test Markers

```ini
# pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
markers =
    load: Load tests (require Redis running)
    e2e: End-to-end pipeline tests
    integration: Integration tests (require DB + Redis)
    performance: Performance benchmarks
    security: Security validation tests
    slow: Tests that take > 10s
```

### 13.4 Coverage Targets

| Module | Current (est.) | Target |
|--------|---------------|--------|
| `processor/normalizer.py` | ~85% | 95% |
| `processor/resolver.py` | ~60% | 85% |
| `processor/embeddings.py` | ~40% | 75% |
| `processor/pipeline.py` | ~30% | 70% |
| `api/opportunity.py` | ~70% | 90% |
| `api/scoring.py` | ~50% | 85% |
| `api/routes_config.py` | ~40% | 90% |
| `api/routes/opportunities.py` | ~30% | 80% |
| `scraper/price_parser.py` | ~60% | 90% |
| `scraper/dedup.py` | ~50% | 85% |
| **Overall** | **~45%** | **80%** |

---

## 14. Release Checklist

### Pre-Release Validation

```
INFRASTRUCTURE
[ ] PostgreSQL container healthy, migrations applied
[ ] Redis container healthy, stream accessible
[ ] All Docker services start clean from docker-compose
[ ] .env.example matches all required variables

UNIT TESTS
[ ] All unit tests pass (pytest -m "not load and not e2e and not integration")
[ ] Coverage >= 80% overall
[ ] No new test failures vs. previous release

INTEGRATION TESTS
[ ] Scraper → Redis publish verified
[ ] Redis → Processor consume verified
[ ] Processor → PostgreSQL write verified
[ ] Opportunity detection produces valid results
[ ] API endpoints return correct responses
[ ] All 29 trade routes produce valid profit calculations

DATA QUALITY
[ ] DQ SQL assertions pass on staging data
[ ] No orphan listings in database
[ ] No duplicate URLs per marketplace
[ ] Price sanity checks pass
[ ] Opportunity profit consistency verified

PERFORMANCE
[ ] Processor throughput > 15k/s sustained
[ ] API p95 latency < 200ms under load
[ ] Scan completes in < 30s with 50k products
[ ] Redis stream lag < 1k at steady state
[ ] No memory leaks over 30-min run

SECURITY
[ ] No SQL injection via API filters
[ ] No credentials in API responses
[ ] No stack traces in error responses
[ ] .env not accessible from frontend

OPPORTUNITY ENGINE
[ ] Golden dataset (20 scenarios) all pass within expected score ranges
[ ] Fee calculations match reference spreadsheet for all 8 marketplaces
[ ] Cross-border import taxes correct for all 19 country pairs
[ ] VAT/IVA rates correct per country (MX:16%, AR:21%, CL:19%, CO:19%, US:0%)
[ ] Edge cases: zero profit, max ROI, boundary ratings all handled

FRONTEND
[ ] Dashboard loads and displays opportunities
[ ] Filters work (ROI, price, marketplace)
[ ] Sorting works (score, ROI, profit, recency)
[ ] Detail view shows complete cost breakdown
[ ] Auto-refresh polling active (30s interval)
[ ] Route color coding matches specification

FAULT TOLERANCE
[ ] Processor recovers from Redis restart
[ ] Processor recovers from DB connection drop
[ ] API returns 503 (not 500) when DB is down
[ ] No data loss during infrastructure restart

OBSERVABILITY
[ ] Processor logs throughput metrics every 10s
[ ] API logs request duration
[ ] Error logs include actionable context (URL, marketplace, error type)
[ ] No sensitive data in logs (passwords, tokens)

DEPLOYMENT
[ ] Docker images build successfully
[ ] Migrations are idempotent (safe to re-run)
[ ] Rollback procedure documented and tested
[ ] Environment variables documented in .env.example
```

### Sign-Off

| Role | Name | Date | Status |
|------|------|------|--------|
| QA Lead | | | |
| Backend Engineer | | | |
| Data Engineer | | | |
| SRE | | | |

---

*Document generated for CompraVenta v4. Update this plan as the system evolves.*
