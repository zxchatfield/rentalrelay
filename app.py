import datetime as dt
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import database
from password import verify_password
from stripe_client import checkout_session, refund, verify_stripe_signature, StripeError
from manager_api_delivery import safe_endpoint
from research_leads import seed_research_leads, REVIEW_STATUSES
from autopilot_worker import prepare_messages, send_telegram

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')
DATABASE = os.getenv('DATABASE_PATH', str(ROOT/'rentalrelay.sqlite3'))
DEMO = not (os.getenv('OPERATING_LIVE', 'false').lower() in ('true', '1'))
SECRET = os.getenv('SESSION_SECRET', '')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH', '')
STRIPE_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
PRICE = 2499  # fixed server-side, not controlled by application/user
BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
RETENTION_DAYS = max(7, min(730, int(os.getenv('DATA_RETENTION_DAYS', '90'))))

# Fail closed whenever production configuration is incomplete.
READY = (not DEMO and len(SECRET) >= 32 and ADMIN_USERNAME and ADMIN_PASSWORD_HASH.startswith('scrypt$')
         and STRIPE_KEY.startswith('sk_live_') and STRIPE_WEBHOOK_SECRET.startswith('whsec_')
         and BASE_URL.startswith('https://') and os.getenv('SESSION_HTTPS', '').lower() in ('true','1'))

# Synthetic-test path: lets a test-mode checkout run end to end on the demo
# listing without weakening the live gate. Only active when TEST_MODE is set
# explicitly AND the app is in demo mode AND the Stripe key is a test key
# (or the authd surrogate for one) AND a test webhook secret is configured.
# A test key can never move real money. READY (live) is unchanged.
TEST_MODE = os.getenv('TEST_MODE', 'false').lower() in ('true', '1')
TEST_READY = (TEST_MODE and DEMO and STRIPE_KEY.startswith(('sk_test_', 'hsurr:'))
              and STRIPE_WEBHOOK_SECRET.startswith('whsec_'))

app = FastAPI(title='RentalRelay Application Service')
app.add_middleware(SessionMiddleware,
    secret_key=SECRET if len(SECRET) >= 32 else secrets.token_hex(32),
    same_site='lax', https_only=os.getenv('SESSION_HTTPS', '').lower() in ('true', '1'))
templates = Jinja2Templates(directory=str(ROOT/'templates'))
app.mount('/static', StaticFiles(directory=str(ROOT/'static')), name='static')

@contextmanager
def db():
    conn = database.connect(DATABASE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as conn:
        # WAL is a local-file journaling mode; Turso is a remote database
        # and rejects it, so only enable it for a local sqlite3 file.
        if not database.is_remote():
            conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS properties (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, city TEXT NOT NULL, address TEXT NOT NULL,
            rent_cents INTEGER NOT NULL, deposit_cents INTEGER NOT NULL, beds REAL, baths REAL,
            sqft INTEGER, details TEXT, pets TEXT, manager_name TEXT NOT NULL,
            manager_email TEXT NOT NULL, manager_fee TEXT NOT NULL DEFAULT 'Ask manager',
            source_url TEXT NOT NULL, tour_url TEXT, advert_permission INTEGER NOT NULL DEFAULT 0,
            intake_permission INTEGER NOT NULL DEFAULT 0, photo_rights INTEGER NOT NULL DEFAULT 0,
            secure_delivery_verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT,
            public_active INTEGER NOT NULL DEFAULT 0,
            is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY, tracking_token TEXT UNIQUE NOT NULL, property_id TEXT NOT NULL,
            full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
            move_in TEXT NOT NULL, occupants INTEGER NOT NULL,
            pets TEXT NOT NULL, employment TEXT NOT NULL, income TEXT NOT NULL, notes TEXT NOT NULL,
            consent_delivery INTEGER NOT NULL, consent_service INTEGER NOT NULL,
            consent_disclosure INTEGER NOT NULL, consent_policies INTEGER NOT NULL,
            consent_version TEXT NOT NULL, consent_at TEXT NOT NULL, created_at TEXT NOT NULL,
            retention_until TEXT NOT NULL,
            stripe_session_id TEXT UNIQUE, stripe_payment_intent TEXT, stripe_event_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending', paid_at TEXT, delivered_at TEXT, refunded_at TEXT,
            FOREIGN KEY(property_id) REFERENCES properties(id)
        );
        CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, received_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS telegram_notifications (
            id TEXT PRIMARY KEY, application_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'payment',
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT,
            UNIQUE(application_id, kind)
        );
        CREATE TABLE IF NOT EXISTS delivery_worker_health (
            id INTEGER PRIMARY KEY CHECK(id=1), checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delivery_holds (
            property_id TEXT PRIMARY KEY, held_at TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(property_id) REFERENCES properties(id)
        );
        CREATE TABLE IF NOT EXISTS launch_config (
          id INTEGER PRIMARY KEY CHECK(id=1), business_identity INTEGER DEFAULT 0,
          business_support INTEGER DEFAULT 0, policies_reviewed INTEGER DEFAULT 0,
          stripe_connected INTEGER DEFAULT 0, stripe_webhook_tested INTEGER DEFAULT 0,
          refund_tested INTEGER DEFAULT 0, manager_agreements INTEGER DEFAULT 0,
          privacy_security INTEGER DEFAULT 0, launch_approved INTEGER DEFAULT 0
        );
        INSERT OR IGNORE INTO launch_config(id) VALUES (1);
        ''')
        # Migration: assistance path records which product an application bought.
        cols=[r[1] for r in conn.execute('PRAGMA table_info(applications)').fetchall()]
        if 'service_type' not in cols:
            conn.execute("ALTER TABLE applications ADD COLUMN service_type TEXT NOT NULL DEFAULT 'submission'")
        if not conn.execute('SELECT 1 FROM properties WHERE is_demo=1').fetchone():
            conn.execute('''INSERT INTO properties (id,title,city,address,rent_cents,deposit_cents,beds,baths,sqft,details,pets,
                manager_name,manager_email,manager_fee,source_url,created_at,is_demo,public_active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('DEMO-ONLY','Fictional sample two-bedroom','Fictional, MI','Example location — not a real address',
                 145000,145000,2,1,900,'This rental does not exist; interface demonstration only.','Fictional pet policy',
                 'Fictional Manager','demo@example.invalid','$40 (fictional)','https://example.invalid',utc(),1,1))
        seed_research_leads(conn)
        conn.execute('DELETE FROM applications WHERE retention_until < ?', (utc(),))

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def money(cents): return '${:,.2f}'.format(cents/100)
def one(sql, params=()):
    with db() as conn: return conn.execute(sql,params).fetchone()

def csrf(request):
    if not request.session.get('csrf'):
        request.session['csrf'] = secrets.token_urlsafe(24)
    return request.session['csrf']

def check_csrf(request, supplied):
    if not supplied or not secrets.compare_digest(csrf(request), supplied):
        raise HTTPException(403, 'Invalid request token')

def is_admin(request): return bool(request.session.get('admin'))
def require_admin(request):
    if not is_admin(request): raise HTTPException(403, 'Administrator access required')

def render(request, name, **kw):
    return templates.TemplateResponse(request, name, {'demo':DEMO,'ready':operational_ready(),'price':money(PRICE),
        'csrf':csrf(request),'admin':is_admin(request),'year':dt.date.today().year, **kw})

def authorized(p):
    if not p or p['is_demo'] or not p['public_active']: return False
    if not all(p[k] for k in ('advert_permission','intake_permission','photo_rights','secure_delivery_verified')): return False
    if not p['verified_at'] or not p['manager_email']: return False
    try: verified = dt.datetime.fromisoformat(p['verified_at'])
    except ValueError: return False
    if verified.tzinfo is None: return False
    return dt.timedelta(0) <= dt.datetime.now(dt.timezone.utc) - verified < dt.timedelta(hours=48)

def operational_ready():
    if not READY: return False
    launch=one('SELECT * FROM launch_config WHERE id=1')
    return bool(launch and all(launch[k] for k in (
        'business_identity','business_support','policies_reviewed','stripe_connected',
        'stripe_webhook_tested','refund_tested','manager_agreements','privacy_security','launch_approved')))

def assistance_ready():
    """Readiness for the renter-authorized assistance path: live Stripe plus
    the launch checklist, EXCEPT manager_agreements, which belongs to the
    integrated delivery path only. Assistance needs no manager receiver,
    so it must never be blocked waiting for one. A synthetic test checkout
    (TEST_READY) is allowed without the launch checklist."""
    if TEST_READY:
        return True
    if not READY: return False
    launch=one('SELECT * FROM launch_config WHERE id=1')
    flags=('business_identity','business_support','policies_reviewed','stripe_connected',
           'stripe_webhook_tested','refund_tested','privacy_security','launch_approved')
    return bool(launch and all(launch[k] for k in flags))

def auto_delivery_ready(p):
    """Fail closed if a manager receiver is not configured or the delivery worker stopped.

    The intake adapter must have been polling within 15 minutes and the property
    must not be on hold after an unsuccessful or unacknowledged delivery.
    """
    if not p or not authorized(p):
        return False
    if os.getenv('AUTO_MANAGER_DELIVERY_ENABLED', 'false').lower() not in ('1', 'true'):
        return False
    try:
        mapping = json.loads(os.getenv('MANAGER_API_DESTINATIONS_JSON', '{}'))
        destination = mapping.get(p['manager_email'].lower())
        if not isinstance(destination, dict) or not safe_endpoint(destination.get('url', '')):
            return False
        if len(destination.get('secret', '')) < 24:
            return False
        with db() as conn:
            heartbeat = conn.execute('SELECT checked_at FROM delivery_worker_health WHERE id=1').fetchone()
            hold = conn.execute('SELECT 1 FROM delivery_holds WHERE property_id=?', (p['id'],)).fetchone()
            oldest = conn.execute('SELECT 1 FROM applications WHERE property_id=? AND status IN (\'paid\',\'delivery_pending\') AND paid_at < ?',
                                  (p['id'], (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=120)).isoformat())).fetchone()
        if hold or oldest or not heartbeat:
            return False
        last = dt.datetime.fromisoformat(heartbeat['checked_at'])
        age = dt.datetime.now(dt.timezone.utc) - last
        return bool(last.tzinfo and dt.timedelta(0) <= age < dt.timedelta(minutes=15))
    except (ValueError, TypeError, sqlite3.Error, AttributeError):
        return False


def live_listings():
    with db() as conn:
        props = conn.execute('SELECT * FROM properties WHERE public_active=1 ORDER BY created_at DESC').fetchall()
    return [p for p in props if (p['is_demo'] and DEMO) or (not DEMO and authorized(p))]

def public_listing(p):
    if p and ((p['is_demo'] and DEMO) or (not DEMO and authorized(p))):return p
    raise HTTPException(404, 'Listing not available')

init_db()
_last_purge = 0.0

@app.middleware('http')
async def protective_headers(request: Request, call_next):
    # Clear expired applications when this server receives traffic.
    global _last_purge
    now_seconds = dt.datetime.now(dt.timezone.utc).timestamp()
    if now_seconds - _last_purge > 3600:
        with db() as conn:
            conn.execute('DELETE FROM applications WHERE retention_until < ?', (utc(),))
        _last_purge = now_seconds
    response = await call_next(request)
    response.headers.update({
        'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
        'X-Frame-Options':'DENY','Cache-Control':'no-store',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'self' https://checkout.stripe.com; object-src 'none'",
    })
    return response

@app.get('/', response_class=HTMLResponse)
def home(request:Request): return render(request,'home.html',listings=live_listings()[:3])

@app.get('/rentals', response_class=HTMLResponse)
def rentals(request:Request): return render(request,'rentals.html',listings=live_listings())

@app.get('/rentals/{property_id}', response_class=HTMLResponse)
def listing(request:Request, property_id:str):
    p=public_listing(one('SELECT * FROM properties WHERE id=?',(property_id,)))
    return render(request,'detail.html',p=p,eligible=operational_ready() and auto_delivery_ready(p),assist_eligible=assistance_ready())

@app.get('/apply/{property_id}', response_class=HTMLResponse)
def apply_page(request:Request, property_id:str):
    p=public_listing(one('SELECT * FROM properties WHERE id=?',(property_id,)))
    return render(request,'apply.html',p=p,can_apply=operational_ready() and auto_delivery_ready(p),errors=[])

def validate_application(d):
    errors=[]
    if len(d['full_name'].strip()) not in range(3,121): errors.append('Enter your full name.')
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$',d['email'].strip()) or len(d['email']) > 254: errors.append('Enter a valid email address.')
    if not re.match(r'^[+()\-.\d\s]{7,25}$',d['phone']): errors.append('Enter a valid phone number.')
    try:
        if dt.date.fromisoformat(d['move_in']) < dt.date.today(): errors.append('Move-in date cannot be in the past.')
    except ValueError: errors.append('Enter a move-in date.')
    if d['occupants'] not in range(1,21):errors.append('Enter an occupant count from 1 to 20.')
    if d['employment'] not in ('Employed','Self-employed','Student','Retired','Other','Prefer not to say'):errors.append('Choose an employment status.')
    if d['income'] not in ('Under $2,000','$2,000–$3,999','$4,000–$5,999','$6,000–$7,999','$8,000+','Prefer not to say'):errors.append('Choose a household income range.')
    if len(d['notes'])>1000 or len(d['pets'])>300:errors.append('Message is too long.')
    if not all(d[c] for c in ('consent_delivery','consent_service','consent_disclosure','consent_policies')):
        errors.append('All consent acknowledgments must be checked.')
    return errors

@app.get('/assist/{property_id}', response_class=HTMLResponse)
def assist_page(request:Request, property_id:str):
    p=public_listing(one('SELECT * FROM properties WHERE id=?',(property_id,)))
    return render(request,'assist.html',p=p,can_assist=assistance_ready(),errors=[])

ASSIST_CONSENT_VERSION='2026-11-25-assistance-v1'
ACTIVE_STATUSES=('payment_pending','paid','delivery_pending','delivered','packet_ready')

@app.post('/assist/{property_id}', response_class=HTMLResponse)
def assist_post(request:Request,property_id:str,csrf_token:str=Form(''),full_name:str=Form(''),email:str=Form(''),
               phone:str=Form(''),move_in:str=Form(''),occupants:int=Form(0),pets:str=Form(''),employment:str=Form(''),
               income:str=Form(''),notes:str=Form(''), consent_delivery:str=Form(''), consent_service:str=Form(''),
               consent_disclosure:str=Form(''), consent_policies:str=Form('')):
    """Renter-authorized application assistance: preparation + guidance only.
    No manager receiver is required. The renter submits the finished packet
    themself through the manager's official route; this never claims the
    application was filed with, or accepted by, the manager."""
    check_csrf(request,csrf_token)
    p=public_listing(one('SELECT * FROM properties WHERE id=?',(property_id,)))
    if not assistance_ready():
        raise HTTPException(403, 'Application assistance and payments are not activated for this listing')
    data=dict(full_name=full_name,email=email.lower(),phone=phone,move_in=move_in,occupants=occupants,pets=pets,
              employment=employment,income=income,notes=notes,consent_delivery=(consent_delivery=='yes'),
              consent_service=(consent_service=='yes'),consent_disclosure=(consent_disclosure=='yes'),
              consent_policies=(consent_policies=='yes'))
    errors=validate_application(data)
    if errors:return render(request,'assist.html',p=p,can_assist=True,errors=errors)
    app_id=str(uuid.uuid4())
    token=secrets.token_urlsafe(32)
    date_now=dt.datetime.now(dt.timezone.utc)
    with db() as conn:
        # Never charge the same e-mail twice for the same rental in a 24h period
        placeholders=','.join('?' for _ in ACTIVE_STATUSES)
        existing=conn.execute(f'SELECT id FROM applications WHERE email=? AND property_id=? AND created_at>? AND status IN ({placeholders})',
                    (email.lower(),property_id,(date_now-dt.timedelta(hours=24)).isoformat(),*ACTIVE_STATUSES)).fetchone()
        if existing: raise HTTPException(409,'An existing application for this property is already in progress. Contact support before retrying.')
        conn.execute('''INSERT INTO applications (id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,employment,income,notes,
            consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,status,service_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'assistance')''',(
                app_id,token,property_id,full_name.strip(),email.lower().strip(),phone.strip(),move_in,occupants,pets.strip(),
                employment,income,notes.strip(),1,1,1,1,ASSIST_CONSENT_VERSION,date_now.isoformat(),date_now.isoformat(),
                (date_now+dt.timedelta(days=RETENTION_DAYS)).isoformat(),'pending'))
    try:
        session=checkout_session(app_id,token,p['title']+' — application assistance',PRICE,BASE_URL,email.lower().strip())
        if session.get('id') and session.get('url'):
            with db() as conn:
                conn.execute('UPDATE applications SET stripe_session_id=?, status=? WHERE id=?',(session['id'],'payment_pending',app_id))
            return RedirectResponse(session['url'],status_code=303)
        raise StripeError('Checkout not available')
    except StripeError as exc:
        logging.getLogger(__name__).warning('Assistance checkout failed for application %s: %s', app_id, exc)
        with db() as conn:conn.execute('UPDATE applications SET status=? WHERE id=?',('failed',app_id))
        return render(request,'notice.html',heading='Payment unavailable',message='No charge was taken. Please try again later or contact us.',code=503)

@app.post('/apply/{property_id}', response_class=HTMLResponse)
def apply_post(request:Request,property_id:str,csrf_token:str=Form(''),full_name:str=Form(''),email:str=Form(''),
               phone:str=Form(''),move_in:str=Form(''),occupants:int=Form(0),pets:str=Form(''),employment:str=Form(''),
               income:str=Form(''),notes:str=Form(''), consent_delivery:str=Form(''), consent_service:str=Form(''),
               consent_disclosure:str=Form(''), consent_policies:str=Form('')):
    check_csrf(request,csrf_token)
    p=public_listing(one('SELECT * FROM properties WHERE id=?',(property_id,)))
    if not operational_ready() or not auto_delivery_ready(p):
        raise HTTPException(403, 'Applications and payments are not activated for this listing')
    data=dict(full_name=full_name,email=email.lower(),phone=phone,move_in=move_in,occupants=occupants,pets=pets,
              employment=employment,income=income,notes=notes,consent_delivery=(consent_delivery=='yes'),
              consent_service=(consent_service=='yes'),consent_disclosure=(consent_disclosure=='yes'),
              consent_policies=(consent_policies=='yes'))
    errors=validate_application(data)
    if errors:return render(request,'apply.html',p=p,can_apply=True,errors=errors)
    app_id=str(uuid.uuid4())
    token=secrets.token_urlsafe(32)
    date_now=dt.datetime.now(dt.timezone.utc)
    with db() as conn:
        # Never charge the same e-mail twice for the same rental in a 24h period
        placeholders=','.join('?' for _ in ACTIVE_STATUSES)
        existing=conn.execute(f'SELECT id FROM applications WHERE email=? AND property_id=? AND created_at>? AND status IN ({placeholders})',
                    (email.lower(),property_id,(date_now-dt.timedelta(hours=24)).isoformat(),*ACTIVE_STATUSES)).fetchone()
        if existing: raise HTTPException(409,'An existing application for this property is already in progress. Contact support before retrying.')
        conn.execute('''INSERT INTO applications (id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,employment,income,notes,
            consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                app_id,token,property_id,full_name.strip(),email.lower().strip(),phone.strip(),move_in,occupants,pets.strip(),
                employment,income,notes.strip(),1,1,1,1,'2026-11-24-v1',date_now.isoformat(),date_now.isoformat(),
                (date_now+dt.timedelta(days=RETENTION_DAYS)).isoformat(),'pending'))
    try:
        session=checkout_session(app_id,token,p['title'],PRICE,BASE_URL,email.lower().strip())
        if session.get('id') and session.get('url'):
            with db() as conn:
                conn.execute('UPDATE applications SET stripe_session_id=?, status=? WHERE id=?',(session['id'],'payment_pending',app_id))
            return RedirectResponse(session['url'],status_code=303)
        raise StripeError('Checkout not available')
    except StripeError as exc:
        logging.getLogger(__name__).warning('Submission checkout failed for application %s: %s', app_id, exc)
        with db() as conn:conn.execute('UPDATE applications SET status=? WHERE id=?',('failed',app_id))
        return render(request,'notice.html',heading='Payment unavailable',message='No charge was taken. Please try again later or contact us.',code=503)

# Telegram payment notifications, integrated into the web service (no second
# service). When Stripe confirms a payment the webhook persists the payment
# AND queues a Telegram notification in the same database transaction. The
# webhook tries to send it immediately; the watchdog thread below retries
# anything still pending, so a failed send or a restart never loses it.
TELEGRAM_MAX_ATTEMPTS = 60
TELEGRAM_WATCHDOG_SECONDS = 60
_NOTIFY_LOCK = threading.Lock()

def _telegram_config():
    return os.getenv('TELEGRAM_BOT_TOKEN', '').strip(), os.getenv('TELEGRAM_CHAT_ID', '').strip()

def _payment_notification_message(application_id):
    """Build the Telegram payment message for one application via the shared
    worker formatter. Returns None when the application has no verified payment."""
    with db() as conn:
        a = conn.execute('SELECT * FROM applications WHERE id=?', (application_id,)).fetchone()
        if not a or not a['paid_at']:
            return None
        apps = [dict(r) for r in conn.execute('SELECT * FROM applications').fetchall()]
        props = [dict(r) for r in conn.execute('SELECT * FROM properties').fetchall()]
    msgs = prepare_messages(apps, props, now=dt.datetime.now(dt.timezone.utc), daily_digest=False, test_mode=DEMO)
    for key, body in msgs:
        if key == f'paid:{application_id}':
            return body
    return None

def attempt_payment_notification(application_id):
    """Send the queued Telegram payment notification for one application.
    Returns True when the message is (now) sent. Safe to call repeatedly:
    an already-sent notification is skipped, and a failed send stays queued
    for the watchdog. Serialized so the webhook and the watchdog can never
    send the same notification twice."""
    with _NOTIFY_LOCK:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_notifications WHERE application_id=? AND kind='payment'",
                (application_id,)).fetchone()
            if not row:
                return False
            if row['status'] == 'sent':
                return True
            row_id = row['id']
        message = _payment_notification_message(application_id)
        if not message:
            with db() as conn:
                conn.execute("UPDATE telegram_notifications SET attempts=attempts+1, last_error=? WHERE id=?",
                             ('no verified payment message', row_id))
            return False
        token, chat_id = _telegram_config()
        try:
            send_telegram(token, chat_id, message)
        except Exception as exc:  # noqa: BLE001 - record and retry, never crash the webhook
            logging.getLogger(__name__).warning('Telegram payment notification failed for %s: %s', application_id, exc)
            with db() as conn:
                conn.execute("UPDATE telegram_notifications SET attempts=attempts+1, last_error=? WHERE id=?",
                             (str(exc)[:500], row_id))
                cur = conn.execute("SELECT attempts FROM telegram_notifications WHERE id=?", (row_id,)).fetchone()
                if cur and cur['attempts'] >= TELEGRAM_MAX_ATTEMPTS:
                    conn.execute("UPDATE telegram_notifications SET status='failed' WHERE id=?", (row_id,))
            return False
        with db() as conn:
            conn.execute("UPDATE telegram_notifications SET status='sent', sent_at=?, attempts=attempts+1 WHERE id=?",
                         (utc(), row_id))
        return True

def _retry_pending_notifications():
    with db() as conn:
        rows = conn.execute(
            "SELECT application_id FROM telegram_notifications WHERE status='pending' AND attempts < ? ORDER BY created_at",
            (TELEGRAM_MAX_ATTEMPTS,)).fetchall()
    for r in rows:
        try:
            attempt_payment_notification(r['application_id'])
        except Exception:  # noqa: BLE001 - watchdog must keep running
            logging.getLogger(__name__).exception('Telegram notification retry failed for %s', r['application_id'])

def _notification_watchdog_loop():
    # Poll immediately on startup so a restart picks up pending notifications
    # right away, then every minute.
    while True:
        try:
            _retry_pending_notifications()
        except Exception:  # noqa: BLE001 - watchdog must keep running
            logging.getLogger(__name__).exception('Telegram notification watchdog error')
        time.sleep(TELEGRAM_WATCHDOG_SECONDS)

@app.on_event('startup')
def _start_notification_watchdog():
    thread = threading.Thread(target=_notification_watchdog_loop, daemon=True, name='telegram-notification-watchdog')
    thread.start()

@app.post('/stripe/webhook')
async def stripe_webhook(request:Request):
    try:
        event=verify_stripe_signature(await request.body(), request.headers.get('stripe-signature',''),STRIPE_WEBHOOK_SECRET)
    except StripeError:
        raise HTTPException(400,'Invalid signature')
    eid=event['id']; obj=event.get('data',{}).get('object',{})
    notify_app_id=None
    with db() as conn:
        if conn.execute('SELECT 1 FROM stripe_events WHERE id=?',(eid,)).fetchone():return {'ok':True,'duplicate':True}
        if event.get('type')=='checkout.session.completed':
            app_id=obj.get('client_reference_id')
            a=conn.execute('SELECT * FROM applications WHERE id=?',(app_id,)).fetchone()
            if not a or a['stripe_session_id']!=obj.get('id') or obj.get('payment_status')!='paid' or obj.get('amount_total')!=PRICE or obj.get('currency')!='usd':
                raise HTTPException(400,'Payment reference mismatch')
            # Assistance: the paid deliverable is the prepared packet itself,
            # ready for the renter on their tracking page. It is NOT a filing
            # with the manager, so it never becomes 'delivered' and never
            # triggers the worker's undelivered-manager-delivery escalation.
            new_status='packet_ready' if a['service_type']=='assistance' else 'delivery_pending'
            cur=conn.execute('UPDATE applications SET status=?, stripe_payment_intent=?, paid_at=? WHERE id=? AND status=?',
                    (new_status,obj.get('payment_intent'),utc(),app_id,'payment_pending'))
            if cur.rowcount:
                # Persist the Telegram notification in the same transaction as
                # the payment, so a confirmed payment is never lost. The send
                # is attempted right after this commit; the watchdog retries
                # anything still pending.
                conn.execute("INSERT OR IGNORE INTO telegram_notifications(id, application_id, kind, status, attempts, created_at) VALUES (?,?,?,?,0,?)",
                             (f'payment:{app_id}', app_id, 'payment', 'pending', utc()))
                notify_app_id=app_id
        elif event.get('type')=='charge.refunded':
            pi=obj.get('payment_intent')
            if pi:
                conn.execute('UPDATE applications SET status=?,refunded_at=? WHERE stripe_payment_intent=? AND status!=?',
                             ('refunded',utc(),pi,'refunded'))
        conn.execute('INSERT INTO stripe_events(id,received_at) VALUES (?,?)',(eid,utc()))
    if notify_app_id:
        attempt_payment_notification(notify_app_id)
    return {'ok':True}

@app.get('/checkout/success')
def success(request:Request):
    return render(request,'notice.html',heading='Payment is being verified',message='Your payment must be confirmed directly by Stripe before we mark your application paid. Check your receipt and contact our support for the application reference.',code=200)

@app.get('/checkout/cancel')
def cancel(request:Request):
    return render(request,'notice.html',heading='Checkout cancelled',message='No successful payment has been confirmed. You can return to the rental listing.',code=200)

@app.get('/track/{token}')
def track(request:Request,token:str):
    if len(token)<30: raise HTTPException(404,'Unknown application')
    a=one('SELECT * FROM applications WHERE tracking_token=?',(token,))
    if not a:raise HTTPException(404,'Unknown application')
    p=one('SELECT title,source_url,manager_name,manager_fee FROM properties WHERE id=?',(a['property_id'],))
    # The tracking token is the access credential for this page. For a paid
    # assistance application the packet itself is the deliverable, so the
    # renter sees their prepared details plus the official route here.
    packet_ready = a['service_type']=='assistance' and a['status']=='packet_ready'
    return render(request,'track.html',a=a,p=p,packet_ready=packet_ready)

@app.get('/how-it-works')
def how(request:Request):return render(request,'info.html',heading='How it works',kind='how')
@app.get('/faq')
def faq(request:Request):return render(request,'info.html',heading='Frequently asked questions',kind='faq')
@app.get('/terms')
def terms(request:Request):return render(request,'info.html',heading='Terms of service — draft',kind='terms')
@app.get('/privacy')
def privacy(request:Request):return render(request,'info.html',heading='Privacy policy — draft',kind='privacy')
@app.get('/refund-policy')
def refund_policy(request:Request):return render(request,'info.html',heading='Refund policy — draft',kind='refunds')
@app.get('/contact')
def contact(request:Request):return render(request,'info.html',heading='Contact',kind='contact')

@app.get('/healthz')
def healthz():
    try:
        with db() as conn:
            conn.execute('SELECT 1').fetchone()
        return {'status':'up'}
    except database.ERRORS:
        return JSONResponse({'status':'unavailable'},status_code=503)

@app.get('/prep/demo', response_class=HTMLResponse)
def prep_demo(request: Request):
    """Zero-payment, no-database draft packet demonstration."""
    if not DEMO: raise HTTPException(404, "Preview is not publicly available")
    return render(request, 'prep_demo.html', errors=[])

@app.post('/prep/demo', response_class=HTMLResponse)
def prep_demo_post(request: Request, csrf_token: str=Form(''), full_name: str=Form(''),
                   email: str=Form(''), phone: str=Form(''), target_url: str=Form(''),
                   move_in: str=Form(''), occupants: int=Form(0), pets: str=Form(''),
                   employment: str=Form(''), income: str=Form(''), notes: str=Form(''),
                   consent_packet: str=Form('')):
    if not DEMO: raise HTTPException(404, "Preview is not publicly available")
    check_csrf(request, csrf_token)
    data = dict(full_name=full_name.strip(), email=email.lower().strip(),phone=phone.strip(),
                move_in=move_in, occupants=occupants, pets=pets.strip(), employment=employment,
                income=income, notes=notes.strip(), consent_delivery=True, consent_service=True,
                consent_disclosure=True, consent_policies=True)
    errors=validate_application(data)
    parsed=urlparse(target_url)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc or len(target_url)>1000:
        errors.append('Enter a valid public listing link.')
    if consent_packet != 'yes': errors.append('Confirm this is only a draft packet.')
    if errors: return render(request, 'prep_demo.html', errors=errors)
    return render(request, 'prep_packet.html', applicant=data, target_url=target_url,
                  prepared_at=utc())

@app.get('/admin/inbox', response_class=HTMLResponse)
def inbox_desk(request: Request):
    require_admin(request)
    with db() as conn:
        leads=conn.execute('SELECT * FROM research_leads ORDER BY priority').fetchall()
    return render(request,'inbox_desk.html',leads=leads,selected_id='FICTIONAL',
                  customer_message='',result=None,error=None)

@app.post('/admin/inbox/draft', response_class=HTMLResponse)
def inbox_draft(request: Request, csrf_token: str=Form(''), lead_id: str=Form(''),
                customer_message: str=Form('')):
    require_admin(request)
    check_csrf(request,csrf_token)
    from inquiry_desk import draft_for_test
    with db() as conn:
        leads=conn.execute('SELECT * FROM research_leads ORDER BY priority').fetchall()
        lead=conn.execute('SELECT * FROM research_leads WHERE id=?',(lead_id,)).fetchone()
    if lead_id!='FICTIONAL' and not lead:
        raise HTTPException(404,'Research property not found')
    try:
        result=draft_for_test(customer_message,dict(lead) if lead else None,
                              simulate_verified=(lead_id=='FICTIONAL'))
    except ValueError as exc:
        return render(request,'inbox_desk.html',leads=leads,selected_id=lead_id,
                      customer_message='',result=None,error=str(exc))
    # Keep customer text out of the database, HTML, and HTTP logs.
    return render(request,'inbox_desk.html',leads=leads,selected_id=lead_id,
                  customer_message='',result=result,error=None)

@app.get('/admin/research', response_class=HTMLResponse)
def admin_research(request: Request):
    require_admin(request)
    with db() as conn:
        rows=conn.execute('SELECT * FROM research_leads ORDER BY priority').fetchall()
    leads=[{**dict(r), 'issues':json.loads(r['issues_json'])} for r in rows]
    return render(request, 'research.html', leads=leads, statuses=REVIEW_STATUSES)

@app.post('/admin/research/{lead_id}/status')
def update_research(request: Request, lead_id: str, csrf_token: str=Form(''),
                    review_status: str=Form(''), operator_notes: str=Form('')):
    require_admin(request)
    check_csrf(request, csrf_token)
    if review_status not in REVIEW_STATUSES: raise HTTPException(400, 'Invalid review status')
    if len(operator_notes)>1500: raise HTTPException(400, 'Notes too long')
    with db() as conn:
        changed=conn.execute('UPDATE research_leads SET review_status=?,operator_notes=?,status_updated_at=? WHERE id=?',
                             (review_status,operator_notes.strip(),utc(),lead_id))
        if changed.rowcount!=1: raise HTTPException(404,'Research lead not found')
    return RedirectResponse('/admin/research',status_code=303)

@app.get('/admin/diagnostics')
def admin_diagnostics(request:Request):
    require_admin(request)
    from muse_preflight import inspect
    return inspect(database_path=DATABASE)

@app.get('/admin/login')
def admin_login_page(request:Request):return render(request,'admin_login.html',error=None)

_LOGIN_FAILURES={}
@app.post('/admin/login')
def admin_login(request:Request,username:str=Form(''),password:str=Form(''),csrf_token:str=Form('')):
    check_csrf(request,csrf_token)
    ip = request.client.host if request.client else 'unknown'
    now=dt.datetime.now(dt.timezone.utc)
    last=_LOGIN_FAILURES.get(ip,[])
    last=[t for t in last if (now-t).total_seconds()<900]
    if len(last)>=10: raise HTTPException(429,'Too many login attempts. Try again later.')
    if not (ADMIN_USERNAME and ADMIN_PASSWORD_HASH and hmac.compare_digest(username,ADMIN_USERNAME)
            and verify_password(password,ADMIN_PASSWORD_HASH)):
        last.append(now);_LOGIN_FAILURES[ip]=last
        return render(request,'admin_login.html',error='Incorrect credentials or administrator not configured')
    _LOGIN_FAILURES.pop(ip,None)
    request.session.clear();request.session['admin']=True
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/logout')
def logout(request:Request,csrf_token:str=Form('')):
    require_admin(request);check_csrf(request,csrf_token)
    request.session.clear();return RedirectResponse('/',status_code=303)

@app.get('/admin')
def dashboard(request:Request):
    require_admin(request)
    with db() as conn:
        props=conn.execute('SELECT * FROM properties ORDER BY created_at DESC').fetchall()
        launch=conn.execute('SELECT * FROM launch_config WHERE id=1').fetchone()
        apps=conn.execute('SELECT a.*, p.title AS property_title FROM applications a JOIN properties p ON p.id=a.property_id ORDER BY a.created_at DESC LIMIT 50').fetchall()
    return render(request,'admin.html',properties=props,applications=apps,launch=launch)

@app.post('/admin/properties')
def add_property(request:Request,csrf_token:str=Form(''),title:str=Form(''),city:str=Form(''),address:str=Form(''),
                 rent_dollars:int=Form(0),deposit_dollars:int=Form(0),beds:float=Form(0),baths:float=Form(0),
                 details:str=Form(''),pets:str=Form(''), manager_name:str=Form(''),manager_email:str=Form(''),
                 manager_fee:str=Form(''),source_url:str=Form(''),tour_url:str=Form('')):
    require_admin(request);check_csrf(request,csrf_token)
    if not (title and city and address and manager_name and manager_email and source_url):raise HTTPException(400,'Required property fields missing')
    if rent_dollars<1 or deposit_dollars<0:raise HTTPException(400,'Invalid rent')
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$',manager_email):raise HTTPException(400,'Invalid manager email')
    for url in (source_url,tour_url):
        if url and not (urlparse(url).scheme in ('https','http') and urlparse(url).netloc):raise HTTPException(400,'Source and tour URLs must be http(s)')
    with db() as conn:
        conn.execute('''INSERT INTO properties (id,title,city,address,rent_cents,deposit_cents,beds,baths,details,pets,manager_name,manager_email,
            manager_fee,source_url,tour_url,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid.uuid4()),title[:150],city[:80],address[:150],rent_dollars*100,deposit_dollars*100,beds,baths,details[:3000],pets[:200],
             manager_name[:120],manager_email[:255],manager_fee[:120],source_url[:1000],tour_url[:1000],utc()))
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/properties/{property_id}/verify')
def verify_property(request:Request,property_id:str,csrf_token:str=Form(''),advert_permission:str=Form(''),intake_permission:str=Form(''),
                    photo_rights:str=Form(''),secure_delivery_verified:str=Form(''),public_active:str=Form('')):
    require_admin(request);check_csrf(request,csrf_token)
    p=one('SELECT * FROM properties WHERE id=? AND is_demo=0',(property_id,))
    if not p:raise HTTPException(404,'Property not found')
    flags={k:(v=='yes') for k,v in locals().copy().items() if k in ['advert_permission','intake_permission','photo_rights','secure_delivery_verified','public_active']}
    if flags['public_active'] and not all(flags[k] for k in ('advert_permission','intake_permission','photo_rights','secure_delivery_verified')):
        raise HTTPException(400,'Permissions and delivery verification required to publish')
    with db() as conn:
        conn.execute('''UPDATE properties SET advert_permission=?,intake_permission=?,photo_rights=?,secure_delivery_verified=?,
                verified_at=?,public_active=? WHERE id=?''',
                (int(flags['advert_permission']),int(flags['intake_permission']),int(flags['photo_rights']),
                 int(flags['secure_delivery_verified']),utc() if flags['public_active'] else p['verified_at'],int(flags['public_active']),property_id))
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/properties/{property_id}/edit')
def edit_property(request:Request,property_id:str,csrf_token:str=Form(''),
                  title:str=Form(''),city:str=Form(''),address:str=Form(''),
                  rent_dollars:str=Form(''),deposit_dollars:str=Form(''),
                  beds:str=Form(''),baths:str=Form(''),sqft:str=Form(''),details:str=Form(''),pets:str=Form(''),
                  manager_name:str=Form(''),manager_email:str=Form(''),manager_fee:str=Form(''),
                  source_url:str=Form(''),tour_url:str=Form('')):
    # Correct a property's facts after it was added (e.g. a confirmed manager fee).
    # Empty fields are left unchanged, so a small fee-only form cannot wipe other facts.
    # Editing facts does not touch the permission flags or verified_at.
    require_admin(request);check_csrf(request,csrf_token)
    p=one('SELECT * FROM properties WHERE id=? AND is_demo=0',(property_id,))
    if not p:raise HTTPException(404,'Property not found')
    updates={}
    if title.strip(): updates['title']=title.strip()[:150]
    if city.strip(): updates['city']=city.strip()[:80]
    if address.strip(): updates['address']=address.strip()[:150]
    if rent_dollars.strip():
        try: rent=int(rent_dollars.strip())
        except ValueError: raise HTTPException(400,'Invalid rent')
        if rent<1: raise HTTPException(400,'Invalid rent')
        updates['rent_cents']=rent*100
    if deposit_dollars.strip():
        try: dep=int(deposit_dollars.strip())
        except ValueError: raise HTTPException(400,'Invalid deposit')
        if dep<0: raise HTTPException(400,'Invalid deposit')
        updates['deposit_cents']=dep*100
    if beds.strip():
        try: updates['beds']=float(beds.strip())
        except ValueError: raise HTTPException(400,'Invalid bedrooms')
    if baths.strip():
        try: updates['baths']=float(baths.strip())
        except ValueError: raise HTTPException(400,'Invalid bathrooms')
    if sqft.strip():
        try: updates['sqft']=int(sqft.strip())
        except ValueError: raise HTTPException(400,'Invalid square footage')
        if updates['sqft']<0: raise HTTPException(400,'Invalid square footage')
    if details.strip(): updates['details']=details.strip()[:3000]
    if pets.strip(): updates['pets']=pets.strip()[:200]
    if manager_name.strip(): updates['manager_name']=manager_name.strip()[:120]
    if manager_email.strip():
        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$',manager_email.strip()): raise HTTPException(400,'Invalid manager email')
        updates['manager_email']=manager_email.strip()[:255]
    if manager_fee.strip(): updates['manager_fee']=manager_fee.strip()[:120]
    if source_url.strip():
        if urlparse(source_url.strip()).scheme not in ('https','http') or not urlparse(source_url.strip()).netloc:
            raise HTTPException(400,'Source and tour URLs must be http(s)')
        updates['source_url']=source_url.strip()[:1000]
    if tour_url.strip():
        if urlparse(tour_url.strip()).scheme not in ('https','http') or not urlparse(tour_url.strip()).netloc:
            raise HTTPException(400,'Source and tour URLs must be http(s)')
        updates['tour_url']=tour_url.strip()[:1000]
    if not updates: return RedirectResponse('/admin',status_code=303)
    with db() as conn:
        conn.execute('UPDATE properties SET '+','.join(f'{k}=?' for k in updates)+' WHERE id=?',
                     tuple(updates[k] for k in updates)+(property_id,))
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/applications/{app_id}/delivered')
def mark_delivered(request:Request,app_id:str,csrf_token:str=Form(''),receipt_id:str=Form('')):
    require_admin(request);check_csrf(request,csrf_token)
    proof=receipt_id.strip()
    if len(proof)<4 or len(proof)>150:
        raise HTTPException(400,'A genuine property-manager delivery receipt is required')
    with db() as conn:
        a=conn.execute('''SELECT a.status,p.manager_email FROM applications a JOIN properties p
            ON a.property_id=p.id WHERE a.id=?''',(app_id,)).fetchone()
        if not a or a['status'] not in ('delivery_pending','paid'):
            raise HTTPException(400,'Only paid applications can be marked delivered')
        now=utc()
        conn.execute('''CREATE TABLE IF NOT EXISTS manager_delivery_audit(
             application_id TEXT PRIMARY KEY, manager_email TEXT NOT NULL,receipt_id TEXT NOT NULL,delivered_at TEXT NOT NULL)''')
        conn.execute('INSERT INTO manager_delivery_audit VALUES(?,?,?,?)',
                     (app_id,a['manager_email'],proof,now))
        conn.execute('UPDATE applications SET status=?,delivered_at=? WHERE id=?',('delivered',now,app_id))
        conn.execute('DELETE FROM delivery_holds WHERE property_id=(SELECT property_id FROM applications WHERE id=?)',(app_id,))
    return RedirectResponse('/admin',status_code=303)

@app.post('/admin/applications/{app_id}/refund')
def admin_refund(request:Request,app_id:str,csrf_token:str=Form('')):
    require_admin(request);check_csrf(request,csrf_token)
    a=one('SELECT * FROM applications WHERE id=?',(app_id,))
    if not a or not a['stripe_payment_intent'] or a['status'] not in ('delivery_pending','paid','packet_ready'):
        raise HTTPException(400,'Only paid, undelivered applications can be refunded here')
    try: r=refund(a['stripe_payment_intent'],app_id)
    except StripeError as e: raise HTTPException(502,'Stripe refund failed; record unchanged') from e
    if r.get('status') not in ('succeeded','pending'):
        raise HTTPException(502,'Refund not acknowledged by Stripe')
    # pending refunds receive final update from Stripe webhook
    new_status='refunded' if r.get('status')=='succeeded' else 'refund_pending'
    with db() as conn:conn.execute('UPDATE applications SET status=?, refunded_at=? WHERE id=?',
                                  (new_status,utc() if new_status=='refunded' else None,app_id))
    return RedirectResponse('/admin',status_code=303)


LAUNCH_FLAGS = ('business_identity','business_support','policies_reviewed','stripe_connected',
                'stripe_webhook_tested','refund_tested','manager_agreements','privacy_security','launch_approved')

@app.post('/admin/launch-checklist')
async def launch_checklist(request:Request):
    require_admin(request)
    form=await request.form()
    check_csrf(request,str(form.get('csrf_token','')))
    flags={key:int(form.get(key)=='yes') for key in LAUNCH_FLAGS}
    # No background auto-verification: user/operator must actually complete the steps.
    with db() as conn:
        conn.execute('UPDATE launch_config SET '+','.join(f'{key}=?' for key in LAUNCH_FLAGS)+' WHERE id=1', tuple(flags[k] for k in LAUNCH_FLAGS))
    return RedirectResponse('/admin',status_code=303)

@app.get('/admin/applications/{app_id}')
def admin_app_detail(request:Request,app_id:str):
    require_admin(request)
    with db() as conn:
        a=conn.execute('SELECT * FROM applications WHERE id=?',(app_id,)).fetchone()
        if not a: raise HTTPException(404,'Application not found')
        p=conn.execute('SELECT * FROM properties WHERE id=?',(a['property_id'],)).fetchone()
    return render(request,'admin_app.html',a=a,p=p)
