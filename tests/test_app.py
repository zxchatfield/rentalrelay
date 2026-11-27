import datetime as dt
import hashlib
import hmac
import importlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import stripe_client
import password


def test_password_hash():
    token=password.encode_password('some-long-passphrase')
    assert password.verify_password('some-long-passphrase',token)
    assert not password.verify_password('wrong',token)


def test_webhook_signature():
    body=json.dumps({'id':'evt_abc','type':'test'}).encode()
    secret='whsec_abc'
    clock=int(time.time())
    digest=hmac.new(secret.encode(),str(clock).encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert stripe_client.verify_stripe_signature(body,f't={clock},v1={digest}',secret)['id']=='evt_abc'
    with pytest.raises(stripe_client.StripeError):
        stripe_client.verify_stripe_signature(body,f't={clock},v1=incorrect',secret)
    with pytest.raises(stripe_client.StripeError):
        stripe_client.verify_stripe_signature(body,f't={clock-999},v1={digest}',secret)


def test_checkout_server_price_is_stripe_hosted(monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_test_mock')
    def fake_post(url,headers,data,timeout):
        class Resp:
            ok=True
            def json(self):return {'id':'cs_test_123','url':'https://checkout.stripe.com/test'}
        assert data['line_items[0][price_data][unit_amount]']=='2499'
        assert data['payment_method_types[0]']=='card'
        assert 'full_name' not in str(data)
        assert data['success_url'].endswith('/track/opaque-token?checkout=returned')
        return Resp()
    monkeypatch.setattr(stripe_client.requests,'post',fake_post)
    sess=stripe_client.checkout_session('id','opaque-token','Example rental',2499,'https://test.example.com')
    assert sess['id']=='cs_test_123'


def test_demo_site_and_payment_lock(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.sqlite3'))
    monkeypatch.setenv('OPERATING_LIVE','false')
    monkeypatch.delenv('STRIPE_SECRET_KEY',raising=False)
    import app
    importlib.reload(app)
    client=TestClient(app.app)
    for path in ('/', '/rentals','/rentals/DEMO-ONLY','/apply/DEMO-ONLY','/faq','/terms','/privacy','/admin/login'):
        res=client.get(path)
        assert res.status_code==200,(path,res.text[:100])
        assert 'demo' in res.text.lower()
    form=client.get('/apply/DEMO-ONLY').text
    assert 'disabled' in form
    assert 'consent_delivery' in form
    # No personal information can be stored in demo mode.
    r=client.post('/apply/DEMO-ONLY',data={'full_name':'John Demo','email':'john@test.example'})
    assert r.status_code==403
    with app.db() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM applications').fetchone()['n']==0
    assert client.post('/stripe/webhook',content=b'{}').status_code==400
    assert client.get('/admin').status_code==403


def test_listing_and_validation_rules(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'properties.sqlite3'))
    import app
    importlib.reload(app)
    with app.db() as conn:
        conn.execute('''INSERT INTO properties(id,title,city,address,rent_cents,deposit_cents,manager_name,manager_email,manager_fee,source_url,created_at,public_active)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',('test-real','Real listing','City','123 A',150000,100000,'Company','manager@company.test','0','https://example.com',app.utc(),1))
    assert all(p['id']!='test-real' for p in app.live_listings())
    d={'full_name':'Amy Test','email':'amy@example.com','phone':'(555) 555-0123','move_in':(dt.date.today()+dt.timedelta(days=15)).isoformat(),
       'occupants':1,'pets':'','employment':'Employed','income':'$4,000–$5,999','notes':'',
       'consent_delivery':True,'consent_service':True,'consent_disclosure':True,'consent_policies':True}
    assert not app.validate_application(d)
    d['consent_service']=False
    assert app.validate_application(d)


def test_admin_property_creation_and_publishing_guard(monkeypatch,tmp_path):
    import re
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'admin.sqlite3'))
    monkeypatch.setenv('SESSION_SECRET','x'*48)
    monkeypatch.setenv('ADMIN_USERNAME','owner')
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',password.encode_password('really-long-admin-pass'))
    monkeypatch.setenv('OPERATING_LIVE','false')
    import app
    importlib.reload(app)
    client=TestClient(app.app)
    def csrf(url):
        page=client.get(url)
        return re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    token=csrf('/admin/login')
    r=client.post('/admin/login',data={'username':'owner','password':'really-long-admin-pass','csrf_token':token},follow_redirects=True)
    assert r.status_code==200 and 'RentalRelay operations' in r.text
    token=csrf('/admin')
    new={'csrf_token':token,'title':'123 Real St','city':'Monroe','address':'123 Real St, Monroe, MI',
         'rent_dollars':'1250','deposit_dollars':'1250','beds':'2','baths':'1',
         'manager_name':'Example Manager','manager_email':'real@example.org','manager_fee':'$20',
         'source_url':'https://example.org/listing'}
    assert client.post('/admin/properties',data=new,follow_redirects=False).status_code==303
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE title=?',('123 Real St',)).fetchone()
        assert p and p['public_active']==0
    assert client.post(f'/admin/properties/{p["id"]}/verify',data={'csrf_token':token,'public_active':'yes'}).status_code==400
    assert client.post(f'/admin/properties/{p["id"]}/verify',data={'csrf_token':token,'public_active':'yes',
        'advert_permission':'yes','intake_permission':'yes','photo_rights':'yes','secure_delivery_verified':'yes'},follow_redirects=False).status_code==303
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE id=?',(p['id'],)).fetchone()
        assert p['public_active']==1
    # Real listing may be authorized but demo mode never accepts a payment.
    assert client.get('/rentals/'+p['id']).status_code==404


def test_admin_property_edit_updates_manager_fee(monkeypatch,tmp_path):
    import re
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'edit.sqlite3'))
    monkeypatch.setenv('SESSION_SECRET','x'*48)
    monkeypatch.setenv('ADMIN_USERNAME','owner')
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',password.encode_password('really-long-admin-pass'))
    monkeypatch.setenv('OPERATING_LIVE','false')
    import app
    importlib.reload(app)
    client=TestClient(app.app)
    def csrf(url):
        page=client.get(url)
        return re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    token=csrf('/admin/login')
    r=client.post('/admin/login',data={'username':'owner','password':'really-long-admin-pass','csrf_token':token},follow_redirects=True)
    assert r.status_code==200 and 'RentalRelay operations' in r.text
    token=csrf('/admin')
    new={'csrf_token':token,'title':'456 Edit St','city':'Detroit','address':'456 Edit St, Detroit, MI',
         'rent_dollars':'850','deposit_dollars':'1275','beds':'2','baths':'1',
         'manager_name':'Clara Robinson PA','manager_email':'jose@clararobinsonpa.com','manager_fee':'Ask manager',
         'source_url':'https://example.org/listing'}
    assert client.post('/admin/properties',data=new,follow_redirects=False).status_code==303
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE title=?',('456 Edit St',)).fetchone()
        assert p and p['manager_fee']=='Ask manager'
    # A confirmed fee replaces the placeholder; nothing else changes.
    assert client.post(f'/admin/properties/{p["id"]}/edit',
                       data={'csrf_token':token,'manager_fee':'$50 per adult, non-refundable'},
                       follow_redirects=False).status_code==303
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE id=?',(p['id'],)).fetchone()
        assert p['manager_fee']=='$50 per adult, non-refundable'
        assert p['rent_cents']==85000 and p['manager_name']=='Clara Robinson PA'
    # Square footage can be set the same way; an unconfirmed sqft stays unset.
    assert client.post(f'/admin/properties/{p["id"]}/edit',
                       data={'csrf_token':token,'sqft':'750'},
                       follow_redirects=False).status_code==303
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE id=?',(p['id'],)).fetchone()
        assert p['sqft']==750
    # Bad email is rejected and the fee is untouched.
    assert client.post(f'/admin/properties/{p["id"]}/edit',
                       data={'csrf_token':token,'manager_email':'not-an-email'},
                       follow_redirects=False).status_code==400
    with app.db() as conn:
        p=conn.execute('SELECT * FROM properties WHERE id=?',(p['id'],)).fetchone()
        assert p['manager_fee']=='$50 per adult, non-refundable'
    # An empty edit changes nothing, and demo properties cannot be edited.
    assert client.post(f'/admin/properties/{p["id"]}/edit',
                       data={'csrf_token':token},follow_redirects=False).status_code==303
    assert client.post('/admin/properties/DEMO-ONLY/edit',
                       data={'csrf_token':token,'manager_fee':'$1'},
                       follow_redirects=False).status_code==404


def test_paid_status_only_after_signed_webhook(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'webhook.sqlite3'))
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','whsec_test_key')
    import app
    importlib.reload(app)
    with app.db() as conn:
        conn.execute('''INSERT INTO applications(id,tracking_token,property_id,full_name,email,phone,move_in,occupants,pets,
         employment,income,notes,consent_delivery,consent_service,consent_disclosure,consent_policies,consent_version,consent_at,created_at,retention_until,
         stripe_session_id,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
         ('app-1','opaque-tracking-token-123456789','DEMO-ONLY','Fake Applicant','fake@example.invalid','555-0110','2027-01-01',1,
          '','Other','$4,000–$5,999','',1,1,1,1,'test',app.utc(),app.utc(),(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=90)).isoformat(),
          'cs_test_123','payment_pending'))
    payload={'id':'evt_test_1','type':'checkout.session.completed','data':{'object':{
        'client_reference_id':'app-1','id':'cs_test_123','payment_status':'paid','amount_total':2499,'currency':'usd','payment_intent':'pi_test_123'}}}
    raw=json.dumps(payload).encode()
    timestamp=int(time.time())
    digest=hmac.new(b'whsec_test_key',str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    headers={'Stripe-Signature':f't={timestamp},v1={digest}'}
    client=TestClient(app.app)
    assert client.post('/stripe/webhook',content=raw,headers=headers).status_code==200
    assert client.post('/stripe/webhook',content=raw,headers=headers).json()['duplicate'] is True
    with app.db() as conn:
        a=conn.execute('SELECT status FROM applications WHERE id=?',('app-1',)).fetchone()
        assert a['status']=='delivery_pending'
    raw_bad=raw.replace(b'2499',b'2500')
    assert client.post('/stripe/webhook',content=raw_bad,headers=headers).status_code==400
