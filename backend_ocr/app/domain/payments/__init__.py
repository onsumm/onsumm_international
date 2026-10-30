"""Payment-side domain logic that isn't a route.

`receipt.py` renders what a vendor is told about a payment; `notify.py` is the
Communication Agent hook that actually delivers it. The Payment Hub's routes
(app/routers/disbursement.py) call only `notify.enqueue_payment_receipt`.
"""
