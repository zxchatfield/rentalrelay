import json, unittest
from pathlib import Path

class DetroitInventoryTests(unittest.TestCase):
    def test_all_leads_held(self):
        data=json.loads((Path(__file__).parents[1]/'DETROIT_INVENTORY'/'PROPERTY_RECORDS_FULL.json').read_text())
        self.assertEqual(data['property_count'],8)
        ids=set()
        for p in data['records']:
            self.assertNotIn('Monroe', p['city'])
            self.assertNotIn(p['id'], ids); ids.add(p['id'])
            self.assertFalse(p['facebook_ready'])
            self.assertFalse(p['stripe_payment_ready'])
            self.assertFalse(p['outreach_sent'])
            self.assertFalse(p['third_party_submission_verified'])
            self.assertEqual(p['photo_rights_status'],'NOT_GRANTED_OR_VERIFIED')
            self.assertTrue(p['photo_gallery_url'].startswith('https://'))
            self.assertTrue(p['source'].startswith('https://'))
    def test_hard_gates(self):
        gates=json.loads((Path(__file__).parents[1]/'DETROIT_INVENTORY'/'HARD_GATES.json').read_text())
        for k in ('outreach_allowed','publish_allowed','live_payment_allowed','auto_reply_allowed'):
            self.assertFalse(gates[k])
        self.assertEqual(len(gates['profiles']),8)
        for p in gates['profiles']:
            self.assertFalse(p['advertise']);self.assertFalse(p['copy_images']);self.assertFalse(p['charge_applicant'])
