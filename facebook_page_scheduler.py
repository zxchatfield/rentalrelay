#!/usr/bin/env python3
"""Permission-gated Facebook BUSINESS PAGE publisher, never Marketplace/profile/group automation.

Publishes accurate, manager-approved property summaries through an authorized Meta Page API
when an operator has configured Meta's approved API and explicitly enabled the publisher.
Does not create ads, spend on promotions, scrape Meta or use browser anti-bot methods.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

import database as db_adapter

ROOT=Path(__file__).resolve().parent


def validated_posts(database,base_url, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    if not base_url.startswith('https://'): raise ValueError('Production base URL must be HTTPS')
    if not db_adapter.is_remote() and not Path(database).is_file(): return []
    uri='file:'+urllib.parse.quote(str(Path(database).resolve()))+'?mode=ro'
    conn=db_adapter.connect(uri,uri=True)
    conn.row_factory=sqlite3.Row
    try:
        # A production Page must not advertise our paid service until the
        # operator has completed the website's launch checklist.
        config=conn.execute('SELECT * FROM launch_config WHERE id=1').fetchone()
        required=('business_identity','business_support','policies_reviewed',
                  'stripe_connected','stripe_webhook_tested','refund_tested',
                  'manager_agreements','privacy_security','launch_approved')
        if config is None or not all(config[k] for k in required):
            return []
        props=conn.execute('SELECT * FROM properties WHERE is_demo=0 AND public_active=1').fetchall()
    finally:conn.close()
    out=[]
    for p in props:
        if not all(p[k] for k in ('advert_permission','intake_permission','photo_rights','secure_delivery_verified')):
            continue
        try: verified=dt.datetime.fromisoformat(p['verified_at'])
        except (ValueError,TypeError):continue
        if not verified.tzinfo or not dt.timedelta(0) <= (now-verified) < dt.timedelta(hours=48):continue
        if not p['manager_name'] or not p['manager_email']: continue
        rent=f'${p["rent_cents"] / 100:,.0f}'
        manager_fee=p['manager_fee']
        # factual, no unverifiable quality promises or manufactured urgency
        caption=(f'🏡 Rental in {p["city"]}: {p["beds"]:g} bedrooms, {p["baths"]:g} baths. '
                 f'Rent {rent}/month. Managed by {p["manager_name"]}. '
                 f'Our optional independent application collection and forwarding service is $24.99; '
                 f'the manager may charge separately ({manager_fee}). '
                 f'Tours and applications can also be arranged directly with the manager. '
                 f'Information last verified {verified.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}. '
                 f'View details: {base_url.rstrip("/")}/rentals/{urllib.parse.quote(p["id"])}. '
                 'Equal Housing Opportunity.')
        out.append((p['id'],caption))
    return out


def publish_page_post(page_id, access_token, text, graph_version='v27.0'):
    if not page_id.isdigit() or not graph_version.startswith('v'):raise ValueError('Invalid Page ID/version')
    endpoint=f'https://graph.facebook.com/{graph_version}/{page_id}/feed'
    body=urllib.parse.urlencode({'message':text,'access_token':access_token}).encode()
    req=urllib.request.Request(endpoint,data=body,method='POST')
    with urllib.request.urlopen(req,timeout=25) as resp:
        result=json.loads(resp.read())
    if 'id' not in result: raise RuntimeError('Meta did not acknowledge a published post')
    return result['id']


def run_once(database,state_path,base_url,page_id,token,enabled,publisher=publish_page_post):
    if not enabled:return {'published':[],'skipped':'SOCIAL_POSTS_ENABLED is false'}
    if not page_id or not token:return {'published':[],'skipped':'Meta Page ID or token not configured'}
    if os.getenv('OPERATING_LIVE','false').lower() not in ('1','true'):
        return {'published':[],'skipped':'Production application not enabled'}
    posts=validated_posts(database,base_url)
    with db_adapter.connect(state_path) as state:
        state.execute('CREATE TABLE IF NOT EXISTS social_post_log(property_id TEXT PRIMARY KEY, meta_post_id TEXT NOT NULL, created_at TEXT NOT NULL)')
        state.commit()
        published=[]
        for prop_id,caption in posts:
            if state.execute('SELECT 1 FROM social_post_log WHERE property_id=?',(prop_id,)).fetchone(): continue
            result_id=publisher(page_id,token,caption)
            state.execute('INSERT OR IGNORE INTO social_post_log VALUES(?,?,?)',(prop_id,result_id,dt.datetime.now(dt.timezone.utc).isoformat()))
            state.commit()
            published.append((prop_id,result_id))
    return {'published':published}


if __name__=='__main__':
    import sys
    database=Path(os.getenv('DATABASE_PATH',ROOT/'rentalrelay.sqlite3'))
    try:
        result = run_once(database, Path(os.getenv('OPERATIONS_STATE_DB',ROOT/'operations_state.sqlite3')),
           os.getenv('PUBLIC_BASE_URL',''), os.getenv('FB_PAGE_ID',''), os.getenv('FB_PAGE_ACCESS_TOKEN',''),
           os.getenv('SOCIAL_POSTS_ENABLED','false').lower() in ('true','1'))
        print(json.dumps(result,indent=2))
    except Exception as exc:
        print(f'Page publisher failed: {type(exc).__name__}',file=sys.stderr)
        raise SystemExit(1)
