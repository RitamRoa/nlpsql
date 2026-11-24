"""Run the held-out benchmark and ablations against the configured model.

    python run_heldout_benchmark.py                      # all configs, all 80 questions
    python run_heldout_benchmark.py --configs full baseline_minimal --domain company
    python run_heldout_benchmark.py --out results.json   # also keep per-question detail

Numbers are only meaningful with a real model (see core/providers). The mock
provider does not read the question, so its score says nothing about the system.
"""
import argparse
import json
import sys

from core.providers.factory import configured_provider
from core.providers.mock import MockProvider
from eval.heldout import CONFIGS, format_markdown, run_heldout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", nargs="+", default=None, choices=list(CONFIGS), help="ablation configs to run")
    parser.add_argument("--domain", nargs="+", default=None, help="restrict to these schemas")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--out", help="write full JSON results (with per-question detail) here")
    parser.add_argument("--markdown", help="write the summary tables here")
    parser.add_argument("--allow-mock", action="store_true", help="run even with the mock provider (for smoke tests)")
    args = parser.parse_args(argv)

    provider = configured_provider()
    if isinstance(provider, MockProvider) and not args.allow_mock:
        print(
            "Refusing to run: the configured provider is the mock, so results would be meaningless.\n"
            "Configure a real model, or pass --allow-mock for a smoke test.",
            file=sys.stderr,
        )
        return 2

    report = run_heldout(
        provider,
        args.configs,
        limit=args.limit,
        domains=args.domain,
        on_progress=lambda name: print(f"running config: {name}", file=sys.stderr),
    )
    for warning in report["leakage"]["near_duplicates"]:
        print(f"warning: {warning['id']} is {warning['similarity']} similar to a few-shot question", file=sys.stderr)

    text = format_markdown(report)
    print(text)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
