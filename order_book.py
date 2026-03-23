"""
order_book.py — Limit Order Book matching engine, built from scratch.

Core design:
  - bids: SortedDict (desc price) → deque of Order objects  (FIFO within level)
  - asks: SortedDict (asc price)  → deque of Order objects
  - O(1) best-bid / best-ask via SortedDict.peekitem()
  - Supports LIMIT, MARKET, and CANCEL order types
  - Emits Fill events for every match

No external matching engine libraries are used.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterator, Optional

from sortedcontainers import SortedDict


# ---------------------------------------------------------------------------
# Enums and data classes
# ---------------------------------------------------------------------------

class Side(Enum):
    BUY = auto()
    SELL = auto()


class OrderType(Enum):
    LIMIT = auto()
    MARKET = auto()
    CANCEL = auto()


class Status(Enum):
    OPEN = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()


@dataclass
class Order:
    order_id: str
    side: Side
    order_type: OrderType
    price: Optional[float]       # None for market orders
    quantity: float
    remaining: float = field(init=False)
    status: Status = field(init=False, default=Status.OPEN)
    timestamp: float = field(default_factory=time.time)
    # Optional metadata
    symbol: str = "AAAA"
    trader_id: str = ""

    def __post_init__(self):
        self.remaining = self.quantity

    @classmethod
    def limit(cls, side: Side, price: float, qty: float, **kw) -> "Order":
        return cls(
            order_id=kw.pop("order_id", str(uuid.uuid4())),
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            quantity=qty,
            **kw,
        )

    @classmethod
    def market(cls, side: Side, qty: float, **kw) -> "Order":
        return cls(
            order_id=kw.pop("order_id", str(uuid.uuid4())),
            side=side,
            order_type=OrderType.MARKET,
            price=None,
            quantity=qty,
            **kw,
        )


@dataclass
class Fill:
    aggressor_id: str        # order that triggered the match
    passive_id: str          # resting order
    price: float
    quantity: float
    side: Side               # side of the aggressor
    timestamp: float = field(default_factory=time.time)


@dataclass
class BookStats:
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    bid_depth: int           # number of price levels
    ask_depth: int
    total_bid_qty: float
    total_ask_qty: float


# ---------------------------------------------------------------------------
# Limit Order Book
# ---------------------------------------------------------------------------

class LimitOrderBook:
    """
    Price-time priority limit order book.

    bids: SortedDict with negated prices as keys so peekitem(0) → best bid.
    asks: SortedDict with positive prices as keys so peekitem(0) → best ask.
    """

    def __init__(self, symbol: str = "AAAA"):
        self.symbol = symbol
        # price → deque[Order]
        # bids stored with negated keys for descending order
        self._bids: SortedDict[float, deque[Order]] = SortedDict()
        self._asks: SortedDict[float, deque[Order]] = SortedDict()
        # order_id → Order (for cancel lookup)
        self._orders: dict[str, tuple[SortedDict, float]] = {}
        self.fills: list[Fill] = []
        self._fill_callbacks: list = []

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def add_fill_callback(self, fn):
        """Register a callable(Fill) that fires on each match."""
        self._fill_callbacks.append(fn)

    def submit(self, order: Order) -> list[Fill]:
        """
        Route an order to the appropriate handler.
        Returns a list of Fill events generated.
        """
        if order.order_type == OrderType.CANCEL:
            self.cancel(order.order_id)
            return []
        elif order.order_type == OrderType.LIMIT:
            return self._process_limit(order)
        elif order.order_type == OrderType.MARKET:
            return self._process_market(order)
        raise ValueError(f"Unknown order type: {order.order_type}")

    def cancel(self, order_id: str) -> bool:
        """
        Cancel a resting limit order. Returns True if found and removed.
        """
        if order_id not in self._orders:
            return False
        book_side, key = self._orders.pop(order_id)
        level = book_side.get(key)
        if level is None:
            return False
        # Remove from deque (O(n) within level, typically tiny)
        new_deque: deque[Order] = deque()
        removed = False
        for o in level:
            if o.order_id == order_id:
                o.status = Status.CANCELLED
                removed = True
            else:
                new_deque.append(o)
        if new_deque:
            book_side[key] = new_deque
        else:
            del book_side[key]
        return removed

    def best_bid(self) -> Optional[float]:
        """Highest resting buy price, or None."""
        if not self._bids:
            return None
        neg_price, _ = self._bids.peekitem(0)
        return -neg_price

    def best_ask(self) -> Optional[float]:
        """Lowest resting sell price, or None."""
        if not self._asks:
            return None
        price, _ = self._asks.peekitem(0)
        return price

    def stats(self) -> BookStats:
        bb = self.best_bid()
        ba = self.best_ask()
        return BookStats(
            best_bid=bb,
            best_ask=ba,
            spread=(ba - bb) if (bb and ba) else None,
            bid_depth=len(self._bids),
            ask_depth=len(self._asks),
            total_bid_qty=sum(
                sum(o.remaining for o in q) for q in self._bids.values()
            ),
            total_ask_qty=sum(
                sum(o.remaining for o in q) for q in self._asks.values()
            ),
        )

    def bid_levels(self) -> list[tuple[float, float]]:
        """Return [(price, total_qty)] sorted best-to-worst bid."""
        return [
            (-k, sum(o.remaining for o in q))
            for k, q in self._bids.items()
        ]

    def ask_levels(self) -> list[tuple[float, float]]:
        """Return [(price, total_qty)] sorted best-to-worst ask."""
        return [
            (k, sum(o.remaining for o in q))
            for k, q in self._asks.items()
        ]

    # ------------------------------------------------------------------ #
    # Internal matching                                                    #
    # ------------------------------------------------------------------ #

    def _process_limit(self, order: Order) -> list[Fill]:
        fills: list[Fill] = []
        if order.side == Side.BUY:
            fills = self._match_buy(order, limit_price=order.price)
        else:
            fills = self._match_sell(order, limit_price=order.price)
        # If not fully filled, rest on the book
        if order.remaining > 0 and order.status not in (
            Status.FILLED, Status.CANCELLED
        ):
            self._rest(order)
        return fills

    # Guard: if book is empty on market order side, remaining qty is marked PARTIALLY_FILLED and discarded — previously could loop indefinitely on thin books
    def _process_market(self, order: Order) -> list[Fill]:
        """Market order: match at any price, no resting."""
        if order.side == Side.BUY:
            fills = self._match_buy(order, limit_price=float("inf"))
        else:
            fills = self._match_sell(order, limit_price=0.0)
        if order.remaining > 0:
            order.status = Status.PARTIALLY_FILLED  # unmatched portion lost
        return fills

    def _match_buy(self, aggressor: Order, limit_price: float) -> list[Fill]:
        fills: list[Fill] = []
        while aggressor.remaining > 0 and self._asks:
            best_ask_price, level = self._asks.peekitem(0)
            if best_ask_price > limit_price:
                break
            fills.extend(self._drain_level(aggressor, level, best_ask_price, self._asks))
        return fills

    def _match_sell(self, aggressor: Order, limit_price: float) -> list[Fill]:
        fills: list[Fill] = []
        while aggressor.remaining > 0 and self._bids:
            neg_best_bid, level = self._bids.peekitem(0)
            best_bid_price = -neg_best_bid
            if best_bid_price < limit_price:
                break
            fills.extend(self._drain_level(aggressor, level, best_bid_price, self._bids, neg_key=True))
        return fills

    def _drain_level(
        self,
        aggressor: Order,
        level: deque[Order],
        exec_price: float,
        book_side: SortedDict,
        neg_key: bool = False,
    ) -> list[Fill]:
        """Match aggressor against all resting orders at this price level."""
        fills: list[Fill] = []
        key = -exec_price if neg_key else exec_price

        while level and aggressor.remaining > 0:
            passive = level[0]
            trade_qty = min(aggressor.remaining, passive.remaining)

            aggressor.remaining -= trade_qty
            passive.remaining -= trade_qty

            fill = Fill(
                aggressor_id=aggressor.order_id,
                passive_id=passive.order_id,
                price=exec_price,
                quantity=trade_qty,
                side=aggressor.side,
            )
            fills.append(fill)
            self.fills.append(fill)
            for cb in self._fill_callbacks:
                cb(fill)

            if passive.remaining == 0:
                passive.status = Status.FILLED
                level.popleft()
                self._orders.pop(passive.order_id, None)

        # Clean up empty price level
        if not level:
            del book_side[key]

        if aggressor.remaining == 0:
            aggressor.status = Status.FILLED
        elif aggressor.quantity > aggressor.remaining:
            aggressor.status = Status.PARTIALLY_FILLED

        return fills

    def _rest(self, order: Order):
        """Place an unmatched limit order onto the resting book."""
        if order.side == Side.BUY:
            key = -order.price  # type: ignore[operator]
            book = self._bids
        else:
            key = order.price  # type: ignore[assignment]
            book = self._asks

        if key not in book:
            book[key] = deque()
        book[key].append(order)
        self._orders[order.order_id] = (book, key)
