"""MCP transport adapter.

Uses the *low-level* server, not the high-level MCPServer, on purpose: a dynamic
toolset must own tools/list per session (capability gating, provider
de-duplication, token budget) and be able to raise tools/list_changed when the
host's capabilities change. Schema comes from manifests, not from python
signatures, so a tool author's type hints can never silently change the public
contract. See ADR-0005.
"""
