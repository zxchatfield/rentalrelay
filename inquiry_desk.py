"""Private, deterministic Marketplace-reply dry-run desk.

It never reads Meta, sends messages, stores customer text, publishes a
listing, or creates a payment.  All real leads remain research-only.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from sales_reply_engine import render as render_approved_reply

PATTERNS = [
    ('stop', r'\b(stop messaging|stop contacting|do not contact|don.t contact|unsubscribe|remove me|leave me alone|stop sending)\b', None),
    ('refund', r'\b(refund|money back|cancel payment|chargeback)\b', None),
    ('scam_or_verify', r'\b(scam|fraud|legit|legitimate|real listing|trust|verify|verification|is this real)\b', None),
    ('direct_application', r'\b(apply direct|apply directly|application directly|original website|without rentalrelay|without you|apply with the owner|apply with management|apply on their)\b', None),
    ('who_are_you', r'\b(who are you|are you the owner|do you own|are you the landlord|who manages)\b', None),
    ('payment_method', r'\b(credit card|debit card|how do i pay|payment method|cash app|cashapp|venmo|zelle)\b', None),
    ('fees', r'\b(application fee|application cost|how much to apply|fee to apply|why.*fee|cost to apply|\$24[.,]99)\b', None),
    ('tour', r'\b(see it|see the (house|home|property)|see .*first|tour|view it|viewing|showing|walk.?through|come look|see inside)\b', None),
    ('property_fact', r'\b(pets?|dogs?|cats?)\b', 'pet_policy'),
    ('property_fact', r'\b(deposit|security deposit|move.?in cost)\b', 'deposit_usd'),
    ('property_fact', r'\b(utilities|electric|water bill|gas bill)\b', 'utilities'),
    ('property_fact', r'\b(parking|garage|driveway)\b', 'parking'),
    ('property_fact', r'\b(address|where is it|what area|location|neighborhood)\b', 'public_address'),
    ('property_fact', r'\b(lease term|12 months|month.?to.?month)\b', 'lease_term'),
    ('property_fact', r'\b(move in date|when can i move|available date|ready to move)\b', 'move_in_date'),
    ('apply_request', r'\b(apply|application link|send (me )?(the )?link|sign up|ready to apply)\b', None),
    ('availability', r'\b(available|vacant|still open|still there|still for rent|is this rented)\b', None),
    ('interested', r'\b(interested|looks good|like the house|when can i move|want this|perfect)\b', None),
]


def classify(message: str):
    value = re.sub(r'\s+', ' ', message.strip().lower())
    for intent, rx, fact in PATTERNS:
        if re.search(rx, value, re.I):
            return intent, fact
    return 'unknown', None


def demo_facts():
    """Entirely fictitious sandbox data; no real link or property is referenced."""
    return {
        'property_short_name': 'the fictional Cedar Lane sample',
        'city': 'Example City', 'bedrooms': 3, 'rent_usd': 1200.0,
        'is_demo': False,  # Simulate full eligibility ONLY inside this dry-run function.
        'advertising_authorized': True, 'photos_authorized': True,
        'manager_accepts_rentalrelay_application': True,
        'manager_additional_fees_confirmed': True, 'safe_delivery_confirmed': True,
        'stripe_live_and_webhooks_verified': True,
        'property_manager_name': 'Fictional Management',
        'property_manager_verification_url_or_phone': 'https://example.invalid/fictional',
        'approved_application_url': 'https://example.invalid/never-a-real-application',
        'processing_fee_usd': 24.99, 'manager_additional_fee_usd': 40,
        'manager_fee_timing': 'later in the fictional process',
        'verified_available_at_iso': datetime.now(timezone.utc).isoformat(),
        'selling_points_verified': [{'text': 'A fenced backyard', 'approved': True, 'source': 'synthetic-test'}],
        'tour_policy': 'required_before_tour',
        'tour_policy_evidence': 'synthetic-test',
        'our_application_satisfies_manager_pre_tour_requirement': True,
        'pet_policy': 'Cats allowed; dogs case-by-case (fictional)',
        'deposit_usd': 1200, 'parking': 'Driveway (fictional)',
        'utilities': 'Tenant pays gas and electricity (fictional)',
        'public_address': '1 Fictional Cedar Lane, Example City',
        'address_disclosure': 'public',
    }


def draft_for_test(message: str, lead: dict | None = None, *, simulate_verified=False):
    """Return draft only. Source-linked research leads are NEVER payment-ready."""
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1000:
        raise ValueError('Enter a customer question (1–1000 characters).')
    intent, fact = classify(message)
    if simulate_verified:
        result = render_approved_reply(intent, demo_facts(), fact=fact)
        # No valid link, message, or real property can escape the fictitious sandbox.
        return {'intent': intent, 'fact': fact, 'text': result['text'],
                'requires_review': result['requires_review'],
                'can_send': False, 'mode': 'fictional_example'}
    if not lead:
        raise ValueError('Select a Detroit research lead.')
    if intent == 'stop':
        text = "Understood. I won't send any further messages."
    elif intent in ('scam_or_verify', 'who_are_you'):
        text = ("RentalRelay is an independent application-assistance service, not the property owner. "
                "You're welcome to verify the original rental listing first. We aren't collecting payments for this property while its details are being checked.")
    elif intent == 'payment_method':
        text = "We don't collect card numbers in Facebook messages. No paid applications are open for this property while we're verifying it."
    elif intent == 'fees':
        text = ("RentalRelay's proposed application-assistance service is $24.99. "
                "Before accepting any payment for this home, we need to confirm its application process and any additional fees.")
    elif intent == 'refund':
        text = "We're not accepting payments for this property. If you've already paid RentalRelay for another home, please use your payment receipt and our support process."
    elif intent == 'direct_application':
        text = ("You can review the property's original listing for its own application process. "
                "RentalRelay is a separate, optional application-assistance service.")
    elif intent == 'tour':
        text = ("I understand wanting to see the home. I'm checking the current showing and application process "
                "before giving you an incorrect requirement or appointment.")
    elif intent == 'property_fact' and fact == 'public_address':
        text = (f"The originally advertised address is {lead['address']}, {lead['city']}. "
                "I'm still checking that the listing is current.")
    elif intent == 'property_fact' and fact == 'deposit_usd':
        text = ("I'm checking the current security deposit and move-in costs against the original listing "
                "before quoting a confirmed amount.")
    elif intent == 'property_fact':
        topic = {'pet_policy':'pet rules','utilities':'utilities','parking':'parking',
                 'lease_term':'lease terms','move_in_date':'move-in dates'}.get(fact, 'property details')
        text = f"Good question! I'm checking the {topic} against the original listing before confirming."
    elif intent == 'availability':
        # Source listing itself may be outdated, so never say 'Yes, available'.
        text = (f"Thanks for asking about {lead['address']}! The most recent research lists it at "
                f"${lead['rent_usd']:,.0f}/month, but I'm verifying the current price and availability first. "
                "When are you hoping to move?")
    elif intent == 'apply_request':
        text = ("I can help you with the application process. First I'm confirming this property's current "
                "requirements and submission method, so I don't send you an incorrect payment or application link.")
    elif intent == 'interested':
        text = ("Great! What appeals to you most about the home? I'm confirming its current availability and "
                "application process before offering an application link.")
    else:
        text = ("Thanks for reaching out! Which detail about the home would you like to know? "
                "I'm verifying the listing information before any application payments are accepted.")
    return {'intent': intent, 'fact': fact, 'text': text, 'requires_review': True,
            'can_send': False, 'mode': 'unverified_research'}
