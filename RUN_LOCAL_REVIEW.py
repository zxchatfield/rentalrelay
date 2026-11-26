#!/usr/bin/env python3
"""One-command localhost demo (no payments or external network posting).

Start: python RUN_LOCAL_REVIEW.py
If dependencies are missing: pip install -r requirements.txt

Passwords are generated per run and printed locally to the operator terminal.
"""
import os
import secrets
from pathlib import Path
from password import encode_password

ROOT=Path(__file__).resolve().parent
ADMIN_PASSWORD=secrets.token_urlsafe(16)
os.environ.update({
    'OPERATING_LIVE': 'false',
    'SESSION_SECRET': secrets.token_urlsafe(48),
    'ADMIN_USERNAME': 'operator',
    'ADMIN_PASSWORD_HASH': encode_password(ADMIN_PASSWORD),
    'DATABASE_PATH': str(ROOT/'review_demo.sqlite3'),
    'SESSION_HTTPS':'false',
})

if __name__=='__main__':
    import uvicorn
    print('\nRENTALRELAY PRIVATE REVIEW — DEMO ONLY', flush=True)
    print('Website: http://127.0.0.1:8000/', flush=True)
    print('Private Detroit leads: http://127.0.0.1:8000/admin/research', flush=True)
    print('Marketplace sales desk: http://127.0.0.1:8000/admin/inbox', flush=True)
    print('Application packet demo: http://127.0.0.1:8000/prep/demo', flush=True)
    print('Admin username: operator', flush=True)
    print(f'Admin password for this run: {ADMIN_PASSWORD}', flush=True)
    print('Never expose port 8000 to the internet. No real payments or customer applications.', flush=True)
    uvicorn.run('app:app', host='127.0.0.1', port=8000, access_log=False)
