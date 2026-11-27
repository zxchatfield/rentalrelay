#!/usr/bin/env python3
"""RentalRelay 24/7 OPERATIONS NOTIFIER.

Only *reads* production application DB. Writes separate local outbox/dedup state.
A verified Stripe webhook (in app.py) must have already marked payment paid.
No applicants' names, emails, phone numbers or addresses leave this worker.

Use with systemd or a process manager for persistent execution.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import database

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/Detroit")
PRICE_CENTS = 2499


@dataclass
class Config:
    application_db: Path
    state_db: Path
    token: str
    chat_id: str
    daily_hour: int = 8
    poll_seconds: int = 60
    delivery_escalate_minutes: int = 120
    daily_digest: bool = True

    @classmethod
    def from_environment(cls):
        return cls(
            application_db=Path(os.getenv('DATABASE_PATH', ROOT / 'rentalrelay.sqlite3')),
            state_db=Path(os.getenv('OPERATIONS_STATE_DB', ROOT / 'operations_state.sqlite3')),
            token=os.getenv('TELEGRAM_BOT_TOKEN','').strip(),
            chat_id=os.getenv('TELEGRAM_CHAT_ID','').strip(),
            daily_hour=int(os.getenv('DAILY_REPORT_HOUR_ET','8')),
            poll_seconds=int(os.getenv('AUTOPILOT_POLL_SECONDS','60')),
            delivery_escalate_minutes=int(os.getenv('DELIVERY_ESCALATE_MINUTES','120')),
            daily_digest=os.getenv('DAILY_DIGEST_ENABLED','true').lower() in ('true','1'),
        )


def connect_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = database.connect(str(path), timeout=20)
    # busy_timeout is a local-file lock wait; Turso is a remote database
    # and rejects it, so only set it for a local sqlite3 file.
    if not database.is_remote():
        con.execute('PRAGMA busy_timeout=20000')
    con.execute('CREATE TABLE IF NOT EXISTS message_log(event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL)')
    con.commit()
    return con


def read_snapshot(path):
    if not database.is_remote() and not path.is_file():
        raise FileNotFoundError('RentalRelay DB missing. Start the web app first.')
    # Read only: notifications must never mutate payment/application records.
    uri = f'file:{urllib.parse.quote(str(path))}?mode=ro'
    con = database.connect(uri, uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        apps = [dict(row) for row in con.execute('''SELECT id,property_id,status,paid_at,delivered_at,refunded_at
                FROM applications WHERE paid_at IS NOT NULL ORDER BY paid_at ASC''')]
        props = [dict(row) for row in con.execute('''SELECT id,address,city,is_demo,public_active,verified_at,
                advert_permission,intake_permission,photo_rights,secure_delivery_verified FROM properties''')]
        return apps, props
    finally:
        con.close()


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(s):
    try:
        x=dt.datetime.fromisoformat(s)
        return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)
    except (ValueError,TypeError):
        return None


def money(cents):
    return f'${cents / 100:,.2f}'


def prepare_messages(apps, props, now=None, daily_hour=8, escalate_minutes=120, test_mode=False, daily_digest=True):
    """Pure function for auditability and tests. Returns [(id, message), ...].
    test_mode=True labels every message as a TEST payment so synthetic
    checkouts are never presented as real revenue."""
    now = now or now_utc()
    local = now.astimezone(ET)
    prop_map = {p['id']: p for p in props}
    test_digest_prefix = '🧪 TEST — ' if test_mode else ''
    def revenue_for(day=None, month=None):
        total = 0
        for x in apps:
            if x['status'] == 'refunded':
                continue
            # Keep test payments out of real revenue totals (and vice versa).
            # A Stripe Checkout Session id positively identifies the kind.
            sid = x.get('stripe_session_id') or ''
            if test_mode and sid.startswith('cs_live_'):
                continue
            if not test_mode and sid.startswith('cs_test_'):
                continue
            t = parse_time(x['paid_at'])
            if not t:
                continue
            lt = t.astimezone(ET)
            if day is not None and lt.date() != day:
                continue
            if month is not None and (lt.year, lt.month) != month:
                continue
            total += PRICE_CENTS
        return total
    msgs=[]
    for a in apps:
        paid_at=parse_time(a['paid_at'])
        if not paid_at: continue
        # Stripe webhook is the only path to paid_at, never form completion or redirect.
        appid=a['id']
        prop = prop_map.get(a.get('property_id') or '')
        if prop:
            addr = prop['address']
            city = prop['city']
            prop_label = addr if city and city.lower() in addr.lower() else f"{addr}, {city}"
        else:
            prop_label = 'Property not recorded'
        paid_day = paid_at.astimezone(ET).date()
        day_total = revenue_for(day=paid_day)
        month_total = revenue_for(month=(paid_day.year, paid_day.month))
        # Routine payment notification: short and human. Application IDs,
        # technical statuses and disclaimers stay in the internal records.
        test_label = '🧪 TEST PAYMENT\n' if test_mode else ''
        msgs.append((f'paid:{appid}', f'{test_label}💰 New payment: $24.99\n📍 {prop_label}\n\nToday: {money(day_total)}\nThis month: {money(month_total)}'))
        if a['status']=='delivered' and a['delivered_at']:
            msgs.append((f'delivered:{appid}', f'✅ Application marked delivered\nApplication ID: {appid}\nCheck the manager receipt/audit before describing it as confirmed.'))
        if a['status']=='refunded' and a['refunded_at']:
            msgs.append((f'refunded:{appid}', f'↩️ RentalRelay refund recorded\nApplication ID: {appid}\nVerify final payment status in Stripe.'))
        if a['status'] in ('paid','delivery_pending') and (now-paid_at).total_seconds() >= escalate_minutes*60:
            msgs.append((f'undelivered:{appid}:{local.date().isoformat()}', f'⚠️ Paid application awaiting manager delivery for over {escalate_minutes} minutes\nApplication ID: {appid}\nDo not claim it was delivered.'))
    if daily_digest and local.hour >= daily_hour:
        # Report the fully completed *previous* local day. An 8 AM report for
        # the current day would otherwise omit most sales and mislead the owner.
        report_date = local.date() - dt.timedelta(days=1)
        day=report_date.isoformat()
        daily_apps=[a for a in apps if (x:=parse_time(a['paid_at'])) and x.astimezone(ET).date()==report_date]
        refunded_ids={a['id'] for a in daily_apps if a['status']=='refunded'}
        gross=len(daily_apps)*PRICE_CENTS
        approx_processing=int(round(len(daily_apps)*(PRICE_CENTS*.029+30)))
        live_props=0; stale_props=0
        for p in props:
            if p['is_demo'] or not p['public_active']: continue
            verified=parse_time(p['verified_at'])
            ready=all(p[k] for k in ('advert_permission','intake_permission','photo_rights','secure_delivery_verified'))
            if not verified or not ready or (now-verified).total_seconds()>=48*3600: stale_props+=1
            else: live_props+=1
        day_delivered=sum(1 for a in apps if a['delivered_at'] and (t:=parse_time(a['delivered_at'])) and t.astimezone(ET).date().isoformat()==day)
        waiting=sum(1 for a in apps if a['status'] in ('paid','delivery_pending'))
        message=(f'{test_digest_prefix}☀️ RentalRelay morning report — completed day {day} ET\n'
                 f'Verified paid applications for completed day: {len(daily_apps)}\n'
                 f'Gross charges: {money(gross)}\n'
                 f'Estimated Stripe processing: {money(approx_processing)}\n'
                 f'After standard processing, before refunds/other costs: {money(gross-approx_processing)}\n'
                 f'Currently refunded from that day’s payments: {len(refunded_ids)}\n'
                 f'Applications delivered on that day: {day_delivered}\n'
                 f'Currently paid/awaiting delivery: {waiting}\n'
                 f'Active verified listings: {live_props}; stale/unverified active flags: {stale_props}\n'
                 f'Figures come from local application records. Confirm payouts in Stripe; no lease/approval is implied.')
        msgs.append((f'digest:{day}',message))
    return msgs


def send_telegram(token,chat_id,message, timeout=15):
    if not token or not chat_id:
        raise RuntimeError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set')
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data=urllib.parse.urlencode({'chat_id':chat_id,'text':message,'disable_web_page_preview':'true'}).encode()
    req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as resp:
        result=json.loads(resp.read())
        if not result.get('ok'): raise RuntimeError('Telegram returned an unsuccessful response')


def run_once(config:Config, notifier=send_telegram, now=None):
    # Never present fictional/demo data as real income. TEST_NOTIFICATIONS
    # lets the notifier run for synthetic test checkouts; those messages are
    # labeled TEST and never counted as real revenue.
    live = os.getenv("OPERATING_LIVE", "false").lower() in ("true", "1")
    test = os.getenv("TEST_NOTIFICATIONS", "false").lower() in ("true", "1")
    if not live and not test:
        return []
    now=now or now_utc()
    apps,props=read_snapshot(config.application_db)
    state=connect_state(config.state_db)
    sent=[]
    try:
        for key,body in prepare_messages(apps,props,now,config.daily_hour,config.delivery_escalate_minutes, test_mode=test, daily_digest=config.daily_digest):
            if state.execute('SELECT 1 FROM message_log WHERE event_key=?',(key,)).fetchone(): continue
            # Only record event as delivered after Telegram acknowledges request.
            notifier(config.token,config.chat_id,body)
            state.execute('INSERT OR IGNORE INTO message_log(event_key,sent_at) VALUES (?,?)', (key,now.isoformat()))
            state.commit()
            sent.append(key)
    finally:state.close()
    return sent


if __name__=='__main__':
    cfg=Config.from_environment()
    if not cfg.token or not cfg.chat_id:
        raise SystemExit('Configure Telegram token/chat_id in environment. No messages sent.')
    if not 0 <= cfg.daily_hour <= 23: raise SystemExit('DAILY_REPORT_HOUR_ET must be 0..23')
    if cfg.poll_seconds < 15: raise SystemExit('AUTOPILOT_POLL_SECONDS must be >=15')
    print('RentalRelay operations notifier online. Polling verified payment data; not creating listings or charges.',flush=True)
    while True:
        try:
            keys=run_once(cfg)
            if keys: print('Sent event(s):', ', '.join(keys),flush=True)
        except Exception as e:
            # Avoid printing Telegram bot credentials or personal applicant data.
            print(f'Operations notifier retrying after error: {type(e).__name__}',flush=True)
        time.sleep(cfg.poll_seconds)
