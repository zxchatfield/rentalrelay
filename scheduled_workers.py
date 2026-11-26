#!/usr/bin/env python3
"""Deterministic scheduled jobs; independent from Muse usage or phone being open.

Run with RR_JOB=delivery or RR_JOB=page. Server secrets are inherited from
protected process environment. Tests must use injected providers; never store
applicant data or credentials in logs.
"""
import datetime as dt
import os
import sys
import time
from pathlib import Path
from customer_notifications import run_once as notify_applicant

import database as db_adapter

ROOT=Path(__file__).resolve().parent


def run_job(job):
    from manager_api_delivery import run_once as deliver
    from facebook_page_scheduler import run_once as publish
    database=Path(os.getenv('DATABASE_PATH',ROOT/'rentalrelay.sqlite3'))
    if job=='delivery':
        import json
        mapping=json.loads(os.getenv('MANAGER_API_DESTINATIONS_JSON','{}'))
        outcome=deliver(database,mapping,os.getenv('AUTO_MANAGER_DELIVERY_ENABLED','false').lower() in ('true','1'))
        return {'job':'delivery','delivered':len(outcome['processed']),
                'failed_or_not_configured':outcome.get('failed_or_not_configured',0),
                'skipped':bool(outcome.get('skipped'))}
    if job=='applicant_email':
        outcome=notify_applicant(database,Path(os.getenv('OPERATIONS_STATE_DB',ROOT/'operations_state.sqlite3')),
            os.getenv('PUBLIC_BASE_URL',''),
            os.getenv('AUTO_CUSTOMER_NOTIFICATIONS_ENABLED','false').lower() in ('true','1'))
        return {'job':'applicant_email', **outcome}
    if job=='purge':
        if not db_adapter.is_remote() and not database.is_file():
            return {'job':'purge','removed':0,'skipped':True}
        with db_adapter.connect(database,timeout=20) as conn:
            result=conn.execute('''DELETE FROM applications WHERE retention_until < ?
                AND status IN ('failed','delivered','refunded','pending')''',
                (dt.datetime.now(dt.timezone.utc).isoformat(),))
            return {'job':'purge','removed':result.rowcount,'skipped':False}
    if job=='page':
        output=publish(database,Path(os.getenv('OPERATIONS_STATE_DB',ROOT/'operations_state.sqlite3')),
                       os.getenv('PUBLIC_BASE_URL',''),os.getenv('FB_PAGE_ID',''),
                       os.getenv('FB_PAGE_ACCESS_TOKEN',''),
                       os.getenv('SOCIAL_POSTS_ENABLED','false').lower() in ('true','1'))
        return {'job':'page','published':len(output['published']),
                'skipped':bool(output.get('skipped'))}
    raise ValueError('RR_JOB must be delivery, page, purge or applicant_email')


def main():
    job=os.getenv('RR_JOB','').strip().lower()
    if job not in ('delivery','page','purge','applicant_email'): raise SystemExit('Invalid RR_JOB setting')
    interval=int(os.getenv('RR_JOB_INTERVAL_SECONDS', '180' if job=='delivery' else '7200' if job=='page' else '86400' if job=='purge' else '120'))
    if interval<60:raise SystemExit('RR_JOB_INTERVAL_SECONDS must be >=60')
    print(f'RentalRelay {job} job scheduler starting, interval {interval}s',flush=True)
    while True:
        try:
            result=run_job(job)
            # Never print names, addresses, application identifiers, API URLs or secrets.
            print(f'{dt.datetime.now(dt.timezone.utc).isoformat()} {result}',flush=True)
        except Exception as exc:
            print(f'{job} job failed ({type(exc).__name__}); next run scheduled',file=sys.stderr,flush=True)
        time.sleep(interval)


if __name__=='__main__':main()
