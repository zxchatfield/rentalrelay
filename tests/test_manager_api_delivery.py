import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manager_api_delivery import safe_endpoint,run_once

def test_https_required():
    assert safe_endpoint('https://manager.example.net/submit')
    assert not safe_endpoint('http://manager.example.net/submit')
    assert not safe_endpoint('https://localhost:7000/foo')
    assert not safe_endpoint('https://user:password@manager.example.net/submit')
    assert not safe_endpoint('https://169.254.169.254/latest/meta-data')
    assert not safe_endpoint('https://10.0.0.15/admin')
    assert not safe_endpoint('https://internal.local/api')

def test_delivery_disabled_by_default(tmp_path):
    r=run_once(tmp_path/'never-created.db',{},False)
    assert r['processed']==[]

def test_acknowledged_handoff_is_idempotent(tmp_path,monkeypatch):
    import sqlite3,datetime as dt
    from manager_api_delivery import run_once
    monkeypatch.setenv('OPERATING_LIVE','true')
    db=tmp_path/'app.sqlite'
    con=sqlite3.connect(db)
    con.executescript('''CREATE TABLE launch_config(id INTEGER PRIMARY KEY,business_identity INTEGER,business_support INTEGER,policies_reviewed INTEGER,
      stripe_connected INTEGER,stripe_webhook_tested INTEGER,refund_tested INTEGER,manager_agreements INTEGER,
      privacy_security INTEGER,launch_approved INTEGER);
      INSERT INTO launch_config VALUES (1,1,1,1,1,1,1,1,1,1);
      CREATE TABLE properties(id TEXT, title TEXT,manager_email TEXT,manager_name TEXT,intake_permission INTEGER,advert_permission INTEGER,
      photo_rights INTEGER,secure_delivery_verified INTEGER,public_active INTEGER,verified_at TEXT,is_demo INTEGER);
      CREATE TABLE applications(id TEXT,property_id TEXT,status TEXT,paid_at TEXT,full_name TEXT,email TEXT,phone TEXT,move_in TEXT,occupants INTEGER,
      pets TEXT,employment TEXT,income TEXT,notes TEXT,consent_delivery INTEGER,consent_service INTEGER,consent_disclosure INTEGER,
      consent_policies INTEGER,consent_version TEXT,consent_at TEXT,delivered_at TEXT);
    ''')
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute('INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?,?)',('p','Test','verified@example.org','M',1,1,1,1,1,now,0))
    con.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
       ('a','p','delivery_pending',now,'Person','p@example.org','555-0000','2027-01-01',1,'No','Employed','1000','',1,1,1,1,'v1',now,None))
    con.commit();con.close()
    calls=[]
    def manager_api(url,secret,payload):
        assert payload['consent']['delivery']
        calls.append(payload['application_id'])
        return 'receipt-123'
    mapping={'verified@example.org':{'url':'https://manager.example.org/api/receive','secret':'x'*32}}
    assert run_once(db,mapping,True,manager_api)['processed']==[{'application_id':'a','receipt_id':'receipt-123'}]
    assert run_once(db,mapping,True,manager_api)['processed']==[]
    assert calls==['a']
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT status FROM applications WHERE id='a'").fetchone()[0]=='delivered'


def test_failed_receiver_holds_property_until_accepted(tmp_path,monkeypatch):
    import sqlite3,datetime as dt
    monkeypatch.setenv('OPERATING_LIVE','true')
    db=tmp_path/'db.sqlite'
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(db) as c:
        c.executescript('''CREATE TABLE launch_config(id INTEGER PRIMARY KEY,business_identity INTEGER,business_support INTEGER,policies_reviewed INTEGER,
          stripe_connected INTEGER,stripe_webhook_tested INTEGER,refund_tested INTEGER,manager_agreements INTEGER,
          privacy_security INTEGER,launch_approved INTEGER);
          INSERT INTO launch_config VALUES (1,1,1,1,1,1,1,1,1,1);
          CREATE TABLE properties(id TEXT, title TEXT,manager_email TEXT,manager_name TEXT,intake_permission INTEGER,advert_permission INTEGER,
          photo_rights INTEGER,secure_delivery_verified INTEGER,public_active INTEGER,verified_at TEXT,is_demo INTEGER);
          CREATE TABLE applications(id TEXT,property_id TEXT,status TEXT,paid_at TEXT,full_name TEXT,email TEXT,phone TEXT,move_in TEXT,occupants INTEGER,
          pets TEXT,employment TEXT,income TEXT,notes TEXT,consent_delivery INTEGER,consent_service INTEGER,consent_disclosure INTEGER,
          consent_policies INTEGER,consent_version TEXT,consent_at TEXT,delivered_at TEXT);''')
        c.execute('INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?,?)',('p','Test','receiver@example.org','Manager',1,1,1,1,1,now,0))
        c.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('app','p','delivery_pending',now,'Test Person','person@example.org','555-0000','2027-03-01',1,'No','Employed','some','',1,1,1,1,'v1',now,None))
    mapping={'receiver@example.org':{'url':'https://receiver.example.org/intake','secret':'x'*32}}
    def failing(url,secret,payload):raise RuntimeError('Receiver offline')
    result=run_once(db,mapping,True,failing)
    assert result['failed_or_not_configured']==1
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM delivery_holds').fetchone()[0]==1
        assert c.execute('SELECT status FROM applications').fetchone()[0]=='delivery_pending'
    result=run_once(db,mapping,True,lambda u,s,p:'approved-receipt')
    assert result['processed'][0]['receipt_id']=='approved-receipt'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM delivery_holds').fetchone()[0]==0
        assert c.execute('SELECT status FROM applications').fetchone()[0]=='delivered'
