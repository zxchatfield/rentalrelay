import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rr_preflight',ROOT/'MARKETPLACE_OPERATOR/preflight.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
def test_launch_readiness_denies_unset_flags():
    result=mod.evaluate({})
    assert result['ready_for_fully_unattended_sales'] is False
    assert len(result['missing'])==9

def test_all_verified_flags_can_pass():
    assert mod.evaluate({k:True for k in mod.CHECKS})['ready_for_fully_unattended_sales'] is True

def test_four_leads_are_locked_not_photo_approved():
    data=json.loads((ROOT/'MARKETPLACE_OPERATOR/PRIORITY_4_PROPERTY_FACTS.json').read_text())
    assert len(data['candidates'])==4
    assert all(not p['facebook_ready'] and not p['stripe_payment_ready'] for p in data['candidates'])
    assert all(p['photo_rights_status']!='GRANTED' for p in data['candidates'])

def test_no_fake_photos_or_checkout_links_in_ad_drafts():
    s=(ROOT/'MARKETPLACE_OPERATOR/MARKETPLACE_AD_COPY.md').read_text().lower()
    assert 'not currently authorized to post' in s
    assert 'stripe.com/' not in s
    assert 'limited-time special' in s
    assert 'upper unit' in s


def test_compose_has_no_page_publisher_worker():
    c=(ROOT/'deployment/compose.yaml').read_text()
    assert '  page_publisher:' not in c
    assert 'RR_JOB: page' not in c
