"""
simulator.py — Poisson arrival process order flow simulator.

Models a realistic order stream:
  - Order arrivals follow a Poisson process (exponential inter-arrival times)
  - Order side (buy/sell) is symmetric by default
  - Limit order prices are drawn from a normal distribution around mid-price
  - Market order fraction controls fraction of aggressive orders
  - Cancellations are generated for a fraction of open resting orders
  - Mid-price follows a geometric Brownian motion random walk

Note: set SimConfig.seed for fully reproducible order streams; useful for
deterministic benchmark comparisons and unit tests against known fills.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from order_book import LimitOrderBook, Order, Side, OrderType, Fill


# ---------------------------------------------------------------------------
# Simulator configuration
# ---------------------------------------------------------------------------

@dataclass
class SimConfig:
    # Arrival process
    arrival_rate: float = 1000.0      # orders/second (lambda for Poisson)

    # Pricing
    initial_mid: float = 100.0        # starting mid-price
    tick_size: float = 0.01
    spread_ticks: int = 2             # typical bid-ask spread in ticks
    price_sigma: float = 0.10         # std dev of limit price offset (in ticks)
    gbm_vol: float = 0.0001           # GBM vol per order for mid-price drift

    # Order size
    min_qty: float = 1.0
    max_qty: float = 100.0

    # Order mix
    market_frac: float = 0.20         # fraction of orders that are market orders
    cancel_frac: float = 0.15         # fraction of new order slots that are cancels

    # Symbol
    symbol: str = "AAAA"

    # Reproducibility
    seed: int | None = None


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class OrderFlowSimulator:
    """
    Generates a stream of Order objects following a Poisson arrival process.

    Usage:
        sim = OrderFlowSimulator(config)
        book = LimitOrderBook()
        for order in sim.stream(n=10_000):
            book.submit(order)
    """

    def __init__(self, config: SimConfig | None = None):
        self.config = config or SimConfig()
        self._rng = np.random.default_rng(self.config.seed)
        self._random = random.Random(self.config.seed)
        self._mid = self.config.initial_mid
        self._open_order_ids: list[str] = []
        self._order_counter = 0

    @property
    def mid_price(self) -> float:
        return self._mid

    def reset(self) -> None:
        """Reset simulator state — mid-price, order counter, and open order list.

        Useful for running multiple independent simulation runs on the same
        config without creating a new instance each time.
        """
        self._rng = np.random.default_rng(self.config.seed)
        self._random = random.Random(self.config.seed)
        self._mid = self.config.initial_mid
        self._open_order_ids = []
        self._order_counter = 0

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"SIM-{self._order_counter:08d}"

    def _update_mid(self):
        """Geometric Brownian Motion step for mid-price."""
        cfg = self.config
        shock = self._rng.standard_normal() * cfg.gbm_vol
        self._mid = max(cfg.tick_size, self._mid * (1 + shock))

    def _clamp_price(self, p: float) -> float:
        tick = self.config.tick_size
        return round(round(p / tick) * tick, 10)

    def next_order(self) -> Order:
        """Generate a single order."""
        cfg = self.config
        self._update_mid()

        # Decide order type
        r = self._random.random()
        if r < cfg.cancel_frac and self._open_order_ids:
            # Cancel a random resting order (represented as an order with CANCEL type).
            # The cancel carries the target's order_id so the book can find and remove it.
            target_id = self._random.choice(self._open_order_ids)
            self._open_order_ids.remove(target_id)
            return Order(
                order_id=target_id,     # cancel by referencing the original order_id
                side=Side.BUY,          # side is irrelevant for cancel
                order_type=OrderType.CANCEL,
                price=None,
                quantity=0,
            )

        side = Side.BUY if self._random.random() < 0.5 else Side.SELL

        if self._random.random() < cfg.market_frac:
            qty = self._rng.uniform(cfg.min_qty, cfg.max_qty)
            return Order.market(side, qty=round(qty, 2), order_id=self._next_order_id(), symbol=cfg.symbol)

        # Limit order — price centered around mid ± half-spread
        half_spread = (cfg.spread_ticks / 2) * cfg.tick_size
        if side == Side.BUY:
            center = self._mid - half_spread
        else:
            center = self._mid + half_spread

        offset_ticks = self._rng.normal(0, cfg.price_sigma)
        price = self._clamp_price(center + offset_ticks * cfg.tick_size)
        price = max(cfg.tick_size, price)

        qty = round(self._rng.uniform(cfg.min_qty, cfg.max_qty), 2)
        oid = self._next_order_id()
        order = Order.limit(side, price=price, qty=qty, order_id=oid, symbol=cfg.symbol)
        self._open_order_ids.append(oid)
        # Keep list manageable
        if len(self._open_order_ids) > 5000:
            self._open_order_ids = self._open_order_ids[-5000:]
        return order

    def stream(self, n: int) -> Iterator[Order]:
        """Yield n orders."""
        for _ in range(n):
            yield self.next_order()

    def stream_realtime(self, n: int) -> Iterator[Order]:
        """
        Yield n orders with real inter-arrival delays (Poisson process).
        Inter-arrival time ~ Exponential(1 / arrival_rate).
        """
        cfg = self.config
        for _ in range(n):
            delay = self._rng.exponential(1.0 / cfg.arrival_rate)
            time.sleep(delay)
            yield self.next_order()

    def run_into_book(
        self,
        book: LimitOrderBook,
        n: int,
        realtime: bool = False,
    ) -> dict:
        """
        Submit n simulated orders into `book`.
        Returns summary statistics.
        """
        gen = self.stream_realtime(n) if realtime else self.stream(n)
        t0 = time.perf_counter()
        for order in gen:
            book.submit(order)
        elapsed = time.perf_counter() - t0
        stats = book.stats()
        return {
            "orders_submitted": n,
            "elapsed_seconds": round(elapsed, 4),
            "orders_per_second": round(n / elapsed, 0) if elapsed > 0 else float("inf"),
            "total_fills": len(book.fills),
            "best_bid": stats.best_bid,
            "best_ask": stats.best_ask,
            "spread": stats.spread,
            "mid_price_final": self._mid,
        }
