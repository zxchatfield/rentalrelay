#!/usr/bin/env python3
"""Read-only operational preflight for RentalRelay and Muse.

Never prints tokens, applicants, manager email addresses, or property addresses.
Use in Muse's scheduled checks and before turning on live intake.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from manager_api_delivery import safe_endpoint

import database

ROOT = Path(__file__).resolve().parent
FLAGS = ('business_identity', 'business_support', 'policies_reviewed', 'stripe_connected',
         'stripe_webhook_tested', 'refund_tested', 'manager_agreements',
         'privacy_security', 'launch_approved')
LISTING_FLAGS = ('advert_permission', 'intake_permission', 'photo_rights',
                 'secure_delivery_verified')


def _true(key):
    return os.getenv(key, '').strip().lower() in ('true', '1')


def _valid_https(base):
    parsed = urlsplit(base)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password


def _fresh(iso, now):
    if not iso:
        return False
    try:
        t = dt.datetime.fromisoformat(iso)
        return bool(t.tzinfo) and dt.timedelta(0) <= now - t < dt.timedelta(hours=48)
    except (TypeError, ValueError):
        return False


def inspect(env=None, database_path=None, now=None):
    """Return a privacy-preserving machine readable status, with explicit blockers."""
    if env is not None:
        old_env = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
    try:
        now = now or dt.datetime.now(dt.timezone.utc)
        blocks=[]
        env_checks = {
            'operating_live': _true('OPERATING_LIVE'),
            'session_secret_set': len(os.getenv('SESSION_SECRET', '')) >= 32,
            'admin_hash_set': os.getenv('ADMIN_PASSWORD_HASH', '').startswith('scrypt$'),
            'admin_username_set': bool(os.getenv('ADMIN_USERNAME', '')),
            'stripe_live_key_set': os.getenv('STRIPE_SECRET_KEY', '').startswith('sk_live_'),
            'stripe_webhook_secret_set': os.getenv('STRIPE_WEBHOOK_SECRET', '').startswith('whsec_'),
            'https_configured': _valid_https(os.getenv('PUBLIC_BASE_URL', '')) and _true('SESSION_HTTPS'),
            'telegram_notifications_configured': bool(os.getenv('TELEGRAM_BOT_TOKEN')) and bool(os.getenv('TELEGRAM_CHAT_ID')),
            'facebook_page_publishing_configured': bool(os.getenv('FB_PAGE_ID')) and bool(os.getenv('FB_PAGE_ACCESS_TOKEN')) and _true('SOCIAL_POSTS_ENABLED'),
            'manager_delivery_enabled': _true('AUTO_MANAGER_DELIVERY_ENABLED'),
        }
        for k in ('session_secret_set', 'admin_hash_set', 'admin_username_set',
                  'stripe_live_key_set', 'stripe_webhook_secret_set', 'https_configured'):
            if not env_checks[k]:
                blocks.append('setup:'+k)
        path=Path(database_path or os.getenv('DATABASE_PATH') or ROOT/'rentalrelay.sqlite3')
        counts={'authorized_fresh_listings':0, 'active_but_not_ready_listings':0,
                'paid_waiting_delivery':0, 'delivered_applications':0,
                'authorized_with_receiver':0, 'delivery_held_properties':0}
        env_checks['delivery_worker_healthy']=False
        env_checks['approved_receiver_mapping_present']=False
        try:
            receiver_map=json.loads(os.getenv('MANAGER_API_DESTINATIONS_JSON','{}'))
            if not isinstance(receiver_map,dict): receiver_map={}
        except (TypeError,ValueError): receiver_map={}
        env_checks['approved_receiver_mapping_present']=any(
            isinstance(x,dict) and safe_endpoint(x.get('url','')) and len(x.get('secret',''))>=24
            for x in receiver_map.values())
        flags={k:False for k in FLAGS}
        if database.is_remote() or path.exists():
            try:
                # SQLite open in actual read-only mode. Never mutate application's database.
                conn=database.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
                conn.row_factory=sqlite3.Row
                try:
                    row=conn.execute('SELECT * FROM launch_config WHERE id=1').fetchone()
                    if row:
                        flags={k:bool(row[k]) for k in FLAGS}
                    props=conn.execute('SELECT is_demo,public_active,verified_at,advert_permission,intake_permission,photo_rights,secure_delivery_verified,manager_email FROM properties').fetchall()
                    for prop in props:
                        if prop['is_demo'] or not prop['public_active']:
                            continue
                        if all(prop[k] for k in LISTING_FLAGS) and bool(prop['manager_email']) and _fresh(prop['verified_at'],now):
                            counts['authorized_fresh_listings']+=1
                            d=receiver_map.get(prop['manager_email'].lower(),{})
                            if isinstance(d,dict) and safe_endpoint(d.get('url','')) and len(d.get('secret',''))>=24:
                                counts['authorized_with_receiver']+=1
                        else:
                            counts['active_but_not_ready_listings']+=1
                    counts['paid_waiting_delivery']=conn.execute("SELECT count(*) FROM applications WHERE paid_at IS NOT NULL AND status IN ('paid','delivery_pending')").fetchone()[0]
                    counts['delivered_applications']=conn.execute("SELECT count(*) FROM applications WHERE status='delivered'").fetchone()[0]
                    table_names={t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'delivery_worker_health' in table_names:
                        h=conn.execute('SELECT checked_at FROM delivery_worker_health WHERE id=1').fetchone()
                        if h:
                            env_checks['delivery_worker_healthy']=_fresh(h[0],now) and (now-dt.datetime.fromisoformat(h[0]))<dt.timedelta(minutes=15)
                    if 'delivery_holds' in table_names:
                        counts['delivery_held_properties']=conn.execute('SELECT count(*) FROM delivery_holds').fetchone()[0]
                finally:
                    conn.close()
            except (*database.ERRORS, OSError):
                blocks.append('database:unreadable_or_invalid_schema')
        else:
            blocks.append('database:not_initialized')
        for k,v in flags.items():
            if not v:
                blocks.append('launch_checklist:'+k)
        if counts['authorized_fresh_listings'] == 0:
            blocks.append('inventory:no_fresh_authorized_listing')
        if not env_checks['manager_delivery_enabled']:
            blocks.append('fulfillment:manager_worker_disabled')
        if not env_checks['approved_receiver_mapping_present'] or counts['authorized_with_receiver'] == 0:
            blocks.append('fulfillment:no_approved_receiver_for_authorized_listing')
        if not env_checks['delivery_worker_healthy']:
            blocks.append('fulfillment:delivery_worker_not_recently_seen')
        if counts['delivery_held_properties']:
            blocks.append('fulfillment:property_delivery_hold')
        if counts['paid_waiting_delivery']:
            blocks.append('operations:applications_waiting_delivery')
        if counts['active_but_not_ready_listings']:
            blocks.append('inventory:active_listings_without_current_approval')
        return {
            'checked_at_utc':now.isoformat(),
            'status':'ready_for_review' if not blocks and env_checks['operating_live'] else 'setup_or_attention_required',
            'live_configuration_enabled':env_checks['operating_live'],
            'payments_ready_for_review': not any(k.startswith(('setup:','launch_checklist:','inventory:','fulfillment:')) for k in blocks) and env_checks['operating_live'],
            'checks':env_checks,
            'counts':counts,
            'blockers':blocks,
            'note':'This is a configuration health check only. It does not confirm real Stripe charges, live HTTPS reachability, actual manager delivery, legal requirements, or Facebook publishing permissions.'
        }
    finally:
        if env is not None:
            os.environ.clear()
            os.environ.update(old_env)


def main():
    parser=argparse.ArgumentParser(description='Privacy-preserving RentalRelay operational preflight')
    parser.add_argument('--json',action='store_true',help='Emit compact machine-readable JSON for Muse')
    args=parser.parse_args()
    status=inspect()
    if args.json:
        print(json.dumps(status,sort_keys=True))
    else:
        print('RentalRelay preflight:',status['status'])
        print('Fresh authorized listings:',status['counts']['authorized_fresh_listings'])
        print('Paid applications awaiting delivery:',status['counts']['paid_waiting_delivery'])
        print('Needs attention:',', '.join(status['blockers']) or 'none')
        print(status['note'])


if __name__=='__main__':
    main()
