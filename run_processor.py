"""
Processor runner with progress monitoring.
Runs either the high-throughput pipeline (default) or legacy consumer.
"""
import asyncio
import subprocess
import sys
import time
import os

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "raw_listings_queue"
GROUP_NAME = "processor_group"
PROCESSOR_MODE = os.getenv("PROCESSOR_MODE", "pipeline")


def get_pending():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        info = r.xinfo_stream(STREAM_KEY)
        groups = r.xinfo_groups(STREAM_KEY)
        stream_len = info["length"]
        pending = 0
        last_delivered = "0"
        for g in groups:
            if g["name"] == GROUP_NAME:
                pending = g["pending"]
                last_delivered = g["last-delivered-id"]
                break
        return stream_len, pending, last_delivered
    finally:
        r.close()


def progress_bar(current, total, width=40, extra=""):
    if total == 0:
        pct = 100
    else:
        pct = min(100, (current / total) * 100)
    filled = int(width * pct / 100)
    bar = "█" * filled + "░" * (width - filled)
    sys.stdout.write(f"\r  [{bar}] {pct:5.1f}% | {current}/{total} procesados {extra}")
    sys.stdout.flush()


def main():
    stream_len, initial_pending, _ = get_pending()

    total_initial = initial_pending

    # Select processor module
    if PROCESSOR_MODE == "legacy":
        module = "processor.consumer"
        mode_label = "legacy (single-threaded)"
    else:
        module = "processor.pipeline"
        mode_label = "pipeline (high-throughput)"

    print(f"  Processor arrancando... [{mode_label}]")
    print(f"  Stream: {stream_len} mensajes | Pendientes: {initial_pending}")
    print()

    # Start processor as subprocess
    env = os.environ.copy()
    if PROCESSOR_MODE != "legacy":
        env.setdefault("BATCH_SIZE", "64")
        env.setdefault("PREFETCH_SIZE", "512")
        env.setdefault("NUM_WORKERS", "16")
    else:
        env.setdefault("BATCH_SIZE", "50")

    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    processed = 0
    start_time = time.time()

    try:
        while proc.poll() is None:
            time.sleep(5)
            try:
                stream_len, pending, _ = get_pending()
                processed = max(0, total_initial - pending)

                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (pending / rate / 60) if rate > 0 else 0

                extra = f"| {rate:.1f} msg/s | ~{remaining:.0f}min restantes | pendientes: {pending}"
                progress_bar(processed, total_initial, extra=extra)

                if pending < 10:
                    progress_bar(total_initial, total_initial, extra="| ✓ Completado!")
                    print()
                    print(f"\n  Processor al dia. Escuchando nuevos mensajes...")
                    proc.wait()
                    break

            except Exception as e:
                sys.stdout.write(f"\r  [error checking progress: {e}]")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n\n  Deteniendo processor...")
        proc.terminate()
        proc.wait(timeout=10)
        print("  Processor detenido.")
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
