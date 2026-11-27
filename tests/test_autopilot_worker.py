import datetime as dt
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autopilot_worker import Config, prepare_messages, run_once

ET=ZoneInfo('America/Detroit')

def test_paid_notice_not_lease_or_delivery():
    when=dt.datetime(2026,11,25,5,0,tzinfo=dt.timezone.utc)
    apps=[{'id':'A','status':'delivery_pending','paid_at':when.isoformat(),'delivered_at':None,'refunded_at':None}]
    msgs=prepare_messages(apps,[],when,daily_hour=8)
    assert [k for k,_ in msgs]==['paid:A']
    assert msgs[0][1] == '💰 New payment: $24.99\n📍 Property not recorded\n\nToday: $24.99\nThis month: $24.99'
    assert 'lease' not in msgs[0][1].lower()
    assert 'delivered' not in msgs[0][1].lower()
    assert 'A' not in msgs[0][1].split('\n')[0]

def test_daily_report_dedup_and_no_pii(tmp_path,monkeypatch):
    monkeypatch.setenv("OPERATING_LIVE","true")
    db=tmp_path/'app.db'
    con=sqlite3.connect(db)
    con.executescript('''CREATE TABLE applications(id TEXT,property_id TEXT,status TEXT,paid_at TEXT,delivered_at TEXT,refunded_at TEXT);
      CREATE TABLE properties(id TEXT,address TEXT,city TEXT,is_demo INTEGER,public_active INTEGER,verified_at TEXT,
      advert_permission INTEGER,intake_permission INTEGER,photo_rights INTEGER,secure_delivery_verified INTEGER);
    ''')
    when=dt.datetime(2026,11,25,9,15,tzinfo=ET)
    yesterday=when-dt.timedelta(days=1)
    con.execute('INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?)',('prop1','123 Test St','Detroit',0,1,None,1,1,1,1))
    con.execute('INSERT INTO applications VALUES(?,?,?,?,?,?)',('app123','prop1','delivery_pending',yesterday.isoformat(),None,None))
    con.commit();con.close()
    logs=[]
    def mock(token,chat_id,body):logs.append(body)
    cfg=Config(db,tmp_path/'state.db','fake','123',8,60,120)
    assert len(run_once(cfg, mock, when))==3
    assert run_once(cfg, mock, when)==[]
    assert len(logs)==3
    assert any('Gross charges: $24.99' in s for s in logs)
    assert any('📍 123 Test St, Detroit' in s for s in logs)
    assert any('Today: $24.99' in s for s in logs)
    assert any('This month: $24.99' in s for s in logs)
    assert all('Delivery status' not in s for s in logs)
    assert all('full name' not in s.lower() for s in logs)

def test_alert_after_two_hours():
    now=dt.datetime(2026,11,25,15,tzinfo=dt.timezone.utc)
    apps=[{'id':'A','status':'delivery_pending','paid_at':(now-dt.timedelta(hours=3)).isoformat(),'delivered_at':None,'refunded_at':None}]
    msgs=dict(prepare_messages(apps,[],now))
    assert any(k.startswith('undelivered:A') for k in msgs)

def test_no_payment_notified_without_paid_at():
    now=dt.datetime(2026,11,25,15,tzinfo=dt.timezone.utc)
    apps=[{'id':'A','status':'payment_pending','paid_at':None,'delivered_at':None,'refunded_at':None}]
    assert not [k for k,_ in prepare_messages(apps,[],now) if k.startswith('paid:')]


def test_demo_worker_never_claims_payment(tmp_path,monkeypatch):
    monkeypatch.setenv("OPERATING_LIVE","false")
    # The app .env can set TEST_NOTIFICATIONS=true when other tests import
    # app.py (load_dotenv); this test must pin both flags off.
    monkeypatch.setenv("TEST_NOTIFICATIONS","false")
    c=Config(tmp_path/"not-real.db",tmp_path/"ops.db","fake","1")
    assert run_once(c,lambda *args: None)==[]


def test_test_notifications_send_labeled_test_message(tmp_path,monkeypatch):
    monkeypatch.setenv("OPERATING_LIVE","false")
    monkeypatch.setenv("TEST_NOTIFICATIONS","true")
    db=tmp_path/'app.db'
    con=sqlite3.connect(db)
    con.executescript('''CREATE TABLE applications(id TEXT,property_id TEXT,status TEXT,paid_at TEXT,delivered_at TEXT,refunded_at TEXT);
      CREATE TABLE properties(id TEXT,address TEXT,city TEXT,is_demo INTEGER,public_active INTEGER,verified_at TEXT,
      advert_permission INTEGER,intake_permission INTEGER,photo_rights INTEGER,secure_delivery_verified INTEGER);
    ''')
    when=dt.datetime(2026,11,25,9,15,tzinfo=ET)
    con.execute('INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?)',('prop1','123 Test St','Detroit',0,1,None,1,1,1,1))
    con.execute('INSERT INTO applications VALUES(?,?,?,?,?,?)',('app123','prop1','packet_ready',when.isoformat(),None,None))
    con.commit();con.close()
    logs=[]
    def mock(token,chat_id,body):logs.append(body)
    cfg=Config(db,tmp_path/'state.db','fake','123',8,60,120)
    sent=run_once(cfg, mock, when)
    assert 'paid:app123' in sent
    assert any(s.startswith('🧪 TEST PAYMENT') for s in logs)
    assert any(s.startswith('🧪 TEST PAYMENT\n💰 New payment: $24.99') for s in logs)
    assert run_once(cfg, mock, when)==[]


def test_morning_digest_reports_finished_yesterday_not_incomplete_today():
    now=dt.datetime(2026,11,25,8,30,tzinfo=ET)
    prior=now-dt.timedelta(days=1)
    apps=[
        {'id':'prior','status':'delivered','paid_at':prior.isoformat(),'delivered_at':prior.isoformat(),'refunded_at':None},
        {'id':'today','status':'delivery_pending','paid_at':now.isoformat(),'delivered_at':None,'refunded_at':None},
    ]
    digest=[body for key,body in prepare_messages(apps,[],now) if key.startswith('digest:')]
    assert len(digest)==1
    assert 'completed day 2026-11-24' in digest[0]
    assert 'Verified paid applications today: 1' in digest[0] or 'Verified paid applications for completed day: 1' in digest[0]
    assert 'Gross charges: $24.99' in digest[0]


def test_daily_digest_disabled_sends_no_digest_but_keeps_payment_message():
    now=dt.datetime(2026,11,25,8,30,tzinfo=ET)
    prior=now-dt.timedelta(days=1)
    apps=[
        {'id':'prior','status':'delivered','paid_at':prior.isoformat(),'delivered_at':prior.isoformat(),'refunded_at':None},
    ]
    msgs=prepare_messages(apps,[],now,daily_digest=False)
    assert not [key for key,body in msgs if key.startswith('digest:')]
    assert any(key=='paid:prior' for key,body in msgs)
