import datetime as dt
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from facebook_page_scheduler import validated_posts, run_once


def create_launch_flags(conn,approved=True):
    columns=('business_identity','business_support','policies_reviewed','stripe_connected',
             'stripe_webhook_tested','refund_tested','manager_agreements','privacy_security','launch_approved')
    conn.execute('CREATE TABLE launch_config (id INTEGER PRIMARY KEY,'+
                 ','.join(f'{key} INTEGER' for key in columns)+')')
    values=[1]+[int(approved)]*len(columns)
    conn.execute('INSERT INTO launch_config VALUES ('+','.join('?'*len(values))+')',values)

def test_ready_listing_generates_page_post_only(tmp_path,monkeypatch):
    db=tmp_path/'db.sqlite'; conn=sqlite3.connect(db)
    conn.execute('''CREATE TABLE properties (id TEXT,is_demo INTEGER,public_active INTEGER,verified_at TEXT,advert_permission INTEGER,
        intake_permission INTEGER,photo_rights INTEGER,secure_delivery_verified INTEGER,manager_name TEXT,manager_email TEXT,
        city TEXT,beds REAL,baths REAL,rent_cents INTEGER,manager_fee TEXT)''')
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    data=('A',0,1,now,1,1,1,1,'Manager','manager@example.org','Monroe',2,1,120000,'$0')
    conn.execute('INSERT INTO properties VALUES('+','.join(['?']*len(data))+')',data)
    create_launch_flags(conn)
    conn.commit();conn.close()
    results=validated_posts(db,'https://rentalrelay.example')
    assert len(results)==1 and 'optional independent' in results[0][1]
    monkeypatch.setenv('OPERATING_LIVE','true')
    called=[]
    def pub(page,token,caption):called.append(caption);return '123_456'
    args=(db,tmp_path/'state.db','https://rentalrelay.example','123','fake',True,pub)
    assert run_once(*args)['published']==[('A','123_456')]
    assert run_once(*args)['published']==[]
    assert len(called)==1

def test_does_not_post_unapproved(tmp_path):
    db=tmp_path/'db.sqlite';conn=sqlite3.connect(db)
    conn.execute('''CREATE TABLE properties (id TEXT,is_demo INTEGER,public_active INTEGER,verified_at TEXT,advert_permission INTEGER,
      intake_permission INTEGER,photo_rights INTEGER,secure_delivery_verified INTEGER,manager_name TEXT,manager_email TEXT,
      city TEXT,beds REAL,baths REAL,rent_cents INTEGER,manager_fee TEXT)''')
    x=('B',0,1,dt.datetime.now(dt.timezone.utc).isoformat(),0,1,1,1,'M','m@e.org','Monroe',1,1,90000,'0')
    conn.execute('INSERT INTO properties VALUES('+','.join(['?']*len(x))+')',x);create_launch_flags(conn);conn.commit();conn.close()
    assert validated_posts(db,'https://example.com')==[]


def test_page_publisher_blocked_until_business_launch_flags_approved(tmp_path):
    import sqlite3, datetime as dt
    from facebook_page_scheduler import validated_posts
    db=tmp_path/'db.sqlite';conn=sqlite3.connect(db)
    conn.execute('''CREATE TABLE properties (id TEXT,is_demo INTEGER,public_active INTEGER,verified_at TEXT,advert_permission INTEGER,
        intake_permission INTEGER,photo_rights INTEGER,secure_delivery_verified INTEGER,manager_name TEXT,manager_email TEXT,
        city TEXT,beds REAL,baths REAL,rent_cents INTEGER,manager_fee TEXT)''')
    create_launch_flags(conn,approved=False)
    x=('A',0,1,dt.datetime.now(dt.timezone.utc).isoformat(),1,1,1,1,'Manager','m@e.org','Monroe',2,1,130000,'$0')
    conn.execute('INSERT INTO properties VALUES ('+','.join('?'*len(x))+')',x)
    conn.commit();conn.close()
    assert validated_posts(db,'https://rentalrelay.example') == []
