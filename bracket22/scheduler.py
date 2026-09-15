"""Run as a separate long-lived process, not inside web workers."""

import logging
import time
from datetime import UTC, datetime

from .config import configured_agents, configured_database, configured_provider
from .service import Service


def run_once(service, now):
    if now.hour < 7:
        return None
    status = service.operations()
    success = status["last_success"]
    if success and success["day"] == now.date().isoformat():
        return None
    last = status["last_run"]
    if last and last["day"] == now.date().isoformat():
        elapsed = (now - datetime.fromisoformat(last["completed_at"])).total_seconds()
        if elapsed < 3600:
            return None
    return service.daily()


def main():
    logging.basicConfig(level=logging.INFO)
    service = Service(configured_database(), provider=configured_provider(), houston=configured_agents())
    logger = logging.getLogger(__name__)
    while True:
        try:
            result = run_once(service, datetime.now(UTC))
            if result is not None:
                logger.info("Daily analysis: %s reports, errors=%s", len(result["reports"]), result["errors"])
        except Exception:
            logger.exception("Daily run failed; will retry")
        time.sleep(60)


if __name__ == "__main__":
    main()
