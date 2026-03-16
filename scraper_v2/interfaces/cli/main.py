"""CLI Entry Point — bootstraps and runs the scraper_v2 system.

Usage:
    python -m scraper_v2.interfaces.cli.main
"""

from __future__ import annotations

import asyncio
import logging
import sys

from scraper_v2.factory import build_orchestrator
from scraper_v2.config import load_config


def main() -> None:
    config = load_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    logger = logging.getLogger("scraper_v2")
    logger.info("Starting scraper_v2...")
    logger.info("Workers: %d | RPS: %.1f-%.1f | Proxies: %d",
                config.worker_count, config.min_rps, config.max_rps, len(config.proxy_urls))

    orchestrator = build_orchestrator(config)

    try:
        asyncio.run(orchestrator.run_forever())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
