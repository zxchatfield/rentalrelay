from pathlib import Path
import json
from muse_queue import queue_from_db

BASE = Path(__file__).resolve().parent.parent

def test_no_blanket_manager_written_requirement_in_task_queue():
    queue = (BASE / 'muse_queue.py').read_text()
    assert 'request_manager_authorization' not in queue
    assert 'verify_listing_rights_and_fulfillment_path' in queue

def test_research_allowed_even_though_publication_held():
    gates = json.loads((BASE / 'DETROIT_INVENTORY' / 'HARD_GATES.json').read_text())
    assert gates['blanket_manager_written_signature_required'] is False
    assert gates['research_testing_and_internal_implementation_allowed'] is True
    assert gates['publish_allowed'] is False
    assert gates['live_payment_allowed'] is False
