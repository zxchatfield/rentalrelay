# Muse: first concrete tests to prove, not a planning exercise

**Test 1 — Facebook Marketplace identity and listing eligibility**
Check Facebook account's actual Marketplace access, rental category, and official Seller features. Capture list of actions Muse can actually execute, not generic marketing claims. Do not post the four research leads without verified current listing rights and photo rights.

**Test 2 — Instant response capabilities**
Check whether Muse can observe a new personal Marketplace rental conversation as an event, read which listing it belongs to, reply using the approved sales reply script, and do so without repeated manual approval. If no live permitted listing exists, do a safe connector capability audit first; do not fabricate a real listing just to test. A recurring task polling hourly is not instant.

**Test 3 — Business model compatibility**
Inspect the current app.py gates: `authorized()`, `operational_ready()`, `auto_delivery_ready()` and `checkout_session()`. These implement manager-connected forwarding and `sk_live_`-only readiness. Do not start taking independent application-assistance payments without adding a real fulfilled service path, checkout descriptions, receipt and tests.

**Test 4 — Stripe-to-Telegram end-to-end, safe staging**
Ensure code accepts `sk_test_` *only* in an isolated staging configuration, with clear TEST labels and notifications excluded from real revenue. Deploy staging HTTPS, create test Checkout session, receive correctly signed event, ensure 1 paid record/1 Telegram test notification and repeat-event deduplication. After manager/portal fulfillment is operational, configure production `sk_live_`, correct webhook secret, and existing Telegram bot credentials privately. A Stripe connector displayed in Muse does NOT configure a webhook for the app.

**Test 5 — 24/7 and daily owner independence**
Once the above real services exist, leave Muse chat closed and prove a new permitted customer inquiry is answered in a measured time, full paid application is delivered, accurate payment arrives in Telegram, refund alert triggers, and 8 AM America/Detroit full-day summary arrives. If repeat approvals, seller limitations, or platform API availability prevent it, report an honest explicit blocker.

**User scope:** Detroit preferred; don't target Monroe or use work resources. Don't contact owners or managers, don't send external messages, don't post a listing, and don't activate live charging without new explicit approval for those actions.
