import unittest
from datetime import datetime, timezone, timedelta
from sales_reply_engine import render, payment_ready, available_verified

now = datetime(2026,11,25,17,0,tzinfo=timezone.utc)

def ready_facts():
    return dict(
        listing_id='TEST-101',is_demo=False,property_short_name='A sample home',city='Sample City',
        address_disclosure='public',public_address='1 Fictional Way', rent_usd=1250,
        bedrooms=3,bathrooms=2,deposit_usd=1250, pet_policy='cats allowed',move_in_date='2026-12-10',
        verified_available_at_iso=(now-timedelta(hours=5)).isoformat(),
        advertising_authorized=True,photos_authorized=True,
        property_manager_name='Test Manager Co.',property_manager_verification_url_or_phone='https://example.org/test',
        manager_accepts_rentalrelay_application=True,manager_additional_fees_confirmed=True,
        manager_additional_fee_usd=0, manager_fee_timing='before screening',
        tour_policy='unknown',tour_policy_evidence=None,
        our_application_satisfies_manager_pre_tour_requirement=False,
        manager_approved_tour_instructions=None,approved_application_url='https://example.org/apply/test-101',
        safe_delivery_confirmed=True,stripe_live_and_webhooks_verified=True,
        processing_fee_usd=24.99,
    )

class SalesReplyPolicyTests(unittest.TestCase):
    def test_ready(self): self.assertTrue(payment_ready(ready_facts(),now))
    def test_stale_blocks_payment(self):
        f=ready_facts();f['verified_available_at_iso']=(now-timedelta(days=4)).isoformat()
        self.assertFalse(payment_ready(f,now)); self.assertNotIn('https://example.org/apply',render('apply_request',f,now)['text'])
    def test_missing_manager_blocks_payment(self):
        f=ready_facts(); f['manager_accepts_rentalrelay_application']=False
        self.assertFalse(payment_ready(f,now))
    def test_demo_blocks_payment(self):
        f=ready_facts(); f['is_demo']=True
        self.assertFalse(payment_ready(f,now))
    def test_unknown_tour_cannot_claim_required(self):
        txt=render('tour',ready_facts(),now)['text']
        self.assertNotIn('required',txt.lower());self.assertNotIn('https://example.org/apply',txt)
    def test_verified_required_tour(self):
        f=ready_facts();f['tour_policy']='required_before_tour';f['tour_policy_evidence']='written agreement dated today';f['our_application_satisfies_manager_pre_tour_requirement']=True
        self.assertIn('before a showing can be scheduled',render('tour',f,now)['text'])
    def test_unverified_required_tour_cannot_claim_required(self):
        f=ready_facts();f['tour_policy']='required_before_tour';f['tour_policy_evidence']=None
        self.assertNotIn('required before',render('tour',f,now)['text'].lower())
    def test_our_application_does_not_satisfy_pre_tour(self):
        f=ready_facts();f['tour_policy']='required_before_tour';f['tour_policy_evidence']='written';f['our_application_satisfies_manager_pre_tour_requirement']=False
        self.assertNotIn('required before',render('tour',f,now)['text'].lower())
    def test_tour_allowed_truthful(self):
        f=ready_facts();f['tour_policy']='tour_before_application_allowed';f['manager_approved_tour_instructions']='Call manager at verified number'
        text=render('tour',f,now)['text'];self.assertIn('request a viewing first',text);self.assertIn('Call manager',text)
    def test_real_apply_link_only_when_ready(self):
        f=ready_facts();r=render('apply_request',f,now)
        self.assertTrue(r['can_send_payment_link']);self.assertIn('https://example.org/apply/test-101',r['text'])
    def test_extra_fee_disclosed(self):
        f=ready_facts();f['manager_additional_fee_usd']=40
        self.assertIn('$40.00',render('apply_request',f,now)['text'])
    def test_fee_unknown_blocks_payment(self):
        f=ready_facts();f['manager_additional_fees_confirmed']=False
        self.assertFalse(payment_ready(f,now))
    def test_direct_application_truth(self):
        self.assertIn('you can contact',render('direct_application',ready_facts(),now)['text'].lower())
    def test_priority_never_promised(self):
        self.assertIn('does not reserve',render('priority',ready_facts(),now)['text'])
    def test_property_question_from_record(self):
        self.assertIn('cats allowed',render('property_fact',ready_facts(),now,fact='pet_policy')['text'])
    def test_property_missing_not_invented(self):
        f=ready_facts();f['pet_policy']=None
        self.assertTrue(render('property_fact',f,now,fact='pet_policy')['requires_review'])
    def test_address_restricted_not_unlocked_by_payment(self):
        f=ready_facts();f['address_disclosure']='manager_restricted'
        self.assertNotIn('Fictional Way',render('property_fact',f,now,fact='public_address')['text'])
    def test_stop_no_upsell(self):
        self.assertNotIn('apply',render('stop',ready_facts(),now)['text'].lower())
    def test_card_payment_never_in_messenger(self):
        self.assertIn('never send card',render('payment_method',ready_facts(),now)['text'].lower())
    def test_normal_marketing_copy_does_not_name_manager(self):
        f=ready_facts()
        for intent in ('availability', 'interested', 'apply_request', 'tour', 'fees', 'status'):
            txt=render(intent, f, now)['text'].lower()
            self.assertNotIn('test manager co.', txt, intent)
            self.assertNotIn('property manager', txt, intent)
    def test_asked_about_business_identity_answer_truthfully(self):
        txt=render('who_are_you', ready_facts(), now)['text']
        self.assertIn('Test Manager Co.', txt)
    def test_asked_about_direct_application_answer_truthfully(self):
        txt=render('direct_application', ready_facts(), now)['text']
        self.assertIn('Test Manager Co.', txt)
    def test_paid_status_not_guessed(self):
        self.assertIn('Stripe',render('status',ready_facts(),now)['text'])

if __name__=='__main__': unittest.main()
