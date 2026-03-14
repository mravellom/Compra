#!/usr/bin/env python3
"""
Live progress bar for the CompraVenta scraper.
Reads scraper log output and shows real-time progress.
"""
import glob
import os
import re
import sys
import time

# ── Categories (same order as scraper) ─────────────────────
CATEGORIES = [
    "audifonos", "parlantes", "celulares", "accesorios-celulares", "smartwatch",
    "tablets", "perifericos", "consolas", "accesorios-gaming", "camaras-digitales",
    "herramientas-electricas", "electrodomesticos", "relojes", "componentes-pc",
    "cables-adaptadores", "baterias-cargadores", "notebooks", "almacenamiento",
    "networking", "drones", "hogar-inteligente", "robots-aspiradora", "monitores",
    "proyectores", "seguridad-electronica", "accesorios-camaras", "memorias-usb",
    "audio-profesional", "impresoras", "juegos-fisicos", "instrumentos-medicion",
    "climatizacion", "accesorios-vehiculos", "electronica-vehicular", "ups-reguladores",
    "audio-vehicular", "fitness-tracker",
]

CRAWLERS = [
    "mercadolibre_ar", "mercadolibre_mx", "mercadolibre_cl",
    "mercadolibre_co", "amazon", "amazon_us",
]

TOTAL_TASKS = len(CATEGORIES) * len(CRAWLERS)  # 37 × 6 = 222

# ── Find the log file ─────────────────────────────────────
def find_log_file():
    pattern = "/tmp/claude-1000/-home-fabian-workSpace-CompraVenta/*/tasks/*.output"
    files = glob.glob(pattern)
    # Pick the scraper log (the one with category crawling messages)
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        with open(f, "r", errors="ignore") as fh:
            head = fh.read(2000)
            if "Scraper mode:" in head or "Category crawlers:" in head:
                return f
    # Fallback: newest file
    return sorted(files, key=os.path.getmtime, reverse=True)[0] if files else None


def parse_progress(log_file):
    """Parse log and return progress stats."""
    completed = set()  # (category, marketplace) pairs done
    current_category = None
    current_marketplace = None
    total_scraped = 0
    total_published = 0
    total_dupes = 0
    started_at = None
    last_line_time = None

    # Pattern: [marketplace] 'category': N scraped, N unique, N dupes, N published
    result_re = re.compile(
        r"\[(\w+)\] '([\w-]+)': (\d+) scraped, (\d+) unique, (\d+) dupes, (\d+) published"
    )
    # Pattern: Crawling 'category' on marketplace
    crawling_re = re.compile(r"Crawling '([\w-]+)' on (\w+)")
    # Timestamp
    time_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    # Cycle complete
    cycle_re = re.compile(r"Category cycle complete: (\d+) listings")

    with open(log_file, "r", errors="ignore") as f:
        for line in f:
            # Track timestamps
            tm = time_re.match(line)
            if tm:
                last_line_time = tm.group(1)
                if started_at is None:
                    started_at = last_line_time

            # Completed task
            m = result_re.search(line)
            if m:
                mkt, cat, scraped, unique, dupes, published = (
                    m.group(1), m.group(2),
                    int(m.group(3)), int(m.group(4)),
                    int(m.group(5)), int(m.group(6)),
                )
                completed.add((cat, mkt))
                total_scraped += scraped
                total_published += published
                total_dupes += dupes

            # Currently crawling
            m = crawling_re.search(line)
            if m:
                current_category = m.group(1)
                current_marketplace = m.group(2)

            # Cycle done
            m = cycle_re.search(line)
            if m:
                return {
                    "done": True,
                    "completed": len(completed),
                    "total": TOTAL_TASKS,
                    "total_scraped": total_scraped,
                    "total_published": int(m.group(1)),
                    "total_dupes": total_dupes,
                    "started_at": started_at,
                    "last_time": last_line_time,
                    "current": None,
                }

    return {
        "done": False,
        "completed": len(completed),
        "total": TOTAL_TASKS,
        "total_scraped": total_scraped,
        "total_published": total_published,
        "total_dupes": total_dupes,
        "started_at": started_at,
        "last_time": last_line_time,
        "current": f"{current_category} @ {current_marketplace}" if current_category else "starting...",
    }


def render_bar(progress, width=40):
    """Render a progress bar string."""
    pct = progress["completed"] / max(progress["total"], 1)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return bar, pct


def estimate_eta(progress):
    """Estimate remaining time."""
    if not progress["started_at"] or not progress["last_time"] or progress["completed"] == 0:
        return "calculating..."

    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        start = datetime.strptime(progress["started_at"], fmt)
        now = datetime.strptime(progress["last_time"], fmt)
        elapsed = (now - start).total_seconds()
        if elapsed <= 0 or progress["completed"] == 0:
            return "calculating..."

        rate = progress["completed"] / elapsed  # tasks per second
        remaining = progress["total"] - progress["completed"]
        eta_secs = remaining / rate

        if eta_secs < 60:
            return f"{int(eta_secs)}s"
        elif eta_secs < 3600:
            return f"{int(eta_secs // 60)}m {int(eta_secs % 60)}s"
        else:
            return f"{int(eta_secs // 3600)}h {int((eta_secs % 3600) // 60)}m"
    except Exception:
        return "unknown"


def category_index(cat):
    try:
        return CATEGORIES.index(cat)
    except ValueError:
        return -1


def main():
    log_file = find_log_file()
    if not log_file:
        print("No scraper log file found!")
        sys.exit(1)

    print(f"\033[1mCompraVenta Scraper Progress\033[0m")
    print(f"Log: {os.path.basename(log_file)}")
    print()

    try:
        while True:
            progress = parse_progress(log_file)
            bar, pct = render_bar(progress)

            # Current category position
            current = progress["current"] or ""
            cat_name = current.split(" @ ")[0] if " @ " in current else current
            cat_idx = category_index(cat_name)
            cat_progress = f"cat {cat_idx + 1}/{len(CATEGORIES)}" if cat_idx >= 0 else ""

            eta = estimate_eta(progress)

            # Build output
            lines = []
            lines.append(f"\033[2K  {bar}  {pct:6.1%}  [{progress['completed']}/{progress['total']} tasks]")
            lines.append(f"\033[2K  Now: \033[33m{current}\033[0m  {cat_progress}")
            lines.append(f"\033[2K  Scraped: \033[36m{progress['total_scraped']:,}\033[0m  Published: \033[32m{progress['total_published']:,}\033[0m  Dupes: \033[90m{progress['total_dupes']:,}\033[0m")
            lines.append(f"\033[2K  ETA: \033[1m{eta}\033[0m")

            if progress["done"]:
                lines[0] = f"\033[2K  {bar}  \033[32m100.0%  DONE!\033[0m"
                lines[1] = f"\033[2K  \033[32mCycle complete!\033[0m"

            # Move cursor up and rewrite
            sys.stdout.write(f"\033[4A" if hasattr(main, '_drawn') else "")
            main._drawn = True
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()

            if progress["done"]:
                print()
                break

            time.sleep(3)
    except KeyboardInterrupt:
        print("\n\nStopped.")


if __name__ == "__main__":
    main()
