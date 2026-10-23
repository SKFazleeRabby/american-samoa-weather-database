"""Initial backfill, then a timezone-aware daily 03:00 collection in Docker."""
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from as_db.web.config import settings
from .runner import run
from .logging_config import configure_logging

HEALTH = Path('/tmp/collection-health.json')
logger = logging.getLogger(__name__)


def collect(mode):
    HEALTH.write_text(json.dumps({'last_attempt': datetime.now(timezone.utc).isoformat(),
                                 'mode': mode, 'status': 'running', 'errors': []}), encoding='utf-8')
    try:
        result = run(mode)
    except Exception as error:
        result = {'errors': [f'Collection could not run ({type(error).__name__})']}
        logger.error('%s', result['errors'][0])
    temporary = HEALTH.with_suffix('.tmp')
    temporary.write_text(json.dumps({'last_attempt': datetime.now(timezone.utc).isoformat(), 'mode': mode, **result}), encoding='utf-8')
    temporary.replace(HEALTH)
    logger.info('Collection finished: %s', result)
    return result


def main():
    configure_logging()
    zone = settings().get('COLLECTION_TIMEZONE', 'Pacific/Pago_Pago')
    scheduler = BlockingScheduler(timezone=zone, job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 21600})
    scheduler.add_job(collect, CronTrigger(hour=3, minute=0, timezone=zone), args=['daily'], id='daily')
    scheduler.add_job(collect, 'date', run_date=datetime.now(timezone.utc) + timedelta(seconds=1), args=['backfill'], id='startup')
    logger.info('Collection worker: backfill after historical CSVs and daily 03:00 in %s', zone)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        if scheduler.running:
            scheduler.shutdown(wait=False)


if __name__ == '__main__':
    main()
