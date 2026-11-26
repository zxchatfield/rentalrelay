"""The user can verify sales copy without any Meta connection or payment risk."""
from pathlib import Path
import importlib, os, re, sys
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from inquiry_desk import classify, draft_for_test
from password import encode_password


def test_classify_actual_common_inquiries():
    expected={
        'Is this still available?':'availability',
        'Can I see it first?':'tour',
        'Can I apply directly on their website?':'direct_application',
        'What is the application fee?':'fees',
        'Do you allow dogs?':'property_fact',
        'What is the deposit?':'property_fact',
        'Send me the application link':'apply_request',
        'Is this a scam?':'scam_or_verify',
        'Stop contacting me':'stop',
        'Are you the landlord?':'who_are_you',
    }
    for message,intent in expected.items():
        assert classify(message)[0]==intent, message


def test_real_research_draft_never_claims_availability_or_accepts_money():
    lead={'address':'18637 Forrer St','city':'Detroit','rent_usd':950}
    for question in ('Is it available?', 'I want to apply', 'Can I see it first?',
                     'Pets allowed?', 'How much to apply?', 'Send me the link'):
        result=draft_for_test(question,lead)
        assert result['can_send'] is False
        assert result['mode']=='unverified_research'
        assert result['requires_review'] is True
        assert 'https://example.invalid' not in result['text']
    r=draft_for_test('Is it available?',lead)
    assert '$950' in r['text'] and 'verifying' in r['text'].lower()


def test_fictional_demo_is_not_sendable_even_if_core_sales_engine_would_allow_link():
    for q in ('Is this still available?', 'I want to apply', 'Can I see it first?', 'Do you accept cats?'):
        result=draft_for_test(q,simulate_verified=True)
        assert not result['can_send']
        assert result['mode']=='fictional_example'
    assert '$24.99' in draft_for_test('I want to apply',simulate_verified=True)['text']


def test_inbox_requires_admin_csrf_and_does_not_store_messages(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'inbox.sqlite3'))
    monkeypatch.setenv('OPERATING_LIVE','false')
    monkeypatch.setenv('SESSION_SECRET','k'*48)
    monkeypatch.setenv('ADMIN_USERNAME','operator')
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',encode_password('testpassword123'))
    import app
    importlib.reload(app)
    client=TestClient(app.app)
    assert client.get('/admin/inbox').status_code==403
    token=lambda s: re.search(r'name="csrf_token" value="([^"]+)"',s).group(1)
    login=client.get('/admin/login')
    r=client.post('/admin/login',data={'csrf_token':token(login.text), 'username':'operator','password':'testpassword123'},follow_redirects=False)
    assert r.status_code==303
    page=client.get('/admin/inbox')
    assert page.status_code==200
    assert 'Fictional verified rental' in page.text
    assert client.post('/admin/inbox/draft',data={'csrf_token':'invalid','lead_id':'DT-001','customer_message':'Hi'}).status_code==403
    assert client.post('/admin/inbox/draft',data={'csrf_token':token(page.text),'lead_id':'unknown','customer_message':'Hi'}).status_code==404
    question='Is this a scam? <script>alert(1)</script>'
    result=client.post('/admin/inbox/draft',data={'csrf_token':token(page.text),'lead_id':'DT-001','customer_message':question})
    assert result.status_code==200
    assert 'independent application-assistance service' in result.text
    assert '<script>alert(1)</script>' not in result.text
    with app.db() as conn:
        assert conn.execute('select count(*) as n from applications').fetchone()['n']==0
        assert conn.execute('select count(*) as n from properties where is_demo=0').fetchone()['n']==0
    assert question not in (tmp_path/'inbox.sqlite3').read_bytes().decode('latin1')
