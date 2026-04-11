"""
benchmark.py — Measure orders/second throughput of the matching engine.

Runs several scenarios and prints a report:
  - Limit orders only (resting book builds up)
  - Market orders only (drains the book)
  - Mixed realistic flow (matches SimConfig defaults)
  - Stress: 100% cancel rate (tests cancel path)

Usage:
    python benchmark.py
    python benchmark.py --orders 500000 --runs 3
"""

import argparse
import statistics
import time
from dataclasses import dataclass
from typing import Callable

from order_book import LimitOrderBook, Order, Side
from simulator import OrderFlowSimulator, SimConfig


# ---------------------------------------------------------------------------
# Benchmark harness
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    scenario: str
    orders: int
    runs: int
    mean_ops: float
    std_ops: float
    min_ops: float
    max_ops: float
    total_fills: int
    mean_latency_ns: float     # nanoseconds per order


def run_scenario(
    scenario_name: str,
    orders: int,
    runs: int,
    config: SimConfig,
    warmup: bool = False,
) -> BenchmarkResult:
    if warmup:
        # Throwaway pass so Python's bytecode cache and branch predictor are warm
        # before the first timed run. Reduces cold-start noise on short benchmarks.
        _book = LimitOrderBook()
        _sim = OrderFlowSimulator(config)
        for _order in _sim.stream(min(orders, 1_000)):
            _book.submit(_order)

    ops_list: list[float] = []
    fills_last = 0

    for run_idx in range(runs):
        book = LimitOrderBook()
        sim = OrderFlowSimulator(config)

        # Orders are pre-generated before the timed section so RNG overhead doesn't pollute throughput measurements
        order_list = list(sim.stream(orders))
        fills_seen = 0

        t_start = time.perf_counter_ns()
        for order in order_list:
            fills = book.submit(order)
            fills_seen += len(fills)
        t_end = time.perf_counter_ns()

        elapsed_ns = t_end - t_start
        elapsed_s = elapsed_ns / 1e9
        ops = orders / elapsed_s
        ops_list.append(ops)
        fills_last = fills_seen

    return BenchmarkResult(
        scenario=scenario_name,
        orders=orders,
        runs=runs,
        mean_ops=statistics.mean(ops_list),
        std_ops=statistics.stdev(ops_list) if runs > 1 else 0.0,
        min_ops=min(ops_list),
        max_ops=max(ops_list),
        total_fills=fills_last,
        mean_latency_ns=(1e9 / statistics.mean(ops_list)),
    )


def _fmt(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:.1f}"


def print_report(results: list[BenchmarkResult]):
    header = (
        f"{'Scenario':<30} {'Orders':>10} {'Mean ops/s':>12} "
        f"{'Std':>10} {'Min':>10} {'Max':>10} {'ns/order':>10} {'Fills':>8}"
    )
    print("\n" + "=" * len(header))
    print("  ORDER BOOK ENGINE — BENCHMARK RESULTS")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.scenario:<30} {r.orders:>10,} {_fmt(r.mean_ops):>12} "
            f"{_fmt(r.std_ops):>10} {_fmt(r.min_ops):>10} {_fmt(r.max_ops):>10} "
            f"{r.mean_latency_ns:>10.0f} {r.total_fills:>8,}"
        )
    print("=" * len(header) + "\n")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    (
        "Limit orders only",
        SimConfig(market_frac=0.0, cancel_frac=0.0, price_sigma=2.0),
    ),
    (
        "Market orders only",
        SimConfig(market_frac=1.0, cancel_frac=0.0),
    ),
    (
        "Realistic mixed flow",
        SimConfig(market_frac=0.20, cancel_frac=0.15),
    ),
    (
        "High cancel rate (50%)",
        SimConfig(market_frac=0.10, cancel_frac=0.50),
    ),
    (
        "Tight spread (1 tick)",
        SimConfig(market_frac=0.25, cancel_frac=0.10, spread_ticks=1),
    ),
]


def main():
    parser = argparse.ArgumentParser(description="Order book benchmarking tool")
    parser.add_argument("--orders", type=int, default=100_000, help="Orders per run")
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario")
    parser.add_argument("--scenario", type=str, default=None, help="Run a single scenario by partial name match")
    parser.add_argument("--warmup", action="store_true", default=False,
                        help="Run a throwaway warm-up pass before timing to eliminate cold-start noise")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [(n, c) for n, c in SCENARIOS if args.scenario.lower() in n.lower()]
        if not scenarios:
            print(f"No scenario matching '{args.scenario}'. Available:")
            for name, _ in SCENARIOS:
                print(f"  - {name}")
            return

    print(f"\nRunning {len(scenarios)} scenario(s) × {args.runs} runs × {args.orders:,} orders each …")
    if args.warmup:
        print("  (warmup pass enabled)")
    results = []
    for name, config in scenarios:
        print(f"  [{name}] …", end="", flush=True)
        r = run_scenario(name, args.orders, args.runs, config, warmup=args.warmup)
        results.append(r)
        print(f" {_fmt(r.mean_ops)} ops/s")

    print_report(results)


if __name__ == "__main__":
    main()
