"""CLI: python -m features.evaluator --suite 1000 --model mock-1b --report out.json

Env: SK_EVAL_BACKEND (auto|mock|heuristic|hf), SK_EVAL_REWARD_MODEL,
SK_INF_BACKEND, SK_INF_MODEL — same conventions as the other features.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from features.common.config import FeatureConfig  # noqa: E402
from features.evaluator import Evaluator, generate_suite, load_suite  # noqa: E402
from features.inference_engine import InferenceEngine, make_backend  # noqa: E402


async def _run(args: argparse.Namespace) -> int:
    cfg = FeatureConfig(prefix="SK_EVAL")
    engine = InferenceEngine()
    engine.register(make_backend())  # honors SK_INF_BACKEND
    reward = None
    if cfg.get_str("reward", "heuristic") == "hf":
        from features.evaluator.rewards import HuggingFaceReward

        reward = HuggingFaceReward()

    if args.suite_file:
        cases = load_suite(args.suite_file)
        print(f"loaded {len(cases)} cases from {args.suite_file}")
    else:
        cases = generate_suite(n=args.suite, seed=args.seed)
        print(f"generated {len(cases)} cases (seed={args.seed})")

    evaluator = Evaluator(engine, model=args.model, reward=reward)
    report = None
    async for event in evaluator.astream(cases):
        if event["stage"] == "case":
            if args.verbose or (event["index"] + 1) % max(1, len(cases) // 20) == 0:
                print(f"[{event['index'] + 1}/{len(cases)}] "
                      f"{event['case_id']} ({event['category']}) "
                      f"score={event['score']} pass={event['passed']}")
        elif event["stage"] == "done":
            report = event["report"]

    assert report is not None
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"report written to {args.report}")
    print(json.dumps({k: report[k] for k in
                      ("model", "n_cases", "mean_score", "pass_rate", "elapsed_sec")},
                     indent=2))
    for cat, stats in sorted(report["by_category"].items()):
        print(f"  {cat:20s} n={stats['n']:<5d} mean={stats['mean']:.3f} "
              f"pass={stats['pass_rate']:.1%}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="features.evaluator")
    parser.add_argument("--suite", type=int, default=1000,
                        help="number of suite cases to generate (default 1000)")
    parser.add_argument("--seed", type=int, default=20260916, help="suite seed")
    parser.add_argument("--suite-file", default=None, help="use a JSONL suite file instead")
    parser.add_argument("--model", default=None, help="model id (default: engine default)")
    parser.add_argument("--report", default=None, help="write JSON report to this path")
    parser.add_argument("--verbose", action="store_true", help="print every case")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
