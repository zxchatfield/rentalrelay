"""Feature tests: usable internal research queue and renter packet preview."""
import datetime as dt
import importlib
import os
import re
from pathlib import Path
import sys

from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import password


def get_app(monkeypatch, tmp_path, admin=False):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'progress.sqlite3'))
    monkeypatch.setenv('OPERATING_LIVE','false')
    monkeypatch.setenv('SESSION_SECRET','r'*48)
    if admin:
        monkeypatch.setenv('ADMIN_USERNAME', 'owner')
        monkeypatch.setenv('ADMIN_PASSWORD_HASH', password.encode_password('strong-password-for-this-test'))
    import app
    importlib.reload(app)
    return app, TestClient(app.app)


def token(page):
    return re.search(r'name="csrf_token" value="([^"]+)"',page).group(1)


def test_research_inventory_private_and_separate_from_public(monkeypatch,tmp_path):
    app, client = get_app(monkeypatch,tmp_path,admin=True)
    assert client.get('/admin/research').status_code==403
    with app.db() as db:
        assert db.execute('SELECT count(*) as n FROM research_leads').fetchone()['n']==8
        assert db.execute('SELECT count(*) as n FROM properties WHERE is_demo=0').fetchone()['n']==0
    login_token=token(client.get('/admin/login').text)
    r=client.post('/admin/login', data={'csrf_token':login_token,'username':'owner',
                   'password':'strong-password-for-this-test'},follow_redirects=True)
    assert r.status_code==200
    research=client.get('/admin/research')
    assert research.status_code==200
    assert '18637 Forrer' in research.text
    assert 'NOT_GRANTED_OR_VERIFIED' in research.text
    assert 'Research only' in research.text
    assert 'Violetlawn' in research.text
    tok=token(research.text)
    assert client.post('/admin/research/DT-001/status',data={'csrf_token':'wrong',
           'review_status':'rechecking'}).status_code==403
    assert client.post('/admin/research/DT-001/status',data={'csrf_token':tok,
           'review_status':'promote_to_public'}).status_code==400
    res=client.post('/admin/research/DT-001/status',data={'csrf_token':tok,
           'review_status':'rechecking','operator_notes':'Price conflicting: must verify'},follow_redirects=False)
    assert res.status_code==303
    with app.db() as db:
        row=db.execute('SELECT * FROM research_leads WHERE id="DT-001"').fetchone()
        assert row['review_status']=='rechecking'
        assert 'Price conflicting' in row['operator_notes']
        assert db.execute('SELECT count(*) as n FROM properties WHERE is_demo=0').fetchone()['n']==0
    # Restart seeding must not erase the operator's saved research progress
    app.init_db()
    assert app.one('SELECT review_status FROM research_leads WHERE id=?',('DT-001',))['review_status']=='rechecking'


def test_prep_preview_no_charge_no_database_and_input_escaped(monkeypatch,tmp_path):
    app, client=get_app(monkeypatch,tmp_path)
    r=client.get('/prep/demo')
    assert r.status_code==200
    assert 'No payment' in r.text
    d={'csrf_token':token(r.text),'target_url':'https://rental.example.org/address',
       'full_name':'Alex <script>alert(1)</script> Sample','email':'alex@example.org',
       'phone':'555-555-0123','move_in':(dt.date.today()+dt.timedelta(days=20)).isoformat(),
       'occupants':'2','employment':'Student','income':'$2,000–$3,999','pets':'No pets',
       'notes':'Please send a copy','consent_packet':'yes'}
    r=client.post('/prep/demo', data=d)
    assert r.status_code==200
    assert 'DRAFT — NOT SUBMITTED' in r.text
    assert 'alex@example.org' in r.text
    assert '<script>alert(1)</script>' not in r.text
    assert '/static/print_packet.js' in r.text
    assert 'No payment was collected' in r.text
    with app.db() as db:
        assert db.execute('SELECT count(*) as n FROM applications').fetchone()['n']==0
    d['target_url']='javascript:alert(1)'
    r=client.post('/prep/demo', data=d)
    assert 'valid public listing link' in r.text
    assert r.status_code==200
    d['target_url']='https://rental.example.org/address'
    d['consent_packet']=''
    assert 'Confirm this is only a draft packet' in client.post('/prep/demo',data=d).text


def test_candidate_record_cannot_set_public_ready(monkeypatch,tmp_path):
    from research_leads import candidate_records
    leads=candidate_records()
    assert len(leads)==8
    assert not any('Monroe' in i['city'] for i in leads)
    assert all('facebook_ready' not in row for row in leads)
    assert all('photo_gallery_url' in row for row in leads)
