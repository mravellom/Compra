"""Monitor processor progress via DB listing count and Redis stream."""
import time
import sys
import os
import redis
import subprocess

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
GROUP_NAME = "processor_group"
DB_CMD = [
    "docker", "exec", "compraventa_db",
    "psql", "-U", "postgres", "-d", "compraventa", "-t", "-c",
]


def get_redis_stats():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        info = r.xinfo_stream(STREAM_KEY)
        groups = r.xinfo_groups(STREAM_KEY)
        stream_len = info["length"]
        pending = 0
        last_delivered = "0-0"
        for g in groups:
            if g["name"] == GROUP_NAME:
                pending = g["pending"]
                last_delivered = g["last-delivered-id"]
                break
        return stream_len, pending, last_delivered
    finally:
        r.close()


def get_db_count():
    try:
        r = subprocess.run(
            DB_CMD + ["SELECT count(*) FROM product_listings;"],
            capture_output=True, text=True, timeout=5,
        )
        return int(r.stdout.strip())
    except Exception:
        return -1


def get_multi_mp():
    try:
        r = subprocess.run(
            DB_CMD + [
                "SELECT count(*) FROM ("
                "SELECT master_product_id FROM product_listings "
                "WHERE scraped_at > NOW() - INTERVAL '24 hours' "
                "GROUP BY master_product_id "
                "HAVING count(DISTINCT marketplace_id) >= 2) sub;"
            ],
            capture_output=True, text=True, timeout=5,
        )
        return int(r.stdout.strip())
    except Exception:
        return -1


def main():
    stream_len, _, last_id = get_redis_stats()
    initial_db = get_db_count()
    start = time.time()

    print(f"\n  Processor monitor")
    print(f"  Stream: {stream_len:,} msgs | DB listings: {initial_db:,}\n")

    while True:
        try:
            stream_len, pending, last_id = get_redis_stats()
            db_count = get_db_count()
            multi_mp = get_multi_mp()
            new_listings = db_count - initial_db
            elapsed = time.time() - start
            rate = new_listings / elapsed if elapsed > 0 else 0

            w = 50
            # Progress = how much of the stream has been delivered
            # last_id format: "timestamp-seq"
            try:
                last_ts = int(last_id.split("-")[0])
                first_entry_ts = 1772845845181  # first entry from earlier
                total_ts_range = last_ts - first_entry_ts
                progress_pct = min(99.9, (total_ts_range / max(1, stream_len)) * 100) if stream_len > 0 else 0
            except Exception:
                progress_pct = 0

            filled = int(w * min(100, max(0, new_listings / max(1, pending + new_listings) * 100)) / 100)
            bar = "█" * filled + "░" * (w - filled)

            sys.stdout.write(
                f"\r  DB: {db_count:,} (+{new_listings:,}) | "
                f"{rate:.1f}/s | "
                f"pending: {pending:,} | "
                f"multi-mp: {multi_mp} | "
                f"stream: {stream_len:,}  "
            )
            sys.stdout.flush()

            if pending == 0 and new_listings > 0:
                print(f"\n\n  ✓ Al dia!")
                break

            time.sleep(5)
        except KeyboardInterrupt:
            print("\n")
            break
        except Exception as e:
            sys.stdout.write(f"\r  Error: {e}          ")
            time.sleep(5)


if __name__ == "__main__":
    main()
