"""Driven adapters: one job board each, behind the JobSource port.

One module per board, one class per board, no shared board knowledge. Adding a board must
not require touching the application, the domain, or another adapter: that is the open/closed
seam this package exists to provide.

An adapter's whole job is fetch plus normalize into the canonical `Job`. A board's quirks
(a boilerplate first row, a 20-item page cap, an RSS title of "Company: Role") stop here and
never reach the layers above. An adapter must not raise: a board that is down or has changed
its shape reports the failure in its result, because one board failing is a normal Tuesday
and must not abort a run across five others.
"""
