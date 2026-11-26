#!/usr/bin/env python3
"""Read-only, privacy-minimizing daily task queue for Muse from RentalRelay's SQLite DB.
No credentials, no applicant PII, no sending or posting; safe to use in pilot.
Usage: DATABASE_PATH=/path/to/rentalrelay.sqlite3 python muse_queue.py
"""
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

import database

DATABASE = Path(os.environ.get("DATABASE_PATH", Path(__file__).resolve().parent / "rentalrelay.sqlite3"))


def queue_from_db(path=DATABASE):
    if not database.is_remote() and not Path(path).is_file():
        return {"status": "not_initialized", "tasks": [{"type": "setup", "reason": "Start RentalRelay locally in demo mode to create the database"}]}
    db = database.connect(str(path))
    db.row_factory = sqlite3.Row
    now = dt.datetime.now(dt.timezone.utc)
    tasks = []
    try:
        props = db.execute("SELECT id,title,is_demo,public_active,verified_at,advert_permission,intake_permission,photo_rights,secure_delivery_verified FROM properties").fetchall()
        for p in props:
            if p["is_demo"]:
                continue
            ready = all(p[k] for k in ("advert_permission", "intake_permission", "photo_rights", "secure_delivery_verified"))
            if p["public_active"] and not ready:
                tasks.append({"type": "halt_listing", "priority": "urgent", "property_id": p["id"], "reason": "Published but missing mandatory authorization flag"})
            if p["public_active"]:
                verified = None
                try:
                    verified = dt.datetime.fromisoformat(p["verified_at"])
                    if verified.tzinfo is None:
                        verified = verified.replace(tzinfo=dt.timezone.utc)
                except (TypeError, ValueError):
                    pass
                if verified is None or now - verified > dt.timedelta(hours=48):
                    tasks.append({"type": "reverify_or_expire", "priority": "high", "property_id": p["id"], "reason": "Availability verification missing/expired after 48 hours"})
            elif not ready:
                tasks.append({"type": "verify_listing_rights_and_fulfillment_path", "priority": "normal", "property_id": p["id"], "reason": "Research lawful marketing assets and select an actual fulfillment path: renter-authorized application assistance or verified receiving-party intake. A manager's signed permission is NOT a blanket prerequisite for research or software work."})
        apps = db.execute("SELECT id,status,created_at,paid_at FROM applications WHERE status NOT IN ('delivered','refunded')").fetchall()
        for a in apps:
            if a["status"] in ("paid", "delivery_pending"):
                tasks.append({"type": "verify_secure_manager_delivery", "priority": "urgent", "application_id": a["id"], "reason": "Stripe-paid application must be delivered to manager with confirmed receipt"})
            elif a["status"] == "payment_pending":
                tasks.append({"type": "reconcile_checkout", "priority": "normal", "application_id": a["id"], "reason": "Reconcile with Stripe before any paid claim or fulfillment"})
        cfg = db.execute("SELECT * FROM launch_config WHERE id=1").fetchone()
        if cfg and not cfg["launch_approved"]:
            tasks.append({"type": "launch_not_approved", "priority": "info", "reason": "Payments must remain disabled until owner verifies all launch gates"})
        return {"status": "ok", "generated_at_utc": now.isoformat(), "task_count": len(tasks), "tasks": tasks}
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(queue_from_db(), indent=2))
