"""
lobster_replay.py — LOBSTER data replay and fill validation.

LOBSTER (Limit Order Book System — The Efficient Reconstructor) produces
two CSV files per stock per day:
  - <ticker>_<date>_<starttime>_<endtime>_message_<levels>.csv  (event stream)
  - <ticker>_<date>_<starttime>_<endtime>_orderbook_<levels>.csv (book snapshots)

Message file columns (LOBSTER format):
  Time, Type, OrderID, Size, Price, Direction
    Time      : seconds from midnight (float)
    Type      : 1=submit limit, 2=cancel partial, 3=cancel full, 4=exec visible,
                5=exec hidden, 7=halt/trading-halt
    OrderID   : exchange order ID
    Size      : number of shares
    Price     : price * 10000 (integer representation)
    Direction : 1=buy, -1=sell

This module:
  1. Reads LOBSTER message CSV.
  2. Replays each event into our LimitOrderBook.
  3. Compares reported executions (Type 4/5) against fills produced by our engine.
  4. Prints a validation report.

If you don't have real LOBSTER data, run with --generate to create a
synthetic sample CSV for smoke-testing.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from order_book import LimitOrderBook, Order, OrderType, Side, Fill


# ---------------------------------------------------------------------------
# LOBSTER message types
# ---------------------------------------------------------------------------

MSG_SUBMIT_LIMIT   = 1
MSG_CANCEL_PARTIAL = 2
MSG_CANCEL_FULL    = 3
MSG_EXEC_VISIBLE   = 4
MSG_EXEC_HIDDEN    = 5
MSG_HALT           = 7


@dataclass
class LobsterMessage:
    time: float
    msg_type: int
    order_id: str
    size: int
    price: float          # already converted from integer price*10000
    direction: int        # 1=buy, -1=sell
    raw_price_int: int    # original integer price field


@dataclass
class ValidationResult:
    total_messages: int
    limit_orders_submitted: int
    cancels_attempted: int
    executions_reported: int      # LOBSTER Type 4/5
    fills_generated: int          # our engine
    matched_fills: int            # executions where qty matches
    mismatched_fills: int
    unknown_order_cancels: int    # cancels for IDs our engine doesn't know


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_lobster_csv(path: Path) -> list[LobsterMessage]:
    messages: list[LobsterMessage] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            try:
                time_s, mtype, oid, size, price_int, direction = (
                    float(row[0]), int(row[1]), row[2].strip(),
                    int(row[3]), int(row[4]), int(row[5]),
                )
            except (ValueError, IndexError):
                continue
            messages.append(LobsterMessage(
                time=time_s,
                msg_type=mtype,
                order_id=oid,
                size=size,
                price=price_int / 10_000,
                direction=direction,
                raw_price_int=price_int,
            ))
    return messages


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------

def replay(messages: list[LobsterMessage], verbose: bool = False) -> ValidationResult:
    book = LimitOrderBook()

    limit_submitted = 0
    cancels_attempted = 0
    executions_reported = 0
    unknown_cancels = 0
    # Map LOBSTER order ID → our Order for tracking
    order_map: dict[str, Order] = {}

    for msg in messages:
        side = Side.BUY if msg.direction == 1 else Side.SELL

        if msg.msg_type == MSG_SUBMIT_LIMIT:
            order = Order.limit(
                side=side,
                price=msg.price,
                qty=float(msg.size),
                order_id=msg.order_id,
            )
            book.submit(order)
            order_map[msg.order_id] = order
            limit_submitted += 1

        elif msg.msg_type in (MSG_CANCEL_PARTIAL, MSG_CANCEL_FULL):
            cancels_attempted += 1
            found = book.cancel(msg.order_id)
            if not found:
                unknown_cancels += 1
                if verbose:
                    print(f"  [warn] Cancel for unknown order {msg.order_id}")

        elif msg.msg_type in (MSG_EXEC_VISIBLE, MSG_EXEC_HIDDEN):
            executions_reported += 1
            # LOBSTER reports an execution against the passive side;
            # we just count them for comparison.

        elif msg.msg_type == MSG_HALT:
            if verbose:
                print(f"  [info] Trading halt at t={msg.time:.3f}s")

    fills_generated = len(book.fills)

    # Validate: count fills where our qty matches LOBSTER execution events
    # (Simple approach: compare totals; a full match would align by order ID)
    total_lobster_exec_qty = sum(
        m.size for m in messages if m.msg_type in (MSG_EXEC_VISIBLE, MSG_EXEC_HIDDEN)
    )
    total_our_fill_qty = sum(int(f.quantity) for f in book.fills)
    matched = 1 if abs(total_lobster_exec_qty - total_our_fill_qty) / max(total_lobster_exec_qty, 1) < 0.05 else 0

    return ValidationResult(
        total_messages=len(messages),
        limit_orders_submitted=limit_submitted,
        cancels_attempted=cancels_attempted,
        executions_reported=executions_reported,
        fills_generated=fills_generated,
        matched_fills=matched,
        mismatched_fills=1 - matched,
        unknown_order_cancels=unknown_cancels,
    )


# ---------------------------------------------------------------------------
# Synthetic sample CSV generator
# ---------------------------------------------------------------------------

def generate_sample_csv(path: Path, n_messages: int = 1000, seed: int = 42):
    """Write a synthetic LOBSTER-format message CSV for smoke testing."""
    rng = random.Random(seed)
    mid_price_int = 1_000_000   # $100.00 in LOBSTER integer format
    order_ids = [f"ORD{i:06d}" for i in range(n_messages)]
    resting: list[str] = []

    rows: list[list] = []
    t = 34_200.0  # 9:30:00 AM in seconds from midnight

    for i, oid in enumerate(order_ids):
        t += rng.expovariate(500)   # ~500 orders/sec
        direction = rng.choice([1, -1])
        size = rng.randint(1, 200)
        half_spread = 50   # 0.5 cents
        offset = rng.randint(-200, 200)

        if direction == 1:
            price_int = mid_price_int - half_spread + offset
        else:
            price_int = mid_price_int + half_spread + offset
        price_int = max(100, price_int)

        # Decide message type
        if resting and rng.random() < 0.15:
            # Cancel
            target = rng.choice(resting)
            resting.remove(target)
            rows.append([f"{t:.6f}", MSG_CANCEL_FULL, target, size, price_int, direction])
        elif resting and rng.random() < 0.25:
            # Execution against a resting order
            target = resting.pop(0)
            rows.append([f"{t:.6f}", MSG_EXEC_VISIBLE, target, size, price_int, direction])
            mid_price_int += rng.randint(-10, 10)
        else:
            # Submit limit
            rows.append([f"{t:.6f}", MSG_SUBMIT_LIMIT, oid, size, price_int, direction])
            resting.append(oid)

    path.write_text(
        "\n".join(",".join(str(c) for c in r) for r in rows) + "\n"
    )
    print(f"[generate] Wrote {len(rows)} messages to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_result(r: ValidationResult):
    print("\n" + "="*50)
    print("  LOBSTER REPLAY VALIDATION REPORT")
    print("="*50)
    print(f"  Total messages        : {r.total_messages:,}")
    print(f"  Limit orders submitted: {r.limit_orders_submitted:,}")
    print(f"  Cancels attempted     : {r.cancels_attempted:,}")
    print(f"    Unknown order cancels: {r.unknown_order_cancels:,}")
    print(f"  Executions (LOBSTER)  : {r.executions_reported:,}")
    print(f"  Fills (our engine)    : {r.fills_generated:,}")
    status = "PASS ✓" if r.matched_fills else "WARN — qty mismatch"
    print(f"  Qty match status      : {status}")
    print("="*50 + "\n")


def main():
    parser = argparse.ArgumentParser(description="LOBSTER data replay and fill validation")
    parser.add_argument("--file", type=Path, default=None, help="Path to LOBSTER message CSV")
    parser.add_argument("--generate", action="store_true", help="Generate synthetic sample CSV first")
    parser.add_argument("--sample-path", type=Path, default=Path("sample_lobster.csv"))
    parser.add_argument("--n", type=int, default=1000, help="Messages to generate (--generate mode)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    csv_path = args.file
    if args.generate or csv_path is None:
        generate_sample_csv(args.sample_path, n_messages=args.n)
        csv_path = args.sample_path

    if not csv_path.exists():
        print(f"Error: file not found: {csv_path}")
        sys.exit(1)

    print(f"[replay] Parsing {csv_path} …")
    messages = parse_lobster_csv(csv_path)
    print(f"[replay] Loaded {len(messages):,} messages. Running replay …")
    result = replay(messages, verbose=args.verbose)
    print_result(result)


if __name__ == "__main__":
    main()
