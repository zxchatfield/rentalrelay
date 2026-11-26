# RentalRelay — Rental Application Service Pilot

A working **demo and deployable starter application** for a Michigan rental-advertising and paid application-forwarding service. The business name `RentalRelay` is a *temporary placeholder*, not a registered or cleared brand.

**Default price:** $24.99 for the optional independent application collection and forwarding service. The receiving property manager may charge separately. Applicants may always contact the manager directly.

## What works

- Responsive homepage, catalog, detail pages, FAQ, how-it-works, contact, policy drafts.
- Multi-field applicant form and four separate required consents.
- A fictional, clearly marked demo property. **Demo mode NEVER accepts applicant data or payments.**
- Private admin sign-in with hashed password and signed cookies, CSRF protection.
- Admin property creation, marketing/intake authorization flags, 48-hour availability gating.
- Admin launch checklist (off by default), private paid-application view and manual fulfillment confirmation.
- Stripe-hosted Checkout integration at a **server-set $24.99**, with idempotency key, Stripe webhook signature verification and replay protection, applicant tracking URL, and refund API.
- No SSN, DOB, bank credentials, government ID scans, credit reports or card fields.
- Time-based applicant data deletion when the server starts and periodically while serving traffic (production needs retention monitoring).
- Automated tests for validation, admin, demo fail-closed behavior, and payment webhook verification.

## What is NOT connected or done

- Your real Stripe business account, bank payout details, and live HTTPS domain.
- Real manager-approved available properties or image licenses.
- Secure email/document transfer to a named manager (the MVP requires manual operator delivery; email automation is intentionally OFF).
- Real support inbox or legally reviewed business-specific policy documents.
- Hosting, complete security assessment, encrypted storage/backups, or formal confirmation of applicable licensing requirements.
- Automated Facebook Marketplace publishing, which may require authorized methods and platform compliance.

**Do not use this app to collect real personal information or money until these items are handled.**

## Run in demo mode — Windows / macOS / Linux

1. Install Python 3.11 or newer.
2. Extract `rentalrelay_app.zip` and open a terminal in `rentalrelay_app`.
3. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   # Windows PowerShell: .venv\Scripts\Activate.ps1
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` (on Windows, `copy .env.example .env`).
5. Generate a secure session secret:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Put the result in `SESSION_SECRET` in `.env`.
6. Generate your private administrator password hash:

   ```bash
   python password.py
   ```

   Enter a strong password, then copy the output into `ADMIN_PASSWORD_HASH` in `.env`. Set `ADMIN_USERNAME` to a username you choose. **Never put the raw password into source code or share it in chat.**
7. Start the demo server:

   ```bash
   uvicorn app:app --reload
   ```

8. Open `http://127.0.0.1:8000/` in your browser. Go to `/admin/login` to sign in. The initial demo listing is entirely fictional. Real paid applications remain disabled.

For a **quick visual preview without installing Python**, open `demo-preview.html`. That HTML is a static snapshot only; the functional app requires the server.

## Configure a real Stripe account later

1. Sign up or open your existing Stripe business account on Stripe's official site; complete **identity/business verification** and bank payout details directly in Stripe. Do not provide bank/ID/password details in chat.
2. Begin with Stripe's test mode in a private test environment to verify the flow. **Do not use real applicant details while testing.**
3. Add the real `sk_live_...` Stripe secret API key to your hosting provider's private environment secrets as `STRIPE_SECRET_KEY`. Do not place it in a webpage, repository, screenshot, or chat.
4. Add a Stripe webhook endpoint at `https://YOUR-DOMAIN/stripe/webhook`. Subscribe to `checkout.session.completed` and `charge.refunded`. Configure the signing secret `whsec_...` as `STRIPE_WEBHOOK_SECRET`.
5. Set `PUBLIC_BASE_URL=https://YOUR-DOMAIN`, `SESSION_HTTPS=true`, and use HTTPS hosting with durable encrypted-at-rest storage. Keep `OPERATING_LIVE=false` until launch.
6. Confirm all manager agreements, listings, applicant privacy controls, support inbox, policy review, payment, webhook, refund, and delivery procedures.
7. Change `OPERATING_LIVE=true`, restart the server, sign in as operator, and check every **payment activation** item on `/admin` only after you personally verify it. Until all confirmations are saved, payment stays off.
8. Verify a real low-value transaction/refund only after all onboarding steps are done. Pay close attention to separate manager fees and applicant communication.

The live-mode server requires a live Stripe secret, webhook secret, HTTPS base URL, HTTPS-only session cookies and the admin launch checklist. It will not silently collect money with an incomplete configuration.

## How to approve a listing

1. Obtain permission from the **manager/owner** to use listing text/photos and to collect data for this property.
2. Obtain **written confirmation that the manager accepts your application format and exact delivery channel**, including whether the manager charges a separate screening/application fee.
3. Confirm availability, price, pets, deposit and tour arrangements.
4. Add property at `/admin`. It starts **unpublished**.
5. Check marketing permission, application acceptance, photo rights and secure-delivery confirmation, then publish; the verified timestamp is recorded.
6. Reverify availability at least every 48 hours. Stale properties automatically stop appearing on the public site.

Do not list properties you cannot legitimately forward applications to. Do not imply you are the landlord or that the $24.99 fee is mandatory.

## Data / security / operations

- SQLite local database `rentalrelay.sqlite3` is created on first launch. The database contains **personal information** if you go live. Use encrypted, durable hosting storage; never publish this file or commit it to source control.
- One server worker and one durable storage volume are assumed. For scale, move to managed PostgreSQL with access controls and encrypted backups.
- Server-side Stripe checkout creation is separate from signed webhook reconciliation. Applicants are not marked paid based on redirect alone.
- Application data appears only on operator pages, which require admin login. Manager forwarding is a manual process and the operator should mark delivered **only after receiving actual confirmation**.
- Change the draft contact/policy information to your real registered business details and verified procedures before accepting payments.
- This software is a pilot and has not had a production penetration test. Obtain any licensing or data-security determinations required for your actual operations.

## Test

```bash
pytest -q
```

The tests mock Stripe communication. Passing tests **does not** mean an actual Stripe account or live payment workflow has been configured.

## Additional Muse 24/7 operating kit (Nov 25)

Read **`MUSE_24_7_SYSTEM.md`**, **`MUSE_24_7_GOAL.md`**, **`MUSE_RECURRING_TASKS.md`**, and **`AUTOPILOT_SETUP.md`**. This build includes a **tested read-only 24/7 payment notifier** and **tested approval-gated Facebook Page publishing adapter**; both require provider credentials and always-on hosting. Manager delivery has an optional signed HTTPS API adapter, but NO real manager is connected; see the gap section in the system document. Do not interpret this ZIP as a live, deployed business.

### Manager API delivery adapter

`manager_api_delivery.py` is an **OPTIONAL**, default-off adapter for property managers that explicitly accept a signed HTTPS JSON intake and return `{"accepted": true, "receipt_id": "..."}`. It validates stored applicant consent and signed-Stripe paid status, fresh manager authorization, and exact manager email-to-endpoint mapping; it requires an idempotent receiver and an HMAC secret (24+ chars) supplied through server secrets. No real endpoints are configured. Never assume an arbitrary manager website accepts this JSON. For managers without such integrations, a manager portal or authorized equivalent must be developed and verified before real paid intake. Set `AUTO_MANAGER_DELIVERY_ENABLED=false` until a signed integration and receipt test exist.


## Muse continuing-work update (Nov 25 revision 2)

`MUSE_CONTINUATION_2026-11-25.md` is the latest handoff for Meta Muse. `muse_preflight.py --json` emits a privacy-safe machine-readable status for autonomous checks without disclosing applicant data or secret values. The alert worker now sends the **previous full Detroit calendar day's** sales in the 8 AM digest (rather than a misleading incomplete current day). Facebook publishing is gated behind the operator launch checklist, and manager destination validation rejects direct private or local IP addresses. These are improvements to the offline pilot only; real hosting, credentials, manager acceptance and actual 24/7 executions remain unconnected.

## Muse handoff v3 — safety and deployment update

Start with `OPEN_FIRST.txt`, `MUSE_VERIFIED_GUIDE.md`, `MUSE_MASTER_PROMPT.txt`, `ONE_TIME_DEPLOYMENT.md`, `MUSE_RECURRING_TASKS.md` and the new Docker Compose setup in `deployment/compose.yaml`.

- Independent manager-delivery worker heartbeat: paid checkouts disabled if last heartbeat is older than 15 minutes.
- Per-property delivery hold when receiver is offline or unavailable; blocks new paid checkout until a confirmed manager receipt releases the hold.
- If any already-paid application remains pending over 120 minutes, further checkouts for that property are paused.
- Only card payments are enabled in Stripe Checkout for the pilot; delayed-payment methods are not accepted without an asynchronous settlement handler.
- Manual application delivery confirmation now requires a genuine manager-issued receipt ID, stored in the audit.
- `/healthz` exposes only web-server health; `/admin/diagnostics` returns private PII-free operational readiness for authorized Muse sessions.
- `deployment/compose.yaml` runs the web app, notifier, delivery and Page publisher with independent restart policies in a dedicated live profile. Not deployed in this kit.
- Expanded mocked tests, including web form -> Stripe signed event -> manager receipt and failure/duplicate scenarios.

A 24/7 system additionally needs real secure hosting, a participating manager, a Stripe merchant account and a Muse account connection; do not call it fully autonomous until the end-to-end acceptance tests in `ONE_TIME_DEPLOYMENT.md` have succeeded.

- Optional `customer_notifications.py` emails verified payment, manager receipt, and refund notices through configured business SMTP SSL. No marketing emails, no real credentials in project files; requires separate connection and mock-to-live test. The Stripe hosted checkout also supplies the customer email to the payment provider as a standard checkout field (not metadata).
- `retention_cleanup` job runs daily even if the site has no visitor traffic. Pending paid applications are not purged until resolved.
