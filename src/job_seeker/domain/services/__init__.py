"""Domain services: business logic that is not naturally a method on one entity.

Scoring a posting against a profile, deciding whether the seeker may hold a role, judging
relevance, and deciding which postings are the same posting are all *reasoning*, not I/O.
They belong here, in the centre of the hexagon, not in infrastructure.

That is why they are not ports. A port exists to cross the boundary out of the application:
fetching over HTTP, rendering a file, reading config. These services touch nothing external,
so putting them behind a port would push the product's actual thinking into an adapter and
leave the domain holding nothing but data classes.

May import: `job_seeker.domain` and the standard library.
May NOT import: `job_seeker.application`, `job_seeker.infrastructure`, or any I/O library
(httpx, bs4, yaml). If a service needs one of those, it is not a domain service.

A service that later needs to cross the boundary (an LLM-backed scorer, say) does not move
here. It becomes an outbound port in `job_seeker.application.ports` with an adapter in
infrastructure, and this package keeps the pure default implementation.
"""
