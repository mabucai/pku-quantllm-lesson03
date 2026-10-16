"""python run_demo.py [--orders examples/orders.json] [--auction]"""
import argparse
import json
from pathlib import Path
from book import Book, demo


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--orders", type=Path, help='JSON事件数组，type为order或cancel')
    p.add_argument("--auction", action="store_true", help="收齐委托后统一集合撮合")
    args=p.parse_args()
    if not args.orders:
        result=demo()
    else:
        book=Book()
        result=[]
        for event in json.loads(args.orders.read_text(encoding="utf-8-sig")):
            if event["type"]=="cancel":
                book.cancel(event["id"])
                fills=[]
            elif event["type"]=="order":
                values=(event["id"],event["side"],event["price_cents"],event["qty"])
                if args.auction:
                    book.add(*values)
                    fills=[]
                else:
                    fills=book.submit(*values)
            else:
                raise ValueError("未知事件类型")
            book.audit()
            result.append(dict(input=event,fills=fills,bids=book.depth("B"),asks=book.depth("S")))
        if args.auction:
            fills=book.auction()
            book.audit()
            result.append(dict(auction_fills=fills,bids=book.depth("B"),asks=book.depth("S")))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
