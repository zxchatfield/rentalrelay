#!/usr/bin/env python3
"""Optional deterministic transactional email for applicants.

Enabled ONLY after authorized business SMTP and real live-mode onboarding.
Sends verified payment, manager delivery, and refund-status notices.
No marketing, no applicant content beyond their opaque tracking link.
Does not claim emails were delivered to inboxes; SMTP acceptance is not proof.
"""
from __future__ import annotations
import datetime as dt
import os
import sqlite3
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

import database

ROOT=Path(__file__).resolve().parent


def valid_address(value):
    return (isinstance(value,str) and 5<=len(value)<=254 and value.count('@')==1
            and '\r' not in value and '\n' not in value
            and '.' in value.rsplit('@',1)[-1] and ' ' not in value)


def smtp_send(to, subject, content):
    host=os.getenv('SMTP_HOST','').strip()
    port=int(os.getenv('SMTP_PORT','465'))
    user=os.getenv('SMTP_USERNAME','').strip()
    pwd=os.getenv('SMTP_PASSWORD','')
    sender=os.getenv('SUPPORT_FROM_EMAIL','').strip()
    if not host or not pwd or not user or not valid_address(sender):
        raise RuntimeError('Authorized transactional email provider is not configured')
    if not valid_address(to):
        raise ValueError('Invalid applicant email address')
    msg=EmailMessage()
    msg['From']=sender
    msg['To']=to
    msg['Subject']=subject
    msg.set_content(content)
    with smtplib.SMTP_SSL(host,port,timeout=15,context=ssl.create_default_context()) as smtp:
        smtp.login(user,pwd)
        smtp.send_message(msg)


def run_once(application_db,state_db,base_url,enabled=False,sender=smtp_send,now=None):
    if not enabled or os.getenv('OPERATING_LIVE','false').lower() not in ('true','1'):
        return {'sent':0,'disabled':True}
    origin=urlsplit(base_url)
    if origin.scheme!='https' or not origin.hostname:
        raise ValueError('A valid public HTTPS hostname is required')
    now=now or dt.datetime.now(dt.timezone.utc)
    with database.connect(application_db,timeout=20) as conn:
        conn.row_factory=sqlite3.Row
        # Only recent events to avoid sending old confirmations on first installation.
        cutoff=(now-dt.timedelta(days=7)).isoformat()
        rows=conn.execute('''SELECT id,email,tracking_token,status,paid_at,delivered_at,refunded_at
           FROM applications WHERE paid_at IS NOT NULL AND
           (paid_at>=? OR delivered_at>=? OR refunded_at>=?)''',(cutoff,cutoff,cutoff)).fetchall()
    with database.connect(state_db,timeout=20) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS email_outbox (event_key TEXT PRIMARY KEY, acknowledged_at TEXT NOT NULL)')
        sent=0
        for app in rows:
            if not valid_address(app['email']):continue
            tracking=f'{base_url.rstrip("/")}/track/{app["tracking_token"]}'
            messages=[]
            if app['paid_at']:
                messages.append((f'paid:{app["id"]}', 'RentalRelay payment confirmed',
                  'Your $24.99 application forwarding-service payment was verified. This does not mean the manager has approved or received your application yet.\n\nCheck status: '+tracking))
            if app['status']=='delivered' and app['delivered_at']:
                messages.append((f'delivered:{app["id"]}', 'RentalRelay application forwarded',
                  'Your application has been acknowledged by the participating property manager. They decide all rental outcomes.\n\nCheck status: '+tracking))
            if app['status']=='refunded' and app['refunded_at']:
                messages.append((f'refunded:{app["id"]}', 'RentalRelay refund status',
                  'A refund has been recorded for your application-service payment. Check your payment provider for settlement timing.\n\nCheck status: '+tracking))
            for key,subject,body in messages:
                if conn.execute('SELECT 1 FROM email_outbox WHERE event_key=?',(key,)).fetchone():continue
                sender(app['email'],subject,body)
                conn.execute('INSERT INTO email_outbox VALUES (?,?)',(key,now.isoformat()))
                conn.commit()
                sent+=1
    return {'sent':sent,'disabled':False}


if __name__=='__main__':
    outcome=run_once(os.getenv('DATABASE_PATH',str(ROOT/'rentalrelay.sqlite3')),
        os.getenv('OPERATIONS_STATE_DB',str(ROOT/'operations_state.sqlite3')),
        os.getenv('PUBLIC_BASE_URL',''),
        os.getenv('AUTO_CUSTOMER_NOTIFICATIONS_ENABLED','false').lower() in ('true','1'))
    print({'event':'customer_notification_poll','sent':outcome['sent'],'disabled':outcome['disabled']})
