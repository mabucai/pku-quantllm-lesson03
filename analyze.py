"""课程数据全量回放、快照核对、微观结构小实验。运行方式见 README。"""
import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq

from book import Book, apply_trade, demo


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def ms(t):
    return t // 10000000 * 3600000 + (t // 100000 % 100) * 60000 + (t // 1000 % 100) * 1000 + t % 1000


def clock(t):
    t = int(t)
    return f"{t//10000000:02d}:{t//100000%100:02d}:{t//1000%100:02d}.{t%1000:03d}"


def continuous(t):
    return ((t >= 93000000) & (t < 113000000)) | ((t >= 130000000) & (t < 145700000))


def cents(x):
    return np.rint(np.asarray(x, dtype=np.float64) * 100).astype(np.int64)


def profile(files):
    result = {}
    for kind, path in files.items():
        p = pq.ParquetFile(path)
        with path.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        result[kind] = dict(file=path.name, bytes=path.stat().st_size, rows=p.metadata.num_rows,
                            sha256=digest, schema={field.name: str(field.type) for field in p.schema_arrow})
    return result


def normalize(o, e):
    channels = o.Channel.unique()
    if len(channels) != 1 or not set(e.Channel.unique()) <= set(channels):
        raise ValueError("本适配器要求单证券单竞价通道；新数据需要重新确认字段")
    a = pd.DataFrame(dict(seq=o.BizIndex, time=o.Time, kind=o.OrderKind, side=o.FunctionCode,
                          oid=o.OrderOriNo, bid=0, ask=0, price=cents(o.Price), qty=o.Volume))
    b = pd.DataFrame(dict(seq=e.BizIndex, time=e.Time, kind=84, side=e.BSFlag,
                          oid=0, bid=e.BidOrder, ask=e.AskOrder, price=cents(e.Price), qty=e.Volume))
    events = pd.concat([a, b], ignore_index=True).sort_values("seq", kind="stable").astype("int64")
    if events.seq.duplicated().any():
        raise ValueError("duplicate BizIndex within symbol/channel")
    return events


def snap_depth(row, side):
    prefix = "Bid" if side == "B" else "Ask"
    return [[int(round(getattr(row, f"{prefix}Price{i}") * 100)), int(getattr(row, f"{prefix}Volume{i}"))]
            for i in range(1, 11) if getattr(row, f"{prefix}Price{i}") > 0 and getattr(row, f"{prefix}Volume{i}") > 0]


def opening_check(o, e):
    target = e[(e.BSFlag == 0) & (e.Time < 93000000)].sort_values("BizIndex")
    if target.empty:
        return {"tested": False}
    b = Book()
    for r in o[o.BizIndex < target.BizIndex.min()].sort_values("BizIndex").itertuples(index=False):
        if r.OrderKind == 65:
            b.add(int(r.OrderOriNo), chr(r.FunctionCode), int(round(r.Price * 100)), int(r.Volume))
        elif r.OrderKind == 68:
            b.reduce(int(r.OrderOriNo), int(r.Volume), chr(r.FunctionCode))
    generated = b.auction()
    b.audit()
    expected = [(int(r.BidOrder), int(r.AskOrder), int(round(r.Price * 100)), int(r.Volume)) for r in target.itertuples(index=False)]
    got = [(r["buy"], r["sell"], r["price"], r["qty"]) for r in generated]
    return {"tested": True, "pairs_equal": got == expected, "trades": len(expected),
            "volume": sum(r[3] for r in expected), "price_cents": expected[0][2]}


def replay(symbol, o, e, s, out):
    # Channel=2301 is the observed after-hours trade stream, with no order feed.
    after = e[e.Channel == 2301]
    regular = e[e.Channel != 2301]
    events = normalize(o, regular)
    time_values = events.time.to_numpy()
    regressions = int((np.diff(time_values) < 0).sum())
    # Never reorder by wall time. A snapshot consumes only an eligible business-sequence prefix.
    watermark = np.maximum.accumulate(ms(time_values))
    sn = s[continuous(s.Time) | (s.Time >= 150000000)].sort_values("Time").reset_index(drop=True)
    ends = np.searchsorted(watermark, ms(sn.Time.to_numpy()), side="right")
    rows = list(sn.itertuples(index=False))
    it = iter(events.itertuples(index=False, name=None))
    b = Book()
    done = volume = amount_cents = trade_count = active_absent = 0
    last_seq = 0
    checks, frames = [], []
    last_minute = None
    for row, end in zip(rows, ends):
        while done < end:
            seq, t, kind, side, oid, bid, ask, price, qty = next(it)
            if kind == 65:
                b.add(oid, chr(side), price, qty)
            elif kind == 68:
                b.reduce(oid, qty, chr(side))
            elif kind == 84:
                active_absent += apply_trade(b, bid, ask, side, qty)
                volume += qty
                amount_cents += price * qty
                trade_count += 1
            elif kind != 83:
                raise ValueError(f"unknown event {kind}")
            done += 1
            last_seq = seq
        bids, asks = b.depth("B"), b.depth("S")
        sb, sa = snap_depth(row, "B"), snap_depth(row, "S")
        best = (bids[:1] and asks[:1] and sb[:1] and sa[:1] and bids[0][0] == sb[0][0] and asks[0][0] == sa[0][0])
        prices_equal = [x[0] for x in bids] == [x[0] for x in sb] and [x[0] for x in asks] == [x[0] for x in sa]
        full_equal = bids == sb and asks == sa
        checks.append(dict(Symbol=symbol, Time=int(row.Time), BizIndex=last_seq,
                           best_equal=bool(best), prices_equal=prices_equal, depth_equal=full_equal,
                           volume_equal=volume == row.AccVolume, replay_volume=volume, snapshot_volume=int(row.AccVolume),
                           turnover_error_yuan=round(amount_cents / 100 - row.AccTurnover, 2)))
        minute = row.Time // 100000
        if symbol in (600036, 601688, 688981) and minute != last_minute:
            frames.append(dict(time=clock(row.Time), seq=last_seq, bids=bids, asks=asks,
                               snapshot_bids=sb, snapshot_asks=sa, equal=full_equal,
                               volume=volume, snapshot_volume=int(row.AccVolume)))
            last_minute = minute
    # End-of-day statuses may follow the final comparable snapshot.
    for seq, t, kind, side, oid, bid, ask, price, qty in it:
        if kind == 65:
            b.add(oid, chr(side), price, qty)
        elif kind == 68:
            b.reduce(oid, qty, chr(side))
        elif kind == 84:
            active_absent += apply_trade(b, bid, ask, side, qty)
            volume += qty
            amount_cents += price * qty
            trade_count += 1
        elif kind != 83:
            raise ValueError(f"unknown event {kind}")
    b.audit()
    df = pd.DataFrame(checks)
    df.to_csv(out / f"checks_{symbol}.csv", index=False, encoding="utf-8-sig")
    last = s.sort_values("Time").iloc[-1]
    final_bids, final_asks = b.depth("B"), b.depth("S")
    summary = dict(symbol=symbol, events=len(events), orders=int((o.OrderKind == 65).sum()),
                   cancellations=int((o.OrderKind == 68).sum()), trades=len(regular), after_trades=len(after),
                   time_regressions=regressions, active_absent_trade_rows=active_absent,
                   snapshots=len(df), best_matches=int(df.best_equal.sum()), prices_matches=int(df.prices_equal.sum()),
                   depth_matches=int(df.depth_equal.sum()), volume_matches=int(df.volume_equal.sum()),
                   final_depth_equal=final_bids == snap_depth(last, "B") and final_asks == snap_depth(last, "S"),
                   final_volume=volume, final_volume_equal=volume == int(last.AccVolume),
                   final_turnover_error_yuan=round(amount_cents/100-int(last.AccTurnover), 2),
                   after_volume=int(after.Volume.sum()), after_volume_equal=int(after.Volume.sum()) == int(last.AfterVolume),
                   audit_passed=True, opening=opening_check(o, regular))
    return summary, frames


def microstructure(s, e, out):
    """固定假设：60秒决策网格，信号延迟3秒，30秒未来标签，早/下午分开。

    不调阈值、不拟合参数；mid收益和按对手报价的纸面价差分别报告。
    """
    statistics, experiments, minute_volume = [], [], []
    valid = s[continuous(s.Time) & (s.BidPrice1 > 0) & (s.AskPrice1 > s.BidPrice1)].copy()
    valid["BidPrice1"] = cents(valid.BidPrice1) / 100
    valid["AskPrice1"] = cents(valid.AskPrice1) / 100
    valid["spread_bps"] = (valid.AskPrice1-valid.BidPrice1) / ((valid.AskPrice1+valid.BidPrice1)/2) * 10000
    valid["spread_cents"] = cents(valid.AskPrice1) - cents(valid.BidPrice1)
    for symbol, x in valid.groupby("Symbol", sort=True):
        x = x.sort_values("Time")
        statistics.append(dict(symbol=int(symbol), snapshots=len(x), median_spread_bps=float(x.spread_bps.median()),
                               median_mid=float(((x.BidPrice1+x.AskPrice1)/2).median()),
                               one_tick_pct=float((x.spread_cents == 1).mean()*100),
                               median_best_depth=float(((x.BidVolume1+x.AskVolume1)/2).median())))
        for name, lo, hi in [("上午", 34200000, 41400000), ("下午", 46800000, 53820000)]:
            y = x[(ms(x.Time) >= lo) & (ms(x.Time) < hi)].copy()
            ts = ms(y.Time.to_numpy())
            if len(ts) < 3:
                continue
            bid, ask = y.BidPrice1.to_numpy(dtype=float), y.AskPrice1.to_numpy(dtype=float)
            bv, av = y.BidVolume1.to_numpy(), y.AskVolume1.to_numpy()
            mid = (bid+ask)/2
            for decision in range(lo+30000, hi-30000, 60000):
                # Latest signal available as of decision-3s; current quote only as of decision.
                signal = int(np.searchsorted(ts, decision-3000, side="right")-1)
                entry = int(np.searchsorted(ts, decision, side="right")-1)
                future = int(np.searchsorted(ts, decision+30000, side="left"))
                if signal < 0 or entry < 0 or future >= len(ts):
                    continue
                if decision-3000-ts[signal] > 6000 or decision-ts[entry] > 6000 or ts[future]-(decision+30000) > 6000:
                    continue
                imbalance = (bv[signal]-av[signal])/(bv[signal]+av[signal])
                if not np.isfinite(imbalance):
                    continue
                group = "买盘较厚" if imbalance >= 0.5 else "卖盘较厚" if imbalance <= -0.5 else "中性"
                experiments.append(dict(symbol=int(symbol), session=name, decision_ms=decision,
                                        signal_time=int(y.Time.iloc[signal]), entry_time=int(y.Time.iloc[entry]),
                                        exit_time=int(y.Time.iloc[future]), imbalance=float(imbalance), group=group,
                                        future_mid_bps=float((mid[future]/mid[entry]-1)*10000),
                                        paper_buy_sell_bps=float((bid[future]/ask[entry]-1)*10000)))
    stats = pd.DataFrame(statistics)
    exp = pd.DataFrame(experiments)
    stats.to_csv(out/"liquidity_by_symbol.csv", index=False, encoding="utf-8-sig")
    exp.to_csv(out/"signal_observations.csv", index=False, encoding="utf-8-sig")
    agg = exp.groupby(["session", "group"]).agg(n=("symbol", "size"), mid_bps=("future_mid_bps", "mean"),
                                                paper_bps=("paper_buy_sell_bps", "mean")).reset_index()
    agg.to_csv(out/"signal_groups.csv", index=False, encoding="utf-8-sig")
    regular = e[e.Channel != 2301].copy()
    regular["minute"] = regular.Time // 100000
    vol = regular.groupby("minute").Volume.sum()
    vol.to_csv(out/"volume_by_minute.csv", encoding="utf-8-sig")
    return dict(valid_snapshots=len(valid), median_spread_bps=float(valid.spread_bps.median()),
                one_tick_pct=float((valid.spread_cents == 1).mean()*100),
                signal_groups=agg.to_dict("records"), statistics=statistics,
                volume_minutes={str(int(k)): int(v) for k,v in vol.items()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent/"output")
    parser.add_argument("--symbols", type=int, nargs="+", help="默认全部；可用600036快速运行")
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    files = {}
    for kind in ("ord", "exe", "snp"):
        matches = list(args.data_dir.glob(f"{kind}_*.parquet"))
        if len(matches) != 1:
            raise ValueError(f"需要唯一的 {kind}_*.parquet")
        files[kind] = matches[0]
    if len({f.stem.split("_")[-1] for f in files.values()}) != 1:
        raise ValueError("三份数据的文件日期必须一致")
    if next(iter(files.values())).stem.split("_")[-1] != "20260923":
        raise ValueError("本课程报告适配20260923数据；其他日期需要另行验证语义")
    prof = profile(files)
    dump(out/"data_profile.json", prof)
    o, e, s = [pq.read_table(files[k]).to_pandas() for k in ("ord", "exe", "snp")]
    if not set(o.Symbol) == set(e.Symbol) == set(s.Symbol):
        raise ValueError("三份数据的证券集合不一致")
    if o.isna().any().any() or e.isna().any().any():
        raise ValueError("逐笔数据存在缺失值，需先确定处理口径")
    symbols = sorted(set(o.Symbol) & set(e.Symbol) & set(s.Symbol))
    if args.symbols:
        if not set(args.symbols) <= set(symbols):
            raise ValueError("requested symbol absent")
        symbols = sorted(set(args.symbols))
    og, eg, sg = o.groupby("Symbol"), e.groupby("Symbol"), s.groupby("Symbol")
    summaries, frames = [], {}
    for i, symbol in enumerate(symbols, 1):
        result, sample = replay(int(symbol), og.get_group(symbol), eg.get_group(symbol), sg.get_group(symbol), out)
        summaries.append(result)
        if sample:
            frames[str(symbol)] = sample
        print(f"[{i}/{len(symbols)}] {symbol}: final={result['final_depth_equal']}, intraday={result['depth_matches']}/{result['snapshots']}, opening={result['opening'].get('pairs_equal')}", flush=True)
    research = microstructure(s[s.Symbol.isin(symbols)], e[e.Symbol.isin(symbols)], out)
    # Preserve an instructive local example instead of copying another student's result.
    example = o[(o.Symbol == 600036) & (o.OrderOriNo == 299104)]
    example_trades = e[(e.Symbol == 600036) & ((e.BidOrder == 299104) | (e.AskOrder == 299104))].sort_values("BizIndex").head(10)
    example.to_csv(out/"example_order_299104.csv", index=False, encoding="utf-8-sig")
    example_trades.to_csv(out/"example_trades_299104.csv", index=False, encoding="utf-8-sig")
    summary = dict(date=next(iter(files.values())).stem.split("_")[-1], symbols=len(symbols),
                   source="https://github.com/aslan9/pku_quantllm/issues/6", profile=prof,
                   replay=summaries, research=research,
                   elapsed_seconds=round(time.perf_counter()-start, 2),
                   environment=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, pyarrow=pyarrow.__version__),
                   alignment="business-sequence prefix with cumulative max event timestamp <= snapshot timestamp; no window search or snapshot overwrite")
    dump(out/"summary.json", summary)
    dump(out/"replay_frames.json", frames)
    dump(out/"matching_demo.json", demo())
    from build_report import build
    build(out)
    print(f"Done in {summary['elapsed_seconds']} s: {out/'report.html'}", flush=True)


if __name__ == "__main__":
    main()
