"""evaluator — reward-model evaluation + the 1000+ automated test suite.

- ``HeuristicReward``: dependency-free multi-dimension reward (format,
  length, content, repetition, coherence, safety) — always available.
- ``HuggingFaceReward``: sequence-classification reward model (lazy).
- ``generate_suite(n, seed)``: deterministic 1000+ case evaluation suite
  (JSONL-serializable; each case carries machine-checkable assertions).
- ``Evaluator``: runs a model over the suite (streaming progress),
  scores with the reward + automatic checks, reports per-category stats.

CLI: ``python -m features.evaluator --suite 1000 --model mock-1b
--report report.json``
"""

from __future__ import annotations

from .harness import CaseResult, EvalReport, Evaluator, evaluate_checks
from .rewards import HeuristicReward, HuggingFaceReward, RewardModel
from .suite import CATEGORIES, generate_suite, load_suite, save_suite

__all__ = [
    "CATEGORIES",
    "CaseResult",
    "EvalReport",
    "Evaluator",
    "HeuristicReward",
    "HuggingFaceReward",
    "RewardModel",
    "evaluate_checks",
    "generate_suite",
    "load_suite",
    "save_suite",
]
