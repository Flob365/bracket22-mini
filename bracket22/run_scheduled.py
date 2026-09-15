"""One bounded scheduler invocation for hosted cron jobs."""
from datetime import UTC, datetime

from .config import configured_agents, configured_database, configured_provider
from .scheduler import run_once
from .service import Service


def main():
    service = Service(configured_database(), provider=configured_provider(), houston=configured_agents())
    try:
        result = run_once(service, datetime.now(UTC))
        if result and result['errors']:
            raise SystemExit('Daily run incomplete; inspect operations in the desk')
    finally:
        service.store.engine.dispose()


if __name__ == '__main__':
    main()
