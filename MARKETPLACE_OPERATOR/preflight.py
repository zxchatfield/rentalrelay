#!/usr/bin/env python3
"""Inspect marketplace launch gates without accessing any user's accounts."""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CHECKS=('available_now_verified','accurate_price_verified','photo_use_rights_verified','permitted_rental_ad_identity_verified','application_fulfillment_verified','marketplace_publish_connector_proven','marketplace_inbox_send_receive_proven','live_checkout_webhook_proven','telegram_verified_paid_event_proven')
def evaluate(state:dict)->dict:
    missing=[k for k in CHECKS if state.get(k) is not True]
    return {'ready_for_fully_unattended_sales':not missing,'missing':missing}
if __name__=='__main__':
    state=json.loads((ROOT/'LIVE_READINESS.json').read_text())
    print(json.dumps({'project':evaluate(state.get('project',{})),'properties':{k:evaluate(v) for k,v in state.get('properties',{}).items()}},indent=2))
