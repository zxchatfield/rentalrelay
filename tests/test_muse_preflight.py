import datetime as dt
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from muse_preflight import inspect


def test_fresh_blank_install_requires_setup(tmp_path):
    status=inspect(env={'OPERATING_LIVE':'false'},database_path=tmp_path/'not-here.sqlite')
    assert status['payments_ready_for_review'] is False
    assert 'database:not_initialized' in status['blockers']
    assert not status['checks']['stripe_live_key_set']
    assert not any('sk_live' in x for x in status['blockers'])


def test_approved_listings_counted_without_exposing_personal_data(tmp_path):
    db=tmp_path/'rentalrelay.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.executescript('''
         CREATE TABLE launch_config(id INTEGER PRIMARY KEY,business_identity INTEGER,business_support INTEGER,policies_reviewed INTEGER,
            stripe_connected INTEGER,stripe_webhook_tested INTEGER,refund_tested INTEGER,manager_agreements INTEGER,
            privacy_security INTEGER,launch_approved INTEGER);
         INSERT INTO launch_config VALUES(1,1,1,1,1,1,1,1,1,1);
         CREATE TABLE properties(is_demo INTEGER,public_active INTEGER,verified_at TEXT,advert_permission INTEGER,intake_permission INTEGER,
            photo_rights INTEGER,secure_delivery_verified INTEGER,manager_email TEXT);
         CREATE TABLE applications(paid_at TEXT,status TEXT);
         INSERT INTO applications VALUES('2026-11-24T12:00:00+00:00','delivery_pending');
         ''')
        now=dt.datetime(2026,11,25,12,tzinfo=dt.timezone.utc)
        conn.execute('INSERT INTO properties VALUES(0,1,?,?,?,?,?,?)',(now.isoformat(),1,1,1,1,'private.manager@example.com'))
    env={'OPERATING_LIVE':'true','SESSION_SECRET':'a'*40,'ADMIN_USERNAME':'operator','ADMIN_PASSWORD_HASH':'scrypt$placeholder',
         'STRIPE_SECRET_KEY':'sk_live_fake_wont_connect','STRIPE_WEBHOOK_SECRET':'whsec_fake',
         'PUBLIC_BASE_URL':'https://rentalrelay.example','SESSION_HTTPS':'true'}
    report=inspect(env=env,database_path=db,now=now)
    assert report['counts']['authorized_fresh_listings']==1
    assert report['counts']['paid_waiting_delivery']==1
    assert report['payments_ready_for_review'] is False
    assert 'fulfillment:delivery_worker_not_recently_seen' in report['blockers']
    assert 'applications_waiting_delivery' in ' '.join(report['blockers'])
    assert 'private.manager@example.com' not in str(report)
    assert 'fake_wont_connect' not in str(report)
