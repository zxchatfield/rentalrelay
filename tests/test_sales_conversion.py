import unittest
from datetime import datetime, timezone, timedelta
from sales_reply_engine import render, payment_ready

NOW = datetime(2026,11,25,17,0,tzinfo=timezone.utc)

def f():
    return {
        'listing_id':'TEST','is_demo':False,'property_short_name':'Example rental',
        'city':'Testville','bedrooms':3,'bathrooms':2,'rent_usd':1250,
        'verified_available_at_iso':(NOW-timedelta(hours=2)).isoformat(),
        'advertising_authorized':True,'photos_authorized':True,
        'manager_accepts_rentalrelay_application':True,
        'manager_additional_fees_confirmed':True,'manager_additional_fee_usd':0,
        'safe_delivery_confirmed':True,'stripe_live_and_webhooks_verified':True,
        'property_manager_name':'Example Management','property_manager_verification_url_or_phone':'https://example.org/verify',
        'approved_application_url':'https://example.org/apply', 'processing_fee_usd':24.99,
        'tour_policy':'unknown','tour_policy_evidence':None,
        'our_application_satisfies_manager_pre_tour_requirement':False,
        'selling_points_verified':[{'text':'Fenced backyard','approved':True,'source':'written verified info'}]
    }

class ConversionPolicyTests(unittest.TestCase):
    def test_ready(self): self.assertTrue(payment_ready(f(),NOW))
    def test_first_touch_sells_property_before_fee(self):
        x=render('availability',f(),NOW)['text']
        self.assertIn('Fenced backyard',x)
        self.assertIn('When are you hoping to move?',x)
        self.assertNotIn('$24.99',x)
    def test_unverified_selling_point_not_added(self):
        x=f();x['selling_points_verified']=[{'text':'Most amazing deal ever','approved':False}]
        t=render('availability',x,NOW)['text']
        self.assertNotIn('Most amazing',t)
    def test_offer_discloses_fee(self):
        x=render('interested',f(),NOW)['text']
        self.assertIn('$24.99',x)
        self.assertIn('Would you like',x)
        self.assertNotIn('Example Management',x)
    def test_timing_moves_to_paid_offer(self):
        x=render('move_in_timing',f(),NOW)['text']
        self.assertIn('$24.99',x)
        self.assertNotIn('Example Management',x)
    def test_application_request_is_direct_and_discloses_price(self):
        z=render('apply_request',f(),NOW)
        self.assertTrue(z['can_send_payment_link'])
        self.assertIn('https://example.org/apply',z['text'])
        self.assertIn('$24.99',z['text'])
    def test_fees_directly_answered_even_at_first_contact(self):
        t=render('availability_and_fee',f(),NOW)['text']
        self.assertIn('$24.99',t)
    def test_verified_manager_identity_on_direct_question(self):
        self.assertIn('Example Management',render('who_are_you',f(),NOW)['text'])
    def test_stale_no_link(self):
        x=f();x['verified_available_at_iso']=(NOW-timedelta(days=3)).isoformat()
        t=render('apply_request',x,NOW)
        self.assertNotIn('https://example.org/apply',t['text'])
        self.assertFalse(t['can_send_payment_link'])
    def test_no_unproven_tour_rule(self):
        t=render('tour',f(),NOW)['text']
        self.assertNotIn('required before',t.lower())
    def test_missing_property_details_blocks_payment(self):
        x=f(); x['rent_usd']=None
        self.assertFalse(payment_ready(x,NOW))
    def test_no_priority_promise(self):
        self.assertIn('does not reserve',render('priority',f(),NOW)['text'])
    def test_stop_stays_stop(self):
        self.assertNotIn('application',render('stop',f(),NOW)['text'].lower())
    def test_verified_feature_only(self):
        x=f();x['selling_points_verified']=[{'text':'Fenced backyard','approved':True,'source':''}]
        self.assertNotIn('Fenced backyard',render('availability',x,NOW)['text'])
    def test_fee_objection_not_shady(self):
        t=render('hesitant_about_fee',f(),NOW)['text']
        self.assertIn('$24.99',t)
        self.assertIn('not a showing',t)
    def test_unknown_property_not_payment_ready(self):
        x=f();x['manager_accepts_rentalrelay_application']=False
        self.assertFalse(payment_ready(x,NOW))

if __name__ == '__main__': unittest.main()
