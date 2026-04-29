# order-book-engine

A small Python limit order book and matching engine for studying market microstructure mechanics.

[Live demo](https://astew24.github.io/order-book-engine/) | [GitHub repo](https://github.com/astew24/order-book-engine)

## Overview

This repo implements the core pieces of a limit order book from scratch:

- price-time priority matching
- limit, market, and cancel orders
- FIFO queues at each price level
- best bid and best ask lookup through sorted price levels
- simulated order flow with configurable market and cancel activity
- LOBSTER-style message replay for working with academic order book data
- a small benchmark script for comparing scenarios

The project is meant to show clean trading-systems engineering in Python. It is not an exchange-grade engine and does not try to hide that Python has limits for low-latency production work.

## Why I Built This

I wanted to understand the mechanics behind the market data and execution systems that quant strategies depend on. The goal was to build the matching logic directly instead of treating the order book as a black box.

## What It Does

```text
incoming order
      |
      v
validate type and side
      |
      v
match against the opposite side at the best available price
      |
      v
emit fills for executed quantity
      |
      v
rest any remaining limit quantity at its price level
```

The matching engine keeps bids and asks in separate sorted maps. Each price level stores orders in a `deque`, so orders at the same price are matched in arrival order.

## Project Structure

```text
order-book-engine/
|-- order_book.py       # core Order, Fill, and LimitOrderBook logic
|-- simulator.py        # synthetic order flow generator
|-- benchmark.py        # throughput benchmark scenarios
|-- lobster_replay.py   # LOBSTER message CSV replay
|-- sample_lobster.csv  # small replay sample
|-- docs/index.html     # static browser demo
`-- requirements.txt
```

## How to Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the benchmark:

```bash
python benchmark.py
python benchmark.py --orders 500000 --runs 3
```

Run one benchmark scenario:

```bash
python benchmark.py --scenario "mixed" --orders 100000
```

Generate and replay a synthetic LOBSTER-style message file:

```bash
python lobster_replay.py --generate --n 5000
python lobster_replay.py --file sample_lobster.csv
```

Use the engine from Python:

```python
from order_book import LimitOrderBook, Order, Side

book = LimitOrderBook(symbol="AAPL")

book.submit(Order.limit(Side.BUY, price=150.00, qty=100))
book.submit(Order.limit(Side.SELL, price=150.05, qty=80))
fills = book.submit(Order.market(Side.BUY, qty=25))

print(fills)
print(book.depth(levels=5))
```

## Design Notes

- `SortedDict` keeps price levels ordered while still supporting inserts and deletes.
- Bid prices are stored as negative keys so the best bid can be read from the front of the sorted map.
- Each price level uses a `deque` to preserve FIFO behavior.
- Cancel lookup uses an `order_id` index to find the relevant price level before scanning that level.
- The simulator uses a configurable order mix rather than trying to model a specific venue exactly.

## Static Demo

`docs/index.html` is a lightweight browser demo that visualizes a simulated order book and trade tape. It is useful for explaining the project quickly, but the source of truth for matching behavior is `order_book.py`.

To preview it locally, open:

```text
docs/index.html
```

## Limitations

- Single-threaded Python implementation.
- No network gateway, persistence layer, risk checks, auctions, pegged orders, or hidden liquidity.
- Benchmark numbers depend heavily on hardware, Python version, scenario mix, and warm-up settings.
- LOBSTER replay is a learning tool, not a full exchange reconstruction engine.

## Next Steps

- Add a clearer separation between the matching core and simulation utilities.
- Expand tests around partial fills, cancels, and multi-level sweeps.
- Add optional CSV output from benchmarks for easier comparison.
- Document more edge cases in the LOBSTER replay path.
