"""Application layer: use cases, and the ports they need the outside world to satisfy.

A use case orchestrates a piece of work end to end ("find the jobs this seeker can hold")
by calling domain services and outbound ports. It holds no business rules of its own: if a
rule is about *jobs*, it belongs in `job_seeker.domain`; if it is about *sequencing*, it
belongs here.

May import: `job_seeker.domain` and the standard library.
May NOT import: `job_seeker.infrastructure`, or any concrete adapter, ever. The application
names what it needs as a Protocol in `ports.py`; infrastructure supplies something that
satisfies it, and the composition root wires the two together. This is the dependency
inversion the whole layout exists to enforce, and it is the one rule whose violation is
never a small thing: an import of httpx or a board name here means the hexagon has leaked.
"""
