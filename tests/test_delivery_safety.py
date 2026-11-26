import datetime as dt
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def setup_real_property(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'safe.sqlite'))
    monkeypatch.setenv('AUTO_MANAGER_DELIVERY_ENABLED','true')
    monkeypatch.setenv('MANAGER_API_DESTINATIONS_JSON',
                       '{"manager@example.org":{"url":"https://manager.example.org/receive","secret":"0123456789abcdef0123456789abcdef"}}')
    import app
    importlib.reload(app)
    now=dt.datetime.now(dt.timezone.utc)
    with app.db() as c:
        c.execute('''INSERT INTO properties (id,title,city,address,rent_cents,deposit_cents,manager_name,manager_email,manager_fee,source_url,created_at,
                   public_active,advert_permission,intake_permission,photo_rights,secure_delivery_verified,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  ('p','Verified','Monroe','Real location',125000,125000,'Participating manager','manager@example.org',
                   'None','https://manager.example.org',now.isoformat(),1,1,1,1,1,now.isoformat()))
        c.execute('INSERT INTO delivery_worker_health(id,checked_at) VALUES (1,?)',(now.isoformat(),))
    return app, now


def test_no_new_fees_if_worker_stops_or_receiver_is_unavailable(monkeypatch, tmp_path):
    app, now=setup_real_property(monkeypatch,tmp_path)
    p=app.one('SELECT * FROM properties WHERE id=?',('p',))
    assert app.auto_delivery_ready(p)
    with app.db() as conn:
        conn.execute('UPDATE delivery_worker_health SET checked_at=? WHERE id=1',
                     ((now-dt.timedelta(minutes=20)).isoformat(),))
    assert not app.auto_delivery_ready(p)
    with app.db() as conn:
        conn.execute('UPDATE delivery_worker_health SET checked_at=? WHERE id=1',(now.isoformat(),))
        conn.execute('INSERT INTO delivery_holds(property_id,held_at) VALUES (?,?)',('p',now.isoformat()))
    assert not app.auto_delivery_ready(p)
    with app.db() as conn:
        conn.execute('DELETE FROM delivery_holds WHERE property_id=?',('p',))
    assert app.auto_delivery_ready(p)
    monkeypatch.setenv('MANAGER_API_DESTINATIONS_JSON','{}')
    assert not app.auto_delivery_ready(p)


def test_future_availability_timestamp_does_not_count_as_verified(monkeypatch, tmp_path):
    app, now=setup_real_property(monkeypatch,tmp_path)
    with app.db() as conn:
        conn.execute('UPDATE properties SET verified_at=? WHERE id=?',
                     ((now+dt.timedelta(days=10)).isoformat(),'p'))
    p=app.one('SELECT * FROM properties WHERE id=?',('p',))
    assert not app.authorized(p)


def test_sensitive_admin_diagnostics_requires_login(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    app,_=setup_real_property(monkeypatch,tmp_path)
    client=TestClient(app.app)
    assert client.get('/healthz').status_code==200
    assert client.get('/admin/diagnostics').status_code==403


def test_entire_paid_application_handoff_with_mocked_providers(monkeypatch,tmp_path):
    """Full web checkout -> signed Stripe -> manager acceptance, no real charges/data."""
    import json, hashlib, hmac, re, time
    from fastapi.testclient import TestClient
    from manager_api_delivery import run_once
    app, now=setup_real_property(monkeypatch,tmp_path)
    # Simulate completed onboarding in a test database. Payment provider is mocked.
    monkeypatch.setattr(app,'READY',True)
    monkeypatch.setattr(app,'DEMO',False)
    monkeypatch.setattr(app,'STRIPE_WEBHOOK_SECRET','whsec_test_secret')
    with app.db() as c:
        c.execute('''UPDATE launch_config SET business_identity=1,business_support=1,policies_reviewed=1,
          stripe_connected=1,stripe_webhook_tested=1,refund_tested=1,manager_agreements=1,
          privacy_security=1,launch_approved=1 WHERE id=1''')
    monkeypatch.setattr(app,'checkout_session',lambda aid,token,title,cents,base,applicant_email: {'id':'cs_test_mocked','url':'https://checkout.stripe.com/mock'})
    client=TestClient(app.app)
    page=client.get('/apply/p')
    assert page.status_code==200
    assert 'disabled' not in page.text
    csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    form={'csrf_token':csrf_token,'full_name':'Fictitious Applicant','email':'fiction@example.invalid',
       'phone':'555-0123','move_in':(dt.date.today()+dt.timedelta(days=15)).isoformat(),
       'occupants':'2','pets':'No pets','employment':'Employed','income':'$4,000–$5,999','notes':'test only',
       'consent_delivery':'yes','consent_service':'yes','consent_disclosure':'yes','consent_policies':'yes'}
    result=client.post('/apply/p',data=form,follow_redirects=False)
    assert result.status_code==303
    assert result.headers['location']=='https://checkout.stripe.com/mock'
    with app.db() as conn:
        a=conn.execute('SELECT * FROM applications').fetchone()
        assert a['status']=='payment_pending'
    event={'id':'evt_full_mock','type':'checkout.session.completed','data':{'object':{
        'client_reference_id':a['id'],'id':'cs_test_mocked','payment_status':'paid',
        'amount_total':2499,'currency':'usd','payment_intent':'pi_test_mock'}}}
    body=json.dumps(event).encode()
    ts=int(time.time())
    signature=hmac.new(b'whsec_test_secret',str(ts).encode()+b'.'+body,hashlib.sha256).hexdigest()
    result=client.post('/stripe/webhook',content=body,headers={'Stripe-Signature':f't={ts},v1={signature}'})
    assert result.status_code==200
    monkeypatch.setenv('OPERATING_LIVE','true')
    mapping={'manager@example.org':{'url':'https://manager.example.org/receive','secret':'0'*32}}
    sent=[]
    result=run_once(tmp_path/'safe.sqlite',mapping,True,
                    lambda url,secret,payload:(sent.append(payload['application_id']) or 'receipt-final'))
    assert result['processed']==[{'application_id':a['id'],'receipt_id':'receipt-final'}]
    assert sent==[a['id']]
    assert run_once(tmp_path/'safe.sqlite',mapping,True,lambda *args: (_ for _ in ()).throw(AssertionError('duplicate')))['processed']==[]
    with app.db() as conn:
        final=conn.execute('SELECT status,delivered_at FROM applications').fetchone()
        assert final['status']=='delivered' and final['delivered_at']
