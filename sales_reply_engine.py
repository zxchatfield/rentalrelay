"""Deterministic draft replies for consenting Facebook Marketplace rental inquiries.

No Meta API connection is provided. This module drafts text; a separate authorized
messaging integration must deliver it. Never run with unverified inventory records.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any

SERVICE_FEE = 24.99

def _as_dt(value):
    if not value: return None
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

def available_verified(f: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    t = _as_dt(f.get('verified_available_at_iso'))
    return bool(t and timedelta(0) <= now - t <= timedelta(hours=48) and f.get('advertising_authorized'))

def payment_ready(f: dict[str, Any], now: datetime | None = None) -> bool:
    required = ('advertising_authorized','photos_authorized', 'manager_accepts_rentalrelay_application',
                'manager_additional_fees_confirmed', 'safe_delivery_confirmed',
                'stripe_live_and_webhooks_verified')
    return bool(not f.get('is_demo', True) and available_verified(f, now)
                and all(f.get(x) is True for x in required)
                and f.get('property_manager_name') and f.get('property_manager_verification_url_or_phone')
                and f.get('approved_application_url')
                and f.get('city') and f.get('bedrooms') is not None
                and f.get('rent_usd') is not None
                and abs(float(f.get('processing_fee_usd', -1)) - SERVICE_FEE) < 1e-4)

def _manager_fee(f):
    value = f.get('manager_additional_fee_usd')
    if value is None: return None
    timing = f.get('manager_fee_timing') or 'at the disclosed stage of the process'
    if float(value) == 0: return 'There is no separate application fee for this property.'
    return f"There is also a separate ${float(value):.2f} application fee {timing}."

def _verified_highlight(f: dict[str, Any]) -> str:
    """Use a previously verified, property-focused selling point only."""
    points = f.get('selling_points_verified') or []
    for point in points:
        if isinstance(point, dict):
            if point.get('approved') is True and point.get('source') and point.get('text'):
                return str(point['text']).strip().rstrip('.') + '.'
    return ''

def _home_summary(f: dict[str, Any]) -> str:
    beds = f.get('bedrooms')
    city = f.get('city')
    rent = f.get('rent_usd')
    if beds is None or not city or rent is None:
        return f.get('property_short_name') or 'this home'
    return f"{beds}-bedroom home in {city} for ${float(rent):,.0f}/month"

def _base(f):
    home = f.get('property_short_name') or 'the property'
    manager = f.get('property_manager_name') or 'the property manager'
    return home, manager

def render(intent: str, f: dict[str, Any], now: datetime | None = None, fact: str | None = None) -> dict[str, Any]:
    """Return {'text', 'can_send_payment_link', 'requires_review'}.

    Intents are intentionally restricted; callers should NOT supply arbitrary
    natural-language output as intent or interpolate untrusted user text.
    """
    now = now or datetime.now(timezone.utc)
    home, manager = _base(f)
    ready = payment_ready(f, now)
    fee = _manager_fee(f)
    url = f.get('approved_application_url') if ready else None
    def out(text, link=False, review=False):
        return {'text': text, 'can_send_payment_link': bool(link and ready), 'requires_review': review}
    if intent == 'stop':
        return out("Understood. I won't follow up. Take care!")
    if intent == 'sensitive_or_legal':
        return out("I don't want to guess about that. I'll refer your question for review rather than give you an inaccurate answer.", review=True)
    if intent == 'scam_or_verify':
        contact = f.get('property_manager_verification_url_or_phone')
        if not contact or not f.get('manager_accepts_rentalrelay_application'):
            return out("It is absolutely okay to verify the rental first. I’m confirming the details before accepting any paid applications.", review=True)
        return out(f"Good question. RentalRelay is an independent application-submission service; {manager} manages {home} and has agreed to accept our submissions. You can verify with management at {contact}. Our $24.99 fee is for our service, not for a tour, deposit, or guaranteed approval.")
    if intent == 'direct_application':
        contact = f.get('property_manager_verification_url_or_phone')
        if contact:
            return out(f"Yes, you can contact {manager} directly at {contact}. RentalRelay's optional $24.99 service is for collecting, submitting and tracking your application through our process.")
        return out("You can ask the property manager about applying directly. I'm checking their verified contact information and won't make one up.", review=True)
    if intent == 'fees':
        if fee:
            return out(f"RentalRelay's application submission fee is $24.99. {fee} This fee does not reserve the home or guarantee approval.")
        return out("Our application submission service is $24.99. I’m confirming whether there are any additional application fees before sending a payment link.", review=True)
    if intent == 'who_are_you':
        return out(f"We're RentalRelay, an independent application intake and submission service, not the owner or manager. {manager} handles screening, showings, approvals, and leasing.")
    if intent == 'priority':
        return out("No. The $24.99 is for our application submission service; it does not reserve the property, provide priority, or guarantee approval or a showing.")
    if intent == 'why_apply_with_us':
        return out("RentalRelay makes it easy to fill out your application online and have it submitted for review, with delivery tracking. Our service costs $24.99; it isn't a deposit or a guarantee of approval. If you'd like, I can send the link.")
    if intent == 'hesitant_about_fee':
        return out("I understand wanting to know exactly what you're paying for. The $24.99 covers our application collection, submission, and delivery tracking—not a showing, deposit, or guaranteed approval. I'm happy to answer questions about the home before you decide.")
    if intent == 'move_in_timing':
        if not ready:
            return out("Thanks! I'm confirming the current application process before sending a payment link. Is there anything about the home you'd like me to check?", review=True)
        move_date = f.get('move_in_date')
        date_phrase = f" The advertised availability is {move_date}." if move_date else ''
        return out(f"Thanks for sharing!{date_phrase} If you'd like to move forward, our application can be completed right from your phone and submitted for review. RentalRelay's service costs $24.99. Would you like the secure link?")
    if intent == 'selling_points':
        point = _verified_highlight(f)
        if not point:
            return out("I can share the confirmed property details, but I don't want to make up selling points. What would you like to know?", review=True)
        return out(f"One thing that stands out is: {point} Would you like details on applying?")
    if intent == 'availability_and_fee':
        # If asked for costs up front, never hide them behind a sales question.
        if not ready:
            return out("I'm confirming current availability and the total application fees before I send anything paid.", review=True)
        return out(f"Yes, {_home_summary(f)} is currently available. RentalRelay's application-submission service costs $24.99. {fee} Would you like the link?")
    if intent == 'payment_method':
        return out("Payment is handled through our secure website's Stripe checkout. Please never send card or bank information in Facebook Messenger.")
    if intent == 'status':
        return out("I can check the application status with your private tracking reference. Payment is confirmed through Stripe, and we only mark an application delivered after verified receipt. Please don’t share any card details here.")
    if intent == 'refund':
        return out("If your application could not be delivered as promised or the home became unavailable before delivery, please use the published refund process. I can help check the actual application status without guessing.", review=True)
    if intent == 'application_before_tour':
        intent = 'tour'
    if intent == 'tour':
        if not available_verified(f, now):
            return out("I haven’t confirmed this home’s current availability yet, so I can’t promise a showing or accept an application payment. I’ll verify the details first.", review=True)
        policy = f.get('tour_policy','unknown')
        if (policy == 'required_before_tour'
            and f.get('tour_policy_evidence')
            and f.get('our_application_satisfies_manager_pre_tour_requirement') is True):
            if ready:
                return out(f"For {home}, an application is required before a showing can be scheduled. You can complete yours online through RentalRelay for $24.99. {fee} Would you like the secure application link?")
            return out(f"For {home}, an application is required before a showing can be scheduled. I’m verifying the application setup, so I can’t accept payment just yet.", review=True)
        if policy == 'tour_before_application_allowed' and f.get('manager_approved_tour_instructions'):
            instructions = f['manager_approved_tour_instructions'].strip().rstrip('.')
            text = (f"Yes, you can request a viewing first. The confirmed process is: {instructions}. "
                    + ("If you’d rather start the application now, you can apply through RentalRelay for $24.99. Would you like the application link?" if ready
                       else "I can also explain our application process when the details are confirmed."))
            return out(text)
        return out("I haven’t confirmed this property’s showing requirements yet, so I don’t want to give you incorrect information. I can verify the process and let you know.", review=True)
    if intent == 'property_fact':
        allowed = {'deposit_usd': 'security deposit', 'pet_policy': 'pet policy',
                   'move_in_date': 'move-in availability', 'public_address': 'location',
                   'parking': 'parking', 'utilities': 'utilities', 'lease_term': 'lease term'}
        if fact not in allowed:
            return out("I don’t have a confirmed answer on that yet. I’ll verify it rather than guess.", review=True)
        val = f.get(fact)
        if val is None or val == '':
            return out(f"I don’t have a confirmed answer about the {allowed[fact]} yet. I’ll check and get back to you.", review=True)
        if fact == 'public_address' and f.get('address_disclosure') != 'public':
            return out("I don’t have a confirmed public street address to share for this listing. I can verify the location details.", review=True)
        display = f"${float(val):,.2f}" if fact == 'deposit_usd' else str(val)
        return out(f"For {home}, the confirmed {allowed[fact]} is {display}. Would you like to get the application started?")
    if intent in ('availability','interested','apply_request'):
        if not available_verified(f, now):
            return out("Thanks for reaching out! I'm confirming that the home is still available before sending any application links.", review=True)
        if not ready:
            return out(f"Thanks for your interest in {home}! I'm confirming the application details before sending a link. What would you like to know about the home?", review=True)
        if intent == 'availability':
            point = _verified_highlight(f)
            point_part = f" {point}" if point else ''
            return out(f"Yes, it's still available! It's a {_home_summary(f)}.{point_part} When are you hoping to move?")
        if intent == 'interested':
            return out("Glad you're interested! The next step, if you'd like to apply, is our simple online application. You can complete it from your phone, and we submit it for review. RentalRelay's application-submission service is $24.99. Would you like the secure link?")
        return out(f"Absolutely! Here's the secure application: {url}. RentalRelay's application-submission service costs $24.99. {fee} The application and checkout explain who receives your information and any separate fees before payment. Applying doesn't guarantee approval or a showing.", link=True)
    return out("I want to make sure I give you the correct information. What's your question about this specific property?", review=True)
