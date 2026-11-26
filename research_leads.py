"""Internal-only rental discovery records and review labels.

Source data is research, NOT clearance to publish or collect payments.
Import is idempotent and never converts a lead into a public property.
"""
from __future__ import annotations
from pathlib import Path
import json

REVIEW_STATUSES = ('unreviewed', 'rechecking', 'not_current', 'candidate_for_rights_review', 'discarded')
DATA_FILE = Path(__file__).parent / 'DETROIT_INVENTORY' / 'PROPERTY_RECORDS_FULL.json'


def candidate_records():
    data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
    if data.get('discovery_only') is not True:
        raise ValueError('Research records must be explicitly discovery-only')
    results=[]
    for item in data['records']:
        if item.get('facebook_ready') is not False or item.get('stripe_payment_ready') is not False:
            raise ValueError('Incoming research record misclassified as publishable')
        if 'Monroe' in (item.get('city') or ''):
            continue
        results.append({
            'id': item['id'], 'priority': int(item['priority']),
            'address': item['address'], 'city': item['city'],
            'rent_usd': float(item['rent_usd']), 'beds': float(item['beds']),
            'baths': str(item.get('baths', '')), 'sqft': str(item.get('sqft', '')),
            'source_url': item['source'], 'photo_gallery_url': item['photo_gallery_url'],
            'selling_point': item['click_hook'],
            'issues_json': json.dumps(item.get('material_caveats', [])),
            'photo_count': int(item.get('photo_count') or 0),
            'last_seen': item.get('last_seen', ''),
            'application_method': item.get('application', 'Unknown'),
            'photo_rights_status': item.get('photo_rights_status', 'NOT_VERIFIED'),
            'application_acceptance_status': item.get('application_acceptance_status', 'NOT_VERIFIED'),
            'price_status': item.get('price_status', 'NOT_VERIFIED'),
        })
    return results


def seed_research_leads(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS research_leads (
        id TEXT PRIMARY KEY, priority INTEGER NOT NULL, address TEXT NOT NULL,
        city TEXT NOT NULL, rent_usd REAL NOT NULL, beds REAL NOT NULL,
        baths TEXT, sqft TEXT, source_url TEXT NOT NULL, photo_gallery_url TEXT NOT NULL,
        selling_point TEXT NOT NULL, issues_json TEXT NOT NULL, photo_count INTEGER NOT NULL,
        last_seen TEXT, application_method TEXT, photo_rights_status TEXT,
        application_acceptance_status TEXT, price_status TEXT,
        review_status TEXT NOT NULL DEFAULT 'unreviewed', operator_notes TEXT NOT NULL DEFAULT '',
        status_updated_at TEXT
    )''')
    for x in candidate_records():
        keys=list(x)
        conn.execute(f"INSERT OR IGNORE INTO research_leads ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                     tuple(x[k] for k in keys))
