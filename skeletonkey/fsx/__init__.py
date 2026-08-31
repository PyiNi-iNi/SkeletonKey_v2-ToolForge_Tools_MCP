"""Adaptive filesystem access for agents, behind one policy chokepoint.

sandbox.py owns *authorization* (may this path be touched at all), ops.py owns
*mechanics* (atomic writes, newline/encoding preservation, paged reads),
search.py owns *discovery* (provider-selected grep), journal.py owns
*reversibility* (undo tokens for every mutation).
"""

from .journal import FsJournal, JournalEntry
from .ops import Fs, ReadResult, WriteResult, sniff, unified_diff
from .sandbox import PathSandbox, Resolved, SandboxPolicy, detect_case_sensitivity
from .search import SearchBackend, SearchHit, SearchOutcome

__all__ = ["Fs", "FsJournal", "JournalEntry", "PathSandbox", "ReadResult", "Resolved", "SandboxPolicy",
           "SearchBackend", "SearchHit", "SearchOutcome", "WriteResult", "detect_case_sensitivity",
           "sniff", "unified_diff"]
