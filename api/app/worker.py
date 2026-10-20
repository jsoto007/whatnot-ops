"""Background worker: `python -m app.worker`. Polls the jobs table; stops on SIGTERM."""

import logging
import os
import signal
import time

from . import create_app
from .services import jobs

log = logging.getLogger("worker")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    poll = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    last_sweep = 0.0
    with app.app_context():
        log.info("worker started")
        while not stop:
            if time.monotonic() - last_sweep > 300:
                jobs.requeue_stale()
                last_sweep = time.monotonic()
            job = jobs.run_once()
            if job is None:
                time.sleep(poll)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
