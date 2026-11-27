import datetime as dt
import hashlib
import hmac
import importlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _reload_app(monkeypatch, tmp_path, name):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / name))
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test_key')
    import app
    importlib.reload(app)
    return app


def _insert_pending_app(app, app_id='app-1', session_id='cs_test_123', service_type='assistance'):
    with app.db() as conn:
        conn.execute('''INSERT INTO applications(id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,
         employment,income,notes,consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,
         stripe_session_id,status,service_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
         (app_id, 'opaque-tracking-token-123456789', 'DEMO-ONLY', 'Fake Applicant', 'fake@example.invalid',
          '555-0110', '2027-01-01', 1, '', 'Other', '$4,000–$5,999', '', 1, 1, 1, 1, 'test', app.utc(), app.utc(),
          (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).isoformat(),
          session_id, 'payment_pending', service_type))


def _signed_payload(app_id='app-1', session_id='cs_test_123', event_id='evt_test_1', amount=2499):
    payload = {'id': event_id, 'type': 'checkout.session.completed', 'data': {'object': {
        'client_reference_id': app_id, 'id': session_id, 'payment_status': 'paid',
        'amount_total': amount, 'currency': 'usd', 'payment_intent': 'pi_test_123'}}}
    raw = json.dumps(payload).encode()
    timestamp = int(time.time())
    digest = hmac.new(b'whsec_test_key', str(timestamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return raw, {'Stripe-Signature': f't={timestamp},v1={digest}'}


def _notification_row(app, app_id='app-1'):
    with app.db() as conn:
        return conn.execute(
            "SELECT * FROM telegram_notifications WHERE application_id=? AND kind='payment'",
            (app_id,)).fetchone()


def test_webhook_queues_and_sends_telegram_notification(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'notify-1.sqlite3')
    _insert_pending_app(app)
    sent = []
    monkeypatch.setattr(app, 'send_telegram', lambda token, chat_id, msg, timeout=15: sent.append(msg))
    client = TestClient(app.app)
    raw, headers = _signed_payload()
    assert client.post('/stripe/webhook', content=raw, headers=headers).status_code == 200
    with app.db() as conn:
        a = conn.execute("SELECT status FROM applications WHERE id=?", ('app-1',)).fetchone()
        assert a['status'] == 'packet_ready'
    row = _notification_row(app)
    assert row is not None
    assert row['status'] == 'sent'
    assert row['attempts'] == 1
    assert row['sent_at'] is not None
    assert len(sent) == 1
    assert sent[0].startswith('🧪 TEST PAYMENT\n💰 New payment: $24.99')
    assert '📍' in sent[0]
    assert 'Today: $24.99' in sent[0]
    assert 'This month: $24.99' in sent[0]
    assert 'app-1' not in sent[0]


def test_webhook_duplicate_event_sends_notification_once(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'notify-2.sqlite3')
    _insert_pending_app(app)
    sent = []
    monkeypatch.setattr(app, 'send_telegram', lambda token, chat_id, msg, timeout=15: sent.append(msg))
    client = TestClient(app.app)
    raw, headers = _signed_payload()
    assert client.post('/stripe/webhook', content=raw, headers=headers).status_code == 200
    assert client.post('/stripe/webhook', content=raw, headers=headers).json()['duplicate'] is True
    with app.db() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM telegram_notifications").fetchone()['n']
        assert n == 1
    assert len(sent) == 1


def test_failed_send_stays_pending_and_watchdog_retries(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'notify-3.sqlite3')
    _insert_pending_app(app)
    calls = {'n': 0}

    def flaky(token, chat_id, msg, timeout=15):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('telegram unreachable')
    monkeypatch.setattr(app, 'send_telegram', flaky)
    client = TestClient(app.app)
    raw, headers = _signed_payload()
    assert client.post('/stripe/webhook', content=raw, headers=headers).status_code == 200
    row = _notification_row(app)
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    assert 'telegram unreachable' in (row['last_error'] or '')
    # Watchdog retry now succeeds.
    app._retry_pending_notifications()
    row = _notification_row(app)
    assert row['status'] == 'sent'
    assert row['attempts'] == 2
    assert calls['n'] == 2


def test_pending_notification_survives_restart_and_retries(monkeypatch, tmp_path):
    app = _reload_app(monkeypatch, tmp_path, 'notify-4.sqlite3')
    _insert_pending_app(app)
    # Simulate a crash after the payment was queued but before the send:
    # mark the application paid and leave the notification pending, as the
    # webhook would have persisted it.
    with app.db() as conn:
        conn.execute("UPDATE applications SET status='packet_ready', paid_at=? WHERE id=?", (app.utc(), 'app-1'))
        conn.execute(
            "INSERT INTO telegram_notifications(id, application_id, kind, status, attempts, created_at) "
            "VALUES (?,?,?,?,0,?)", ('payment:app-1', 'app-1', 'payment', 'pending', app.utc()))
    # Restart: reload the module (fresh in-memory state, same database file).
    import app as app2
    importlib.reload(app2)
    sent = []
    monkeypatch.setattr(app2, 'send_telegram', lambda token, chat_id, msg, timeout=15: sent.append(msg))
    app2._retry_pending_notifications()
    assert len(sent) == 1
    assert 'TEST PAYMENT' in sent[0]
    with app2.db() as conn:
        row = conn.execute(
            "SELECT status FROM telegram_notifications WHERE application_id=?", ('app-1',)).fetchone()
        assert row['status'] == 'sent'
