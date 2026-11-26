# One active operating guide
The sole executable instruction for Muse is `../00_MUSE_OPEN_AND_EXECUTE.txt`. The files here contain the actual property source records, listing drafts, copy/qualification rules, test instructions and proof checklist. All previous large Muse prompt files were intentionally excluded to avoid contradictory mandates.

## Source of truth vs. actual integrations
`app.py`: existing FastAPI demo plus manager-integrated application / Stripe webhooks. Running tests proves local behavior only, not a live website or customer payments.
`autopilot_worker.py`: Stripe-paid state -> deduplicated Telegram sales/receipt alerts (requires running process and bot credentials; cannot be proven from a Telegram ping alone).
`sales_reply_engine.py` + `inquiry_desk.py`: response drafts, NOT a live Facebook inbox integration. Marketplace requires an actual Meta connector or approved agent action.
`DETROIT_INVENTORY`: research sources; NO live posts; NO photo licenses. Manager writing is NOT required to run the research/tests, but facts, photos and promised submission must genuinely work before external claims and charges.

## Product decision
* If actual authorized manager-forwarding intake: app may accurately offer submitted applications when recipient accepts packet and receipt is verified.
* If independent renter-hired assistance using an official portal: needs a separate accurate fulfillment and checkout description; the existing manager-forwarding route must NOT pretend to have submitted the portal application. If no genuine service can be delivered, do not enable the $24.99 checkout.
