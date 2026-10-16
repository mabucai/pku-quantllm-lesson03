"""不依赖课程文件的小样本正确性测试；全量对账在 analyze.py 中执行。"""
import random
import unittest
import numpy as np
from book import Book, apply_trade, demo
from analyze import ms, cents, continuous, normalize
import pandas as pd


class MatchingTests(unittest.TestCase):
    def test_price_and_fifo(self):
        b = Book()
        b.add(1, "S", 1001, 200)
        b.add(2, "S", 1000, 100)
        b.add(3, "S", 1000, 300)
        self.assertEqual(b.submit(4, "B", 1001, 500), [
            dict(buy=4, sell=2, price=1000, qty=100),
            dict(buy=4, sell=3, price=1000, qty=300),
            dict(buy=4, sell=1, price=1001, qty=100)])
        b.audit()

    def test_sell_hits_highest_bid(self):
        b = Book()
        b.add(1, "B", 1001, 200)
        b.add(2, "B", 1002, 200)
        self.assertEqual(b.submit(3, "S", 1000, 100)[0], dict(buy=2, sell=3, price=1002, qty=100))

    def test_limit_protection(self):
        b = Book()
        b.add(1, "S", 1001, 100)
        self.assertEqual(b.submit(2, "B", 1000, 200), [])
        self.assertEqual(b.depth("B"), [[1000, 200]])

    def test_partial_fill_cancel(self):
        b = Book()
        b.add(1, "S", 1000, 100)
        self.assertEqual(sum(t["qty"] for t in b.submit(2, "B", 1000, 300)), 100)
        self.assertEqual(b.orders[2].qty, 200)
        b.cancel(2)
        self.assertEqual(b.depth("B"), [])
        b.audit()

    def test_validation_before_mutation(self):
        b = Book()
        for args in [(1, "X", 1000, 1), (1, "B", 10.0, 1), (1, "B", 1000, 0)]:
            with self.assertRaises(ValueError):
                b.submit(*args)
        self.assertEqual(b.orders, {})

    def test_duplicate_even_filled(self):
        b = Book()
        b.add(1, "S", 1000, 100)
        b.submit(2, "B", 1000, 100)
        with self.assertRaises(ValueError):
            b.submit(2, "B", 1000, 100)

    def test_reduction_guards(self):
        b = Book()
        b.add(1, "B", 1000, 100)
        for oid, qty, side in [(9,100,"B"), (1,101,"B"), (1,100,"S"), (1,0,"B")]:
            with self.assertRaises(ValueError):
                b.reduce(oid, qty, side)
        self.assertEqual(b.orders[1].qty, 100)

    def test_random_quantity_conservation(self):
        rng = random.Random(20260923)
        b = Book()
        submitted = executed = cancelled = 0
        for oid in range(1, 1001):
            qty = rng.randint(1, 20) * 100
            submitted += qty
            executed += sum(t["qty"] for t in b.submit(oid, rng.choice(["B","S"]), rng.randint(990,1010), qty))
            if b.orders and rng.random() < 0.3:
                target = rng.choice(list(b.orders))
                cancelled += b.orders[target].qty
                b.cancel(target)
            b.audit()
            self.assertEqual(submitted, 2*executed+cancelled+sum(o.qty for o in b.orders.values()))
            if b.top("B") and b.top("S"):
                self.assertLess(b.top("B"), b.top("S"))

    def test_demo_expected_amount(self):
        states = demo()
        self.assertEqual(sum(t["qty"] for t in states[3]["fills"]), 900)
        self.assertEqual(states[-1]["orders"], [])


class ReplayTests(unittest.TestCase):
    def test_trade_then_residual_add(self):
        b = Book()
        b.add(1, "S", 1000, 1000)
        self.assertEqual(apply_trade(b, 2, 1, 66, 300), 1)
        b.add(2, "B", 1000, 200)
        self.assertEqual(b.orders[1].qty, 700)
        self.assertEqual(b.orders[2].qty, 200)
        b.audit()

    def test_missing_passive_is_error(self):
        with self.assertRaises(ValueError):
            apply_trade(Book(), 1, 2, 66, 100)

    def test_auction_history_both_sides(self):
        b = Book()
        b.add(1,"B",1010,300)
        b.add(2,"S",990,500)
        apply_trade(b,1,2,0,200)
        self.assertEqual((b.orders[1].qty,b.orders[2].qty),(100,300))
        b.audit()

    def test_time_encoding(self):
        self.assertEqual(ms(93000030), 34200030)
        self.assertEqual(ms(130000000)-ms(113000000),5400000)
        self.assertFalse(continuous(120000000))
        self.assertFalse(continuous(145700000))

    def test_float_price_rounding(self):
        self.assertEqual(cents(np.array([40.830002,18.209999],dtype="float32")).tolist(),[4083,1821])

    def test_business_sequence_order(self):
        o = pd.DataFrame([dict(Channel=1001,BizIndex=2,Time=93000000,OrderKind=65,FunctionCode=66,OrderOriNo=7,Price=10.,Volume=200)])
        e = pd.DataFrame([dict(Channel=1001,BizIndex=1,Time=93000000,BSFlag=66,BidOrder=7,AskOrder=8,Price=10.,Volume=100)])
        self.assertEqual(normalize(o,e).kind.tolist(),[84,65])
        e.loc[0,"BizIndex"]=2
        with self.assertRaises(ValueError):
            normalize(o,e)


class AuctionTests(unittest.TestCase):
    def test_no_cross(self):
        b=Book(); b.add(1,"B",999,100); b.add(2,"S",1000,100)
        self.assertEqual(b.auction(),[])

    def test_midpoint_round_up(self):
        b=Book(); b.add(1,"B",1001,100); b.add(2,"S",1000,100)
        self.assertEqual(b.auction(),[dict(buy=1,sell=2,price=1001,qty=100)])
        b.audit()

    def test_max_volume_and_imbalance(self):
        b=Book(); b.add(1,"B",1010,100); b.add(2,"B",1000,500)
        b.add(3,"S",990,300); b.add(4,"S",1000,100)
        trades=b.auction()
        self.assertEqual({t["price"] for t in trades},{1000})
        self.assertEqual(sum(t["qty"] for t in trades),400)
        self.assertEqual(trades[0]["buy"],1)
        self.assertEqual(b.orders[2].qty,200)
        b.audit()


if __name__ == "__main__":
    unittest.main()
