# Architecture

Hexagonal, and the boundaries are a test rather than a promise: `tests/test_architecture.py`
reads every module's imports out of the AST and fails when an arrow turns the wrong way.

Three layers. Dependencies point inward only, and that is enforced by `tests/test_architecture.py`,
which reads every module's imports out of the AST and fails when an arrow turns around. The rule is
a test, not a promise.

```
        infrastructure/entrypoints/     cli.py, mcp_server.py   (driving adapters + composition root)
                     |
                     v  calls a use case
        application/                    use cases + ports.py     (may import domain only)
                     ^  satisfies a Protocol
                     |
        infrastructure/                 sources/ reporting/ config/   (driven adapters)

        domain/                         models, profile, services      (imports nothing of ours)
```

- **domain/** is the centre: entities, the profile, and the *reasoning*. Scoring, eligibility,
  relevance and identity are business logic, so they live in `domain/services`, not behind ports.
  Imports nothing of ours and no I/O library.
- **application/** holds use cases and declares, in `ports.py`, what it needs the outside world to
  do. It never imports infrastructure.
- **infrastructure/** holds everything that touches the outside world, on both sides: driven
  adapters (boards, reporters, config) and driving adapters (`entrypoints`).

**The composition root is spread across infrastructure, and the shape is deliberate.** Each driven
package owns the catalogue of its own adapters, because a list of boards belongs beside the boards
rather than inside a driving adapter: `sources/defaults.py` names the boards, `reporting/__init__.py`
names the reporters. `entrypoints/` names the concrete profile provider, selects from those
catalogues, and calls `register_builtins()` once at startup. Nothing outside those three places may
name a concrete adapter.

Registration happens at startup and nowhere else. A library function that registers on the way past
mutates global state on whatever thread its caller happened to be on, which is exactly what
`sources/registry.py` warns against, and it makes the shared search path depend on state its
signature does not mention.

**Why the services are not ports.** A port exists to cross the boundary. `JobSource`, `Reporter` and
`ProfileProvider` cross it: HTTP, a file, a rendered artifact. A scorer does not; it is pure
reasoning over data already in hand. Putting it behind a port would push the product's actual
thinking into an adapter and leave the domain holding nothing but data classes. If a scorer ever
needs the network (an LLM judge), it becomes a port then, and the pure implementation stays.

**Every third-party job provider sits behind `JobSource`.** Himalayas, Remotive, RemoteOK,
WeWorkRemotely, WorkingNomads and JobSpy's boards reach the application through that Protocol and
nothing else. A board's quirks (a 20-item page cap, boilerplate in row zero, an RSS title of
"Company: Role") stop at its adapter. This is about providers, not libraries: pydantic in the domain
is settled and fine.

**SOLID mapping:**

- **S:** an adapter fetches and normalizes one board; a domain service does one kind of reasoning; a
  reporter renders and never filters.
- **O:** a new board is one new adapter plus a registry entry, plus whatever new names for the world
  it introduces. The first three land in `sources/`; the last lands in `domain/regions.py` as data,
  because a board that calls a country something no other board calls it is adding a spelling, not a
  rule. WeWorkRemotely added eleven. That is the domain being extended through a table rather than
  through code, and it is expected: a contributor editing `regions.py` for a new board has not done
  something wrong. The architecture test is what keeps the rest true.
- **L:** every source is substitutable behind `fetch(query) -> SourceResult`, and **must not raise**:
  a board being down is an expected outcome reported in `SourceResult.error`, not an exception, since
  siblings are in flight on other threads.
- **I:** small Protocols in `application/ports.py`. Structural, so an adapter satisfies one without
  importing it, which is what keeps the arrow inward even at the type level.
- **D:** use cases depend on Protocols; `entrypoints` injects the concrete adapters.

**Concurrency:** `fetch()` is **synchronous**. The orchestrator runs sources in parallel with a
`ThreadPoolExecutor`, so async never leaks into every layer. MCP tools call the sync use case.

**Pipeline stages:** fan out sources concurrently -> collect -> dedupe -> score -> classify -> filter
-> rank by fit desc -> return a `SearchResult` carrying both the ranked jobs and per-source coverage,
so a run where three of five boards failed is never mistaken for a healthy one.

---

