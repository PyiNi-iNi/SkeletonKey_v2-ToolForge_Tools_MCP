"""agent_framework — AutoGen-style agents, code execution, CSV, web search.

- ``AutoGenConversation``: multi-agent round-robin with termination tokens
  (local implementation of the AutoGen pattern; the real ``autogen``
  package is not required)
- ``PythonExecAgent`` / ``CodeAgent``: sandboxed Python execution
  (isolated interpreter, minimal env, timeout kill) + generate→execute→
  feedback loop
- ``CSVAgent``: stdlib csv analytics (describe / query / aggregate)
- ``SearchAgent``: web search (DuckDuckGo HTML, keyless) + grounded answer
"""

from __future__ import annotations

from .autogen import Agent, AutoGenConversation, ConversationResult
from .csv_agent import CSVAgent
from .exec_agent import CodeAgent, CodeReport, PythonExecAgent, ExecReport
from .websearch import DuckDuckGoSearch, MockSearch, SearchAgent, SearchResult

__all__ = [
    "Agent",
    "AutoGenConversation",
    "CodeAgent",
    "CodeReport",
    "ConversationResult",
    "CSVAgent",
    "DuckDuckGoSearch",
    "ExecReport",
    "MockSearch",
    "PythonExecAgent",
    "SearchAgent",
    "SearchResult",
]
