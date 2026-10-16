"""核查保存的CSV/JSON是否自洽，以及研究取样是否使用未来信号。"""
import argparse
import json
import unittest
from pathlib import Path
import pandas as pd
from analyze import ms


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path(__file__).parent/"output")
    args=p.parse_args()
    out=args.output
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern="test_*.py")
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    summary=json.loads((out/"summary.json").read_text(encoding="utf-8"))
    assert len(summary["replay"])==summary["symbols"]
    for r in summary["replay"]:
        assert r["final_depth_equal"] and r["final_volume_equal"] and r["after_volume_equal"] and r["audit_passed"]
        assert r["opening"]["pairs_equal"]
        assert abs(r["final_turnover_error_yuan"]) <= 1
        checks=pd.read_csv(out/f"checks_{r['symbol']}.csv")
        assert len(checks)==r["snapshots"]
        for column,total in [("best_equal","best_matches"),("prices_equal","prices_matches"),("depth_equal","depth_matches"),("volume_equal","volume_matches")]:
            assert checks[column].sum()==r[total]
    obs=pd.read_csv(out/"signal_observations.csv")
    assert (ms(obs.signal_time)<=obs.decision_ms-3000).all()
    assert (ms(obs.entry_time)<=obs.decision_ms).all()
    assert (ms(obs.exit_time)>=obs.decision_ms+30000).all()
    assert (obs.decision_ms-3000-ms(obs.signal_time)<=6000).all()
    assert (obs.decision_ms-ms(obs.entry_time)<=6000).all()
    assert (ms(obs.exit_time)-obs.decision_ms-30000<=6000).all()
    assert ((ms(obs.entry_time)<41400000)==(ms(obs.exit_time)<41400000)).all()
    assert not obs.duplicated(["symbol","decision_ms"]).any()
    saved=pd.read_csv(out/"signal_groups.csv").set_index(["session","group"])
    for key,group in obs.groupby(["session","group"]):
        assert len(group)==saved.loc[key,"n"]
        assert abs(group.future_mid_bps.mean()-saved.loc[key,"mid_bps"])<1e-10
        assert abs(group.paper_buy_sell_bps.mean()-saved.loc[key,"paper_bps"])<1e-10
    report=(out/"report.html").read_text(encoding="utf-8")
    assert "@@" not in report
    payload=report.split('<script id="payload" type="application/json">',1)[1].split('</script>',1)[0]
    decoded=json.loads(payload)
    assert len(decoded["demo"])==6
    verification=dict(unit_tests=result.testsRun, unit_tests_passed=True,
                      symbols_verified=summary["symbols"], signal_rows_verified=len(obs),
                      snapshot_csv_rows_verified=sum(x["snapshots"] for x in summary["replay"]),
                      no_future_signal=True, csv_summary_consistent=True, html_payload_valid=True)
    (out/"verification.json").write_text(json.dumps(verification,indent=2),encoding="utf-8")
    print(json.dumps(verification,indent=2))


if __name__=="__main__":
    main()
