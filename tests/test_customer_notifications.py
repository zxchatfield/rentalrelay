import datetime as dt
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from customer_notifications import run_once,valid_address

def test_customer_email_sends_only_once_after_verified_payment_and_manager_receipt(tmp_path,monkeypatch):
    monkeypatch.setenv('OPERATING_LIVE','true')
    database=tmp_path/'app.db'
    state=tmp_path/'state.db'
    now=dt.datetime.now(dt.timezone.utc)
    with sqlite3.connect(database) as con:
        con.executescript('''CREATE TABLE applications(id TEXT,email TEXT,tracking_token TEXT,status TEXT,paid_at TEXT,delivered_at TEXT,refunded_at TEXT);''')
        con.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?)',
          ('abc','sample@example.org','opaque-tracking-token','delivered',now.isoformat(),now.isoformat(),None))
    sent=[]
    sender=lambda to,sub,body: sent.append((to,sub,body))
    assert run_once(database,state,'https://owned.example.org',True,sender)['sent']==2
    assert run_once(database,state,'https://owned.example.org',True,sender)['sent']==0
    assert len(sent)==2
    assert all('opaque-tracking-token' in message[2] for message in sent)
    assert not valid_address('attacker\nBcc: somebody@example.org')

def test_customer_email_is_off_in_demo_mode(tmp_path,monkeypatch):
    monkeypatch.setenv('OPERATING_LIVE','false')
    assert run_once(tmp_path/'missing.db',tmp_path/'state.db','https://owned.example.org',True)['disabled']
