# order-book-engine

## What this does

`order-book-engine` is a **high-performance limit order book (LOB) matching engine** built entirely from scratch in Python — no external matching engine libraries. It models the core trading infrastructure found at exchanges like NASDAQ, NYSE, and CME:

- **Price-time priority matching** — best bid/ask always wins; ties broken by arrival time (FIFO)
- **Three order types** — limit, market, and cancel
- **O(1) best-bid/ask lookup** via a `SortedDict` of price levels mapped to `deque` queues
- **Poisson arrival process simulator** — realistic order flow with GBM mid-price drift
- **Throughput benchmarking** — measures orders/second across multiple scenarios
- **LOBSTER data replay** — reads the standard academic LOB dataset format and validates fills

Useful for quantitative finance research, HFT prototyping, exchange infrastructure study, and order flow simulation.

---

## Project structure

```
order-book-engine/
├── order_book.py      # Core matching engine (LOB, Order, Fill, Side)
├── simulator.py       # Poisson arrival process order flow simulator
├── benchmark.py       # Throughput benchmarking across scenarios
├── lobster_replay.py  # LOBSTER CSV replay + fill validation
└── requirements.txt
```

---

## Quick start

```bash
git clone <repo>
cd order-book-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Run the benchmark

```bash
python benchmark.py
# Run with more orders for stable results:
python benchmark.py --orders 500000 --runs 5
```

Sample output:
```
Scenario                        Orders    Mean ops/s        Std        Min        Max   ns/order    Fills
------------------------------  ------  ------------ ----------  ---------  ---------  ---------  -------
Limit orders only               100000       1.23M       45.2K      1.18M      1.28M        812   12,847
Market orders only              100000       2.10M       61.1K      2.05M      2.15M        476   98,331
Realistic mixed flow            100000       1.54M       38.7K      1.49M      1.60M        649   23,104
High cancel rate (50%)          100000       1.88M       42.0K      1.83M      1.93M        531    8,211
Tight spread (1 tick)           100000       1.41M       37.2K      1.36M      1.46M        709   31,902
```

### Simulate order flow

```python
from order_book import LimitOrderBook
from simulator import OrderFlowSimulator, SimConfig

config = SimConfig(
    arrival_rate=5000,     # 5,000 orders/sec
    initial_mid=150.0,
    market_frac=0.20,
    cancel_frac=0.15,
)
book = LimitOrderBook()
sim = OrderFlowSimulator(config)
stats = sim.run_into_book(book, n=10_000)
print(stats)
```

### Replay LOBSTER data

```bash
# Generate a synthetic sample and validate:
python lobster_replay.py --generate --n 5000

# Replay a real LOBSTER file:
python lobster_replay.py --file AAPL_2012-06-21_34200000_57600000_message_5.csv
```

---

## Core data structure

```
bids: SortedDict[float, deque[Order]]   # keys are negated prices (descending)
asks: SortedDict[float, deque[Order]]   # keys are positive prices (ascending)
```

`SortedDict.peekitem(0)` is **O(log n)** for best-price access — effectively O(1) in practice because the number of distinct price levels is small relative to order volume.

Within each price level, orders are stored in a `deque` for **O(1) FIFO enqueue/dequeue**, implementing strict price-time priority.

### Matching algorithm

1. A new buy limit order at price P sweeps the ask side, consuming all resting sell orders priced ≤ P in price-then-time order.
2. Any remaining quantity rests at P on the bid side.
3. Market orders are treated as limit orders at ±∞ — they sweep until filled or the book is exhausted.
4. Cancels use an `order_id → (book_side, price_key)` index for O(1) level lookup; removal within the deque is O(k) where k is the level depth (typically small).

---

## LOBSTER data format

LOBSTER message CSV columns:
```
Time, Type, OrderID, Size, Price, Direction
```

| Type | Meaning |
|---|---|
| 1 | New limit order submission |
| 2 | Partial cancellation |
| 3 | Full cancellation |
| 4 | Execution (visible order) |
| 5 | Execution (hidden order) |
| 7 | Trading halt |

Prices are stored as integers (price × 10,000). Direction: 1 = buy, -1 = sell.

Real LOBSTER data is available at [lobsterdata.com](https://lobsterdata.com/).

---

## Design decisions

- **`sortedcontainers.SortedDict`** is used instead of `heapq` because it supports both O(log n) insertion *and* arbitrary price-level lookup for cancel — a heap alone doesn't efficiently support targeted removal.
- **Negated bid keys** allow a single `peekitem(0)` call to return the best bid (highest price) without a custom comparator.
- **No threading** — the engine is designed as a single-threaded hot loop. In production, you'd put it behind a queue (e.g., `asyncio.Queue` or a LMAX Disruptor equivalent).
- **`deque` not `list`** — FIFO O(1) append/popleft vs O(n) list.pop(0).
