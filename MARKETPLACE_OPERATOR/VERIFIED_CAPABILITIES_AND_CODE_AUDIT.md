# RentalRelay: reality-checked Muse capability audit (November 25, 2026)

This audit is based on official Meta/Stripe documentation and code inspection of this exact package. It is NOT evidence that any user account is connected or that unattended operation has been demonstrated.

## VERIFIABLE FEATURES
- Muse works through persistent cloud computer/browser and can pursue longer goals when the app is closed. Meta source: https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/
- Muse for Small Business can connect with Stripe and Facebook/Instagram business accounts. This does NOT mean personal Marketplace publishing/read+reply permissions are included. Source: https://about.fb.com/news/2026/09/introducing-muse-small-business/
- Muse's approval settings may include Allow once, Allow for task, Allow for site, Always allow for a Connector, depending on action/connection. No guarantee blanket permissions for all Marketplace actions. Source: https://www.meta.com/help/superintelligence/1385290430137537/
- Muse scheduled tasks support recurring work. They are not notification subscriptions or real-time Messenger event webhooks. Source: https://www.meta.com/help/superintelligence/1484325780075655/
- Meta's Seller app allows Marketplace listings, a centralized per-listing inbox and bulk listing creation. Its existence does not prove Muse can control every Seller action, post rental-category listings automatically, or respond autonomously. Source: https://about.fb.com/news/2026/07/introducing-seller-app-facebook-marketplace/
- Meta Marketplace AI can automatically reply to certain availability questions. It does not substantiate a 24/7 free-form application-sales bot for rental messages. Source: https://about.fb.com/news/2026/03/facebook-marketplace-new-meta-ai-tools-make-selling-faster-and-easier/
- Approved Marketplace partner APIs support `rental` listing types. These are partner-only and do not give a new business an instant personal Marketplace listing API. Source: https://developers.facebook.com/docs/marketplace/partnerships/itemAPI/
- Stripe offers Checkout/webhooks. Signed webhooks are the correct independent server-to-server trigger for revenue messages. Source: https://docs.stripe.com/webhooks

## WHAT THIS PACKAGE ACTUALLY CONTAINS
- 4 researched Detroit property records and four Marketplace AD DRAFTS. There are no commercially licensed property photographs in this ZIP, and there is no confirmed live listing link or availability guarantee.
- FastAPI app with admin/research/reply draft pages, and a DEMO-only printable application preparation form.
- Stripe Checkout/webhook source, but production gate in app.py requires `sk_live_`, HTTPS, launch checks, and properties satisfying manager intake/secure delivery flags. Its checkout copy currently sells **independent application forwarding**, not a working renter-hired application assistance workflow. Do NOT claim it is ready for that distinct model.
- Telegram worker polls the app's local database every 60 seconds (when configured and running), sends deduplicated notifications for Stripe-webhook-marked paid application records, and an 8AM Eastern report. It is NOT deployed or linked to the owner's Telegram bot just because Muse sent the user a separate Telegram message.
- Facebook Page scheduling code exists but is disabled in production deployment. NO authorized personal Marketplace messaging or listing connector is implemented in this repository.
- The existing 84 tests are local unit/integration-mock tests; they do not exercise Meta or Stripe/Telegram production endpoints.

## NON-NEGOTIABLE IMPLEMENTATION GAP (DON'T HIDE IT)
1. Marketplace: prove account-specific rental listing publication plus continuous permitted inbound+outbound Marketplace message access with reply timestamps; 'Facebook connector present' is not this proof. Do not substitute Page posts.
2. Inventory: obtain an actually current listing with commercial photo rights and the right to accurately represent the ad, or find a substitute listing. The four researched Detroit homes are lead candidates, not publishable inventory.
3. Product/fulfillment: choose the REAL service being sold. If authorized property-forwarding, confirm the recipient accepts and will acknowledge it; if renter-side assistance, implement the actual official portal preparation/submission workflow and change checkout description/fulfillment, confirmations, privacy text, and tests. Do not claim application submitted until verifiable.
4. Deploy HTTPS, secure account setup, customer support, privacy/refund terms, and backups. Local app isn't public.
5. Stripe: build an isolated staging config and tests for actual `sk_test_` sessions without flipping real checkout live; ensure no test events are reported as genuine revenue. Then wire a real signing-secret Stripe webhook to the deployed app.
6. Telegram: verify that signed Stripe webhook triggers a paid record, notifier sends ONE actual Telegram message to owner's existing bot/chat while app is closed, then refund/delivery alerts, and daily report. Muse's own Stripe connector and a Telegram test ping DO NOT substitute.
7. Operational monitoring: implement stale-listing expiry, dispute/refund handoff, message backpressure, exception alerts, and account rate/approval handling; keep minimum monitoring by owner.

## STRICT RESULT FORMAT
For each component, report exactly: PROVED (direct production URL/event ID/redacted screenshot/log), NOT_PROVED (not tested), BLOCKED (precise platform or user approval), or BUILT_LOCALLY (passing tests only). Never use “launched”, “running”, “fully automated” or “generating sales” for local code or drafts.

## HOW TO SPEND THE NEXT SESSION
First run a capability test in user's actual Muse/Facebook connections, not by scraping or creating duplicate accounts. Ask Muse to inspect available Marketplace rental posting, Marketplace inbox read, Marketplace inbox write, and whether an allowed permission can cover repeated customer messages. If missing, explicitly mark the zero-daily-work target impossible through Muse alone and surface the exact technical blocker. In parallel, work on a public HTTPS site and a real fulfillment design without charging anyone. The owner permits one-time approvals but did NOT authorize contact with property owners, posting unaudited homes, or charges for an unfulfillable service.
