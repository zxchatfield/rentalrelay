#!/usr/bin/env python3
"""OPTIONAL secure automated delivery to a manager's preapproved HTTPS INTAKE API.

Disabled by default. Does not support unapproved email addresses or scraped endpoints.
A manager must give a designated HTTPS intake endpoint, accept our form fields,
share an API authentication secret by secure means, and return JSON receipt_id.
Their endpoint MUST deduplicate Idempotency-Key; responses are HTTPS authenticated.

Treat API receipt as delivery/acceptance of the submission, NOT approval for tenancy.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import database

ROOT=Path(__file__).resolve().parent
FLAGS=('business_identity','business_support','policies_reviewed','stripe_connected','stripe_webhook_tested','refund_tested','manager_agreements','privacy_security','launch_approved')


def safe_endpoint(url):
    if not isinstance(url,str) or not url:
        return False
    parsed=urlsplit(url)
    host=(parsed.hostname or '').lower().rstrip('.')
    if not host or host=='localhost' or host.endswith(('.local','.internal','.localhost')):
        return False
    try:
        # Do not let a manager URL point to loopback/private/link-local/cloud metadata IPs.
        if not ipaddress.ip_address(host).is_global:
            return False
    except ValueError:
        pass  # Regular DNS host; infrastructure should also block private DNS resolution.
    return (parsed.scheme=='https' and not parsed.username and not parsed.password
            and not parsed.fragment)


def get_pending(db_path):
    con=database.connect(db_path,timeout=20)
    con.row_factory=sqlite3.Row
    try:
        cfg=con.execute('SELECT * FROM launch_config WHERE id=1').fetchone()
        if not cfg or not all(cfg[k] for k in FLAGS): return []
        now=dt.datetime.now(dt.timezone.utc)
        rows=con.execute('''SELECT a.*, p.title AS property_title, p.manager_email, p.manager_name,
            p.intake_permission,p.advert_permission,p.photo_rights,p.secure_delivery_verified,p.public_active,p.verified_at,
            p.is_demo
            FROM applications a JOIN properties p ON a.property_id=p.id
            WHERE a.status='delivery_pending' AND a.paid_at IS NOT NULL''').fetchall()
        approved=[]
        for r in rows:
            if r['is_demo'] or not all(r[k] for k in ('intake_permission','advert_permission','photo_rights','secure_delivery_verified','public_active')):continue
            if not all(r[k] for k in ('consent_delivery','consent_service','consent_disclosure','consent_policies')):continue
            try:valid=dt.datetime.fromisoformat(r['verified_at']); fresh=valid.tzinfo and dt.timedelta(0) <= now-valid < dt.timedelta(hours=48)
            except (TypeError,ValueError):fresh=False
            if fresh:approved.append(dict(r))
        return approved
    finally:con.close()


def post_delivery(url, secret, payload, timeout=20):
    if not safe_endpoint(url): raise ValueError('Only verified HTTPS manager intake endpoints are allowed')
    body=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()
    ts=str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    sig=hmac.new(secret.encode(),(ts+'.').encode()+body,hashlib.sha256).hexdigest()
    req=urllib.request.Request(url,data=body,method='POST',headers={
        'Content-Type':'application/json','X-RentalRelay-Timestamp':ts,
        'X-RentalRelay-Signature':'sha256='+sig,'Idempotency-Key':payload['application_id']})
    # Refuse redirects: never forward personal application data to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener=urllib.request.build_opener(NoRedirect())
    with opener.open(req,timeout=timeout) as resp:
        result=json.loads(resp.read())
    if result.get('accepted') is not True or not isinstance(result.get('receipt_id'),str) or len(result['receipt_id'])<3:
        raise RuntimeError('Manager did not confirm an accepted application receipt')
    return result['receipt_id']


def run_once(db_path,manager_mapping,enabled=False, sender=post_delivery):
    """manager_mapping must be verified exact manager EMAIL -> {'url':..., 'secret':...}.

    Only for participating managers whose receiving systems explicitly consented. Does
    not mark an application delivered unless the manager's API returns receipt_id.
    """
    if not enabled or os.getenv('OPERATING_LIVE','false').lower() not in ('true','1'):
        return {'processed':[],'skipped':'Automatic delivery disabled'}
    ready=get_pending(db_path);processed=[];failures=0
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    # An independent liveness signal gates NEW payments in the web application.
    with database.connect(db_path,timeout=20) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS delivery_worker_health (id INTEGER PRIMARY KEY CHECK(id=1), checked_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS delivery_holds (property_id TEXT PRIMARY KEY, held_at TEXT NOT NULL, failure_count INTEGER NOT NULL DEFAULT 1)')
        conn.execute('INSERT INTO delivery_worker_health(id,checked_at) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at',(now,))
    held_this_pass=set()
    for a in ready:
        if a['property_id'] in held_this_pass:
            continue
        destination=manager_mapping.get(a['manager_email'].lower())
        if not destination or not safe_endpoint(destination.get('url','')) or len(destination.get('secret',''))<24:
            failures+=1
            held_this_pass.add(a['property_id'])
            with database.connect(db_path,timeout=20) as conn:
                conn.execute('INSERT INTO delivery_holds(property_id,held_at) VALUES (?,?) ON CONFLICT(property_id) DO UPDATE SET held_at=excluded.held_at, failure_count=failure_count+1',(a['property_id'],now))
            continue
        payload={'application_id':a['id'],'property_id':a['property_id'],
         'applicant':{'full_name':a['full_name'],'email':a['email'],'phone':a['phone'],
           'move_in':a['move_in'],'occupants':a['occupants'],'pets':a['pets'],
           'employment':a['employment'],'income_range':a['income'],'notes':a['notes']},
         'consent':{'delivery':bool(a['consent_delivery']),'version':a['consent_version'],'at_utc':a['consent_at']},
         'source':'RentalRelay independent application forwarding',
         'property_title':a['property_title']}
        try:
            # Integration partner must implement application_id idempotency to avoid duplicate
            # processing if response is lost between accepted and local DB commit.
            receipt=sender(destination['url'],destination['secret'],payload)
        except Exception:
            # Retry on the next scheduled run; hold future charges until receipt arrives.
            failures+=1
            held_this_pass.add(a['property_id'])
            with database.connect(db_path,timeout=20) as conn:
                conn.execute('INSERT INTO delivery_holds(property_id,held_at) VALUES (?,?) ON CONFLICT(property_id) DO UPDATE SET held_at=excluded.held_at, failure_count=failure_count+1',(a['property_id'],now))
            continue
        with database.connect(db_path,timeout=20) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS manager_delivery_audit(
                 application_id TEXT PRIMARY KEY, manager_email TEXT NOT NULL,receipt_id TEXT NOT NULL,delivered_at TEXT NOT NULL)""")
            existing=conn.execute('SELECT 1 FROM manager_delivery_audit WHERE application_id=?',(a['id'],)).fetchone()
            if existing:continue
            timestamp=dt.datetime.now(dt.timezone.utc).isoformat()
            result=conn.execute('UPDATE applications SET status=?, delivered_at=? WHERE id=? AND status=?',
                                ('delivered',timestamp,a['id'],'delivery_pending'))
            if result.rowcount!=1:continue
            conn.execute('INSERT INTO manager_delivery_audit VALUES (?,?,?,?)',(a['id'],a['manager_email'],receipt,timestamp))
            conn.execute('DELETE FROM delivery_holds WHERE property_id=?',(a['property_id'],))
        processed.append({'application_id':a['id'],'receipt_id':receipt})
    return {'processed':processed,'failed_or_not_configured':failures}



if __name__=='__main__':
    if os.getenv('AUTO_MANAGER_DELIVERY_ENABLED','false').lower() not in ('true','1'):
        print('Automatic manager delivery is disabled. Requires agreement and approved HTTPS endpoint; no data sent.')
        raise SystemExit(0)
    mapping=json.loads(os.getenv('MANAGER_API_DESTINATIONS_JSON','{}'))
    try:
        result=run_once(os.getenv('DATABASE_PATH',str(ROOT/'rentalrelay.sqlite3')),mapping,True)
        print(json.dumps(result,indent=2))
    except Exception as exc:
        # Never print payload, destination, or credentials.
        print('Manager delivery failed:',type(exc).__name__)
        raise SystemExit(1)
