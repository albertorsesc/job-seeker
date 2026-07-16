"""Infrastructure: everything that touches the outside world.

Adapters live here, on both sides of the hexagon:

- **Driven (outbound)**: `sources` (job boards over HTTP), `reporting` (rendering to
  JSON/CSV/HTML), `config` (env and the profile file on disk). Each satisfies a Protocol
  declared in `job_seeker.application.ports`.
- **Driving (inbound)**: `entrypoints` (the CLI and the MCP server). These translate an
  outside request into a use-case call and translate the result back. They hold no business
  logic; a rule that appears in an entrypoint is a rule the CLI and MCP will disagree about.

May import: `job_seeker.application`, `job_seeker.domain`, and any third-party library.
May NOT be imported by: `job_seeker.domain` or `job_seeker.application`. Dependencies point
inward only. `entrypoints` is the composition root: the one place allowed to name concrete
adapters and wire them to the ports.
"""
