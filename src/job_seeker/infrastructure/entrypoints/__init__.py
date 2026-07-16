"""Driving adapters, and the composition root.

The CLI and the MCP server translate an outside request into a use-case call and the result
back out. They own no business logic. This is also the one place permitted to name concrete
adapters and wire them to the ports the application declares, because something has to know
both sides, and confining that knowledge to the edge is what keeps the rest inverted.
"""
