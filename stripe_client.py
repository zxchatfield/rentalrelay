"""Minimal Stripe-hosted Checkout API integration. Secrets stay server-side."""
import hmac
import hashlib
import json
import os
import time
import requests

STRIPE_API = 'https://api.stripe.com/v1'

class StripeError(RuntimeError): pass


def _stripe_post(path, data, *, idempotency_key=None):
    secret = os.getenv('STRIPE_SECRET_KEY', '').strip()
    # hsurr: is the authd surrogate for a vault-stored key on this VM: the
    # egress swaps it for the real key on requests to api.stripe.com.
    # Production deployments use a real sk_test_/sk_live_ key instead.
    if not secret or not secret.startswith(('sk_test_', 'sk_live_', 'hsurr:')):
        raise StripeError('Stripe secret key is not connected')
    headers = {'Authorization': f'Bearer {secret}'}
    if idempotency_key: headers['Idempotency-Key'] = idempotency_key
    try:
        r = requests.post(f'{STRIPE_API}{path}', headers=headers, data=data, timeout=15)
        response = r.json()
        if not r.ok:
            raise StripeError(response.get('error', {}).get('message', f'Stripe HTTP {r.status_code}'))
        return response
    except (requests.RequestException, ValueError) as exc:
        raise StripeError('Stripe connection error') from exc


def checkout_session(app_id, tracking_token, listing_title, amount_cents, base_url, customer_email=None):
    # Stripe Checkout hosted payment: credit cards never touch this application.
    # Email and name are intentionally excluded from Stripe metadata.
    data = {
        'mode': 'payment',
        # Card-only pilot: do not enable delayed payment methods without an async-payment handler.
        'payment_method_types[0]': 'card',
        'client_reference_id': app_id,
        'success_url': f'{base_url}/track/{tracking_token}?checkout=returned',
        'cancel_url': f'{base_url}/checkout/cancel',
        'line_items[0][price_data][currency]': 'usd',
        'line_items[0][price_data][unit_amount]': str(amount_cents),
        'line_items[0][price_data][product_data][name]': 'Independent rental application forwarding service',
        'line_items[0][price_data][product_data][description]': f'Application delivery service for {listing_title[:80]}',
        'line_items[0][quantity]': '1',
        'payment_intent_data[metadata][application_reference]': app_id,
    }
    if customer_email and '@' in customer_email and not any(c in customer_email for c in '\r\n'):
        data['customer_email']=customer_email
    return _stripe_post('/checkout/sessions', data, idempotency_key='rentalrelay-v1-'+app_id)


def refund(payment_intent, app_id):
    return _stripe_post('/refunds', {'payment_intent': payment_intent, 'reason': 'requested_by_customer'}, idempotency_key='rentalrelay-refund-v1-'+app_id)


def verify_stripe_signature(payload: bytes, signature: str, secret: str, *, clock=None, tolerance=300):
    if not secret: raise StripeError('Webhook secret not configured')
    try:
        pieces = [p.strip().split('=', 1) for p in signature.split(',') if '=' in p]
        timestamp = next(int(v) for k,v in pieces if k == 't')
        digests = [v for k,v in pieces if k == 'v1']
        now = int(time.time() if clock is None else clock)
        if abs(now - timestamp) > tolerance or not digests: raise ValueError('Expired or missing signature')
        signed = str(timestamp).encode('ascii') + b'.' + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, digest) for digest in digests): raise ValueError('Incorrect signature')
        event = json.loads(payload)
        if not isinstance(event, dict) or not event.get('id'): raise ValueError('Invalid event')
        return event
    except (ValueError, StopIteration, UnicodeDecodeError) as exc:
        raise StripeError('Invalid Stripe webhook signature') from exc
