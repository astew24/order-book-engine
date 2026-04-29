# order-book-engine

A single-process limit order book matching engine in Python, plus a
Poisson order-flow simulator, a throughput benchmark, and a LOBSTER CSV
replayer.

[Live demo](https://astew24.github.io/order-book-engine/)

## What's in here

- `order_book.py` - the matching engine. Price-time priority, limit /
  market / cancel.
- `simulator.py` - Poisson arrivals with GBM mid-price drift.
- `benchmark.py` - runs a few flow scenarios and prints ops/sec.
- `lobster_replay.py` - parses LOBSTER message CSVs and replays them into
  the book.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python benchmark.py
python benchmark.py --orders 500000 --runs 5

python lobster_replay.py --generate --n 5000
python lobster_replay.py --file AAPL_2012-06-21_34200000_57600000_message_5.csv
```

```python
from order_book import LimitOrderBook
from simulator import OrderFlowSimulator, SimConfig

book = LimitOrderBook()
sim = OrderFlowSimulator(SimConfig(arrival_rate=5000, initial_mid=150.0))
print(sim.run_into_book(book, n=10_000))
```

## Data structure

Bids and asks are `SortedDict[float, deque[Order]]`. Bid keys are negated
so `peekitem(0)` returns the best bid without a custom comparator. Within
a price level, orders sit in a `deque` for O(1) FIFO.

A separate `order_id -> (book_side, price_key)` map gives cancels an O(1)
level lookup. Removal inside the deque is O(k), but k is the per-level
depth and stays small in practice.

## Matching

A buy limit at price P sweeps asks priced `<= P` in price-then-time order
and rests the remainder at P. Sells are symmetric. Market orders match
against +/- infinity so they sweep until the book is exhausted; leftover
quantity is dropped rather than rested. (An earlier version looped on
this and deadlocked.)

## LOBSTER format

Message columns: `Time, Type, OrderID, Size, Price, Direction`.

| Type | Meaning |
|------|---------|
| 1 | Submit limit |
| 2 | Partial cancel |
| 3 | Full cancel |
| 4 | Execution (visible) |
| 5 | Execution (hidden) |
| 7 | Trading halt |

Price is stored as integer * 10000. Direction: 1 buy, -1 sell. Real data
from lobsterdata.com.

## Design notes

- `sortedcontainers.SortedDict` over `heapq` because cancels need
  targeted removal at arbitrary price levels; a heap doesn't give you
  that cheaply.
- Single-threaded on purpose. In production you'd put this behind a
  queue.
- `deque` rather than `list` for the per-level FIFO (O(1) `popleft`).
