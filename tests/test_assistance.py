import datetime as dt
import hashlib
import hmac
import importlib
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _reload_app(monkeypatch, tmp_path, name):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / name))
    monkeypatch.setenv('OPERATING_LIVE', 'false')
    # Pin test mode off: the app .env now carries TEST_MODE=true and test
    # secrets, and load_dotenv would re-add them on reload if merely deleted.
    monkeypatch.setenv('TEST_MODE', 'false')
    monkeypatch.setenv('STRIPE_SECRET_KEY', '')
    import app
    importlib.reload(app)
    return app


def _csrf(client, url):
    page = client.get(url)
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _valid_assist_data(email='amy@example.com'):
    return {
        'full_name': 'Amy Test', 'email': email, 'phone': '(555) 555-0123',
        'move_in': (dt.date.today() + dt.timedelta(days=15)).isoformat(),
        'occupants': '1', 'pets': '', 'employment': 'Employed',
        'income': '$4,000–$5,999', 'notes': '',
        'consent_delivery': 'yes', 'consent_service': 'yes',
        'consent_disclosure': 'yes', 'consent_policies': 'yes',
    }


def test_assist_page_renders_in_demo(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-page.sqlite3')
    client = TestClient(app.app)
    res = client.get('/assist/DEMO-ONLY')
    assert res.status_code == 200
    assert 'Application assistance' in res.text
    assert 'does not file the application' in res.text
    assert 'consent_delivery' in res.text
    assert 'disabled' in res.text  # demo: no payments accepted
    detail = client.get('/rentals/DEMO-ONLY')
    assert '/assist/DEMO-ONLY' in detail.text
    assert 'Get application help' in detail.text or 'Preview application help' in detail.text


def test_assist_post_blocked_in_demo(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-demo.sqlite3')
    client = TestClient(app.app)
    r = client.post('/assist/DEMO-ONLY', data=_valid_assist_data())
    assert r.status_code == 403
    with app.db() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM applications').fetchone()['n'] == 0


def test_assist_post_success_without_manager_delivery(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-success.sqlite3')
    monkeypatch.setattr(app, 'assistance_ready', lambda: True)
    # No manager delivery is configured in this environment at all.
    with app.db() as conn:
        p = conn.execute('SELECT * FROM properties WHERE id=?', ('DEMO-ONLY',)).fetchone()
    assert app.auto_delivery_ready(p) is False
    def fake_checkout(app_id, token, title, price, base_url, email):
        assert 'application assistance' in title
        assert price == 2499
        return {'id': 'cs_assist_1', 'url': 'https://checkout.stripe.com/assist-test'}
    monkeypatch.setattr(app, 'checkout_session', fake_checkout)
    client = TestClient(app.app)
    token = _csrf(client, '/assist/DEMO-ONLY')
    data = _valid_assist_data()
    data['csrf_token'] = token
    r = client.post('/assist/DEMO-ONLY', data=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers['location'] == 'https://checkout.stripe.com/assist-test'
    with app.db() as conn:
        a = conn.execute('SELECT * FROM applications').fetchone()
        assert a is not None
        assert a['service_type'] == 'assistance'
        assert a['consent_version'] == '2026-11-25-assistance-v1'
        assert a['status'] == 'payment_pending'
        assert a['stripe_session_id'] == 'cs_assist_1'


def test_assist_validation_errors(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-validation.sqlite3')
    monkeypatch.setattr(app, 'assistance_ready', lambda: True)
    client = TestClient(app.app)
    token = _csrf(client, '/assist/DEMO-ONLY')
    data = _valid_assist_data()
    data['csrf_token'] = token
    del data['consent_service']
    r = client.post('/assist/DEMO-ONLY', data=data)
    assert r.status_code == 200
    assert 'Please correct these' in r.text
    with app.db() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM applications').fetchone()['n'] == 0


def test_assist_duplicate_within_24h(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-dup.sqlite3')
    monkeypatch.setattr(app, 'assistance_ready', lambda: True)
    monkeypatch.setattr(app, 'checkout_session',
        lambda *a, **k: {'id': 'cs_dup_1', 'url': 'https://checkout.stripe.com/dup'})
    client = TestClient(app.app)
    token = _csrf(client, '/assist/DEMO-ONLY')
    data = _valid_assist_data()
    data['csrf_token'] = token
    assert client.post('/assist/DEMO-ONLY', data=data, follow_redirects=False).status_code == 303
    token = _csrf(client, '/assist/DEMO-ONLY')
    data['csrf_token'] = token
    assert client.post('/assist/DEMO-ONLY', data=data).status_code == 409


def _signed_webhook(monkeypatch, tmp_path, name, service_type):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / name))
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test_key')
    import app
    importlib.reload(app)
    with app.db() as conn:
        conn.execute('''INSERT INTO applications(id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,
         employment,income,notes,consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,
         stripe_session_id,status,service_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
         ('app-9', 'opaque-assist-token-1234567890abcdef', 'DEMO-ONLY', 'Fake Applicant', 'fake@example.invalid',
          '555-0110', '2027-01-01', 1, '', 'Other', '$4,000–$5,999', '', 1, 1, 1, 1, 'test', app.utc(), app.utc(),
          (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).isoformat(),
          'cs_test_9', 'payment_pending', service_type))
    payload = {'id': 'evt_assist_1', 'type': 'checkout.session.completed', 'data': {'object': {
        'client_reference_id': 'app-9', 'id': 'cs_test_9', 'payment_status': 'paid',
        'amount_total': 2499, 'currency': 'usd', 'payment_intent': 'pi_test_9'}}}
    raw = json.dumps(payload).encode()
    timestamp = int(time.time())
    digest = hmac.new(b'whsec_test_key', str(timestamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    headers = {'Stripe-Signature': f't={timestamp},v1={digest}'}
    return app, TestClient(app.app), raw, headers


def test_webhook_marks_packet_ready_for_assistance(monkeypatch, tmp_path):
    app, client, raw, headers = _signed_webhook(monkeypatch, tmp_path, 'assist-webhook.sqlite3', 'assistance')
    assert client.post('/stripe/webhook', content=raw, headers=headers).status_code == 200
    with app.db() as conn:
        a = conn.execute('SELECT status, paid_at, delivered_at FROM applications WHERE id=?', ('app-9',)).fetchone()
        assert a['status'] == 'packet_ready'
        assert a['paid_at'] is not None
        assert a['delivered_at'] is None  # no manager delivery is claimed
    track = client.get('/track/opaque-assist-token-1234567890abcdef')
    assert track.status_code == 200
    assert 'Your application packet is ready' in track.text
    assert 'did not file this application' in track.text
    assert 'Fake Applicant' in track.text


def test_webhook_still_marks_delivery_pending_for_submission(monkeypatch, tmp_path):
    app, client, raw, headers = _signed_webhook(monkeypatch, tmp_path, 'assist-webhook-sub.sqlite3', 'submission')
    assert client.post('/stripe/webhook', content=raw, headers=headers).status_code == 200
    with app.db() as conn:
        a = conn.execute('SELECT status FROM applications WHERE id=?', ('app-9',)).fetchone()
        assert a['status'] == 'delivery_pending'
    track = client.get('/track/opaque-assist-token-1234567890abcdef')
    assert track.status_code == 200
    assert 'Your application packet is ready' not in track.text
    assert 'Fake Applicant' not in track.text


def test_service_type_defaults_to_submission(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-default.sqlite3')
    with app.db() as conn:
        conn.execute('''INSERT INTO applications(id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,
         employment,income,notes,consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,status)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
         ('app-old', 'opaque-old-token-1234567890abcdef', 'DEMO-ONLY', 'Old Row', 'old@example.invalid',
          '555-0110', '2027-01-01', 1, '', 'Other', '$4,000–$5,999', '', 1, 1, 1, 1, 'test', app.utc(), app.utc(),
          (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).isoformat(), 'pending'))
        a = conn.execute('SELECT service_type FROM applications WHERE id=?', ('app-old',)).fetchone()
        assert a['service_type'] == 'submission'


def test_assistance_ready_excludes_manager_agreements(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'assist-ready.sqlite3')
    monkeypatch.setattr(app, 'READY', True)
    with app.db() as conn:
        conn.execute('''UPDATE launch_config SET business_identity=1,business_support=1,policies_reviewed=1,
            stripe_connected=1,stripe_webhook_tested=1,refund_tested=1,privacy_security=1,launch_approved=1,
            manager_agreements=0 WHERE id=1''')
    assert app.assistance_ready() is True
    assert app.operational_ready() is False  # integrated path still needs it


def test_test_mode_allows_synthetic_checkout(monkeypatch, tmp_path):
    # _reload_app deletes STRIPE_SECRET_KEY, so set the env and reload here.
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'assist-testmode.sqlite3'))
    monkeypatch.setenv('OPERATING_LIVE', 'false')
    monkeypatch.setenv('TEST_MODE', 'true')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_mock')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_mock')
    import app
    importlib.reload(app)
    assert app.TEST_READY is True
    assert app.assistance_ready() is True
    assert app.READY is False  # live gate unchanged: no live key, no launch
    monkeypatch.setattr(app, 'checkout_session',
        lambda *a, **k: {'id': 'cs_testmode_1', 'url': 'https://checkout.stripe.com/testmode'})
    client = TestClient(app.app)
    token = _csrf(client, '/assist/DEMO-ONLY')
    data = _valid_assist_data()
    data['csrf_token'] = token
    r = client.post('/assist/DEMO-ONLY', data=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers['location'] == 'https://checkout.stripe.com/testmode'
    with app.db() as conn:
        a = conn.execute('SELECT status, stripe_session_id FROM applications').fetchone()
        assert a['status'] == 'payment_pending'
        assert a['stripe_session_id'] == 'cs_testmode_1'


def test_test_mode_off_blocks_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'assist-testmode-off.sqlite3'))
    monkeypatch.setenv('OPERATING_LIVE', 'false')
    monkeypatch.setenv('TEST_MODE', 'false')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_mock')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_mock')
    import app
    importlib.reload(app)
    assert app.TEST_READY is False
    assert app.assistance_ready() is False
