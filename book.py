"""教学用订单簿：整数分、整数股、价格优先、同价按插入顺序。

历史回放只用 add/reduce；独立连续撮合用 submit。两条路径不得混用。
这里只处理已被接受的限价单，不包含券商风控、账户交收或市价指令。
"""
from bisect import bisect_left, bisect_right, insort
from itertools import accumulate
from dataclasses import dataclass


@dataclass(slots=True)
class Order:
    oid: int
    side: str
    price: int
    qty: int


class Book:
    def __init__(self):
        self.orders = {}
        self.seen = set()
        self.levels = {"B": {}, "S": {}}
        self.prices = {"B": [], "S": []}  # both ascending
        self.quantities = {"B": {}, "S": {}}
        self.added = {"B": 0, "S": 0}
        self.removed = {"B": 0, "S": 0}

    def add(self, oid, side, price, qty):
        if side not in ("B", "S") or not all(type(x) is int and x > 0 for x in (oid, price, qty)):
            raise ValueError("正整数订单号、整数分价格、整数股数量及 B/S 方向必填")
        if oid in self.seen:
            raise ValueError(f"duplicate order {oid}")
        self.seen.add(oid)
        order = Order(oid, side, price, qty)
        self.orders[oid] = order
        if price not in self.levels[side]:
            self.levels[side][price] = {}
            self.quantities[side][price] = 0
            insort(self.prices[side], price)
        self.levels[side][price][oid] = order
        self.quantities[side][price] += qty
        self.added[side] += qty

    def reduce(self, oid, qty, expected_side=None):
        if oid not in self.orders:
            raise ValueError(f"missing order {oid}")
        o = self.orders[oid]
        if type(qty) is not int or qty <= 0 or qty > o.qty:
            raise ValueError(f"invalid reduction {oid}: {qty} > {o.qty}")
        if expected_side is not None and expected_side != o.side:
            raise ValueError(f"side mismatch {oid}")
        o.qty -= qty
        self.quantities[o.side][o.price] -= qty
        self.removed[o.side] += qty
        if not o.qty:
            del self.orders[oid]
            del self.levels[o.side][o.price][oid]
        if self.quantities[o.side][o.price] == 0:
            del self.quantities[o.side][o.price]
            del self.levels[o.side][o.price]
            self.prices[o.side].remove(o.price)

    def cancel(self, oid):
        self.reduce(oid, self.orders[oid].qty)

    def depth(self, side, n=10):
        prices = self.prices[side][-n:][::-1] if side == "B" else self.prices[side][:n]
        return [[p, self.quantities[side][p]] for p in prices]

    def top(self, side):
        ps = self.prices[side]
        return ps[-1 if side == "B" else 0] if ps else None

    def submit(self, oid, side, price, qty):
        """独立限价单；成交价为已在簿中的被动订单价格。"""
        if side not in ("B", "S") or not all(type(x) is int and x > 0 for x in (oid, price, qty)):
            raise ValueError("invalid limit order")
        if oid in self.seen:
            raise ValueError(f"duplicate order {oid}")
        other = "S" if side == "B" else "B"
        fills = []
        while qty and self.prices[other]:
            px = self.top(other)
            if (side == "B" and px > price) or (side == "S" and px < price):
                break
            passive = next(iter(self.levels[other][px].values()))
            amount = min(qty, passive.qty)
            fills.append({"buy": oid if side == "B" else passive.oid,
                          "sell": passive.oid if side == "B" else oid,
                          "price": px, "qty": amount})
            self.reduce(passive.oid, amount)
            qty -= amount
        if qty:
            self.add(oid, side, price, qty)
        else:
            self.seen.add(oid)
        return fills

    def audit(self):
        for side in ("B", "S"):
            expected = {}
            for o in self.orders.values():
                if o.side == side:
                    assert o.qty > 0
                    expected[o.price] = expected.get(o.price, 0) + o.qty
            assert expected == self.quantities[side]
            assert sorted(expected) == self.prices[side]
            assert sum(expected.values()) == self.added[side] - self.removed[side]
            assert sum(len(q) for q in self.levels[side].values()) == sum(o.side == side for o in self.orders.values())

    def auction(self):
        """独立集合竞价；候选为申报价，按上交所3.5.2/3.5.4定价。

        只从当前委托簿算价，不读取真实成交价格。无交叉时不成交。
        """
        if not self.prices["B"] or not self.prices["S"] or self.top("B") < self.top("S"):
            return []
        bp, sp = self.prices["B"], self.prices["S"]
        bc = [0] + list(accumulate(self.quantities["B"][p] for p in bp))
        sc = [0] + list(accumulate(self.quantities["S"][p] for p in sp))
        candidates = []
        for p in sorted(set(bp + sp)):
            buy = bc[-1] - bc[bisect_left(bp, p)]
            sell = sc[bisect_right(sp, p)]
            qty = min(buy, sell)
            better_buy = bc[-1] - bc[bisect_right(bp, p)]
            better_sell = sc[bisect_left(sp, p)]
            if qty > 0 and better_buy <= qty and better_sell <= qty:
                candidates.append((qty, -abs(buy - sell), p))
        best = max((q, d) for q, d, _ in candidates)
        choices = [p for q, d, p in candidates if (q, d) == best]
        px = (min(choices) + max(choices) + 1) // 2  # positive half-up
        remaining = best[0]
        fills = []
        while remaining:
            b = next(iter(self.levels["B"][self.top("B")].values()))
            s = next(iter(self.levels["S"][self.top("S")].values()))
            assert b.price >= px >= s.price
            qty = min(b.qty, s.qty, remaining)
            fills.append({"buy": b.oid, "sell": s.oid, "price": px, "qty": qty})
            self.reduce(b.oid, qty)
            self.reduce(s.oid, qty)
            remaining -= qty
        return fills


def apply_trade(book, bid, ask, flag, qty):
    """flag=0 集合成交扣双方；连续成交必须找到被动方。

    主动方若未披露 ADD，不补造挂单，也不把其缺失算作数据丢失。
    返回未入簿主动方的数量（按成交条数计）。
    """
    if flag == 0:
        book.reduce(bid, qty, "B")
        book.reduce(ask, qty, "S")
        return 0
    if flag not in (66, 83):
        raise ValueError(f"unknown BSFlag {flag}")
    passive, active = (ask, bid) if flag == 66 else (bid, ask)
    book.reduce(passive, qty, "S" if flag == 66 else "B")
    if active in book.orders:
        book.reduce(active, qty, "B" if flag == 66 else "S")
        return 0
    return 1


def demo():
    b = Book()
    states = []
    def save(text, fills=None):
        b.audit()
        states.append({"text": text, "bids": b.depth("B"), "asks": b.depth("S"),
                       "fills": fills or [], "orders": [dict(oid=o.oid, side=o.side, price=o.price, qty=o.qty) for o in b.orders.values()]})
    b.submit(1, "S", 1000, 300)
    save("卖单1：10.00元 × 300股，先到达")
    b.submit(2, "S", 1000, 400)
    save("卖单2：10.00元 × 400股，同价排在单1后")
    b.submit(3, "S", 1001, 500)
    save("卖单3：10.01元 × 500股，价格更高")
    f = b.submit(4, "B", 1001, 900)
    save("买单4：限价10.01元买900股；先吃单1的300、单2的400，再吃单3的200", f)
    f = b.submit(5, "B", 1001, 500)
    save("买单5：限价10.01元买500股；成交300，剩余200挂买盘", f)
    b.cancel(5)
    save("撤销买单5剩余200股；已成交300股不会被撤销")
    return states
