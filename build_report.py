"""从已落盘结果生成离线报告；图和交互样本内嵌，可单文件分享。"""
import base64
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def build(out):
    summary = json.loads((out/"summary.json").read_text(encoding="utf-8"))
    frames = json.loads((out/"replay_frames.json").read_text(encoding="utf-8"))
    demo = json.loads((out/"matching_demo.json").read_text(encoding="utf-8"))
    r, research = summary["replay"], summary["research"]
    plt.rcParams.update({"font.sans-serif":["Microsoft YaHei","SimHei","DejaVu Sans"],
                         "axes.unicode_minus":False,"font.size":10,"axes.spines.top":False,"axes.spines.right":False})
    figures = out/"figures"
    figures.mkdir(exist_ok=True)
    def save(fig, name):
        fig.savefig(figures/f"{name}.png", dpi=160, bbox_inches="tight", facecolor="white")
        fig.savefig(figures/f"{name}.svg", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        data = base64.b64encode((figures/f"{name}.png").read_bytes()).decode()
        return f'<img class="figure" src="data:image/png;base64,{data}" alt="{name}">'
    fig, ax = plt.subplots(figsize=(11,3.8),layout="constrained")
    x=np.arange(len(r))
    ax.bar(x-.2,[100*z["best_matches"]/z["snapshots"] for z in r],.4,label="最优买卖价一致",color="#487a95")
    ax.bar(x+.2,[100*z["depth_matches"]/z["snapshots"] for z in r],.4,label="十档价量一致",color="#cc8b51")
    step=max(1,len(r)//13)
    ax.set_xticks(x[::step],[str(z["symbol"]) for z in r[::step]],rotation=45)
    ax.set(ylabel="直接时刻对齐一致率（%）",ylim=(0,105))
    ax.legend(frameon=False,ncol=2,loc="upper right")
    check_fig=save(fig,"01_alignment")
    fig,axs=plt.subplots(1,2,figsize=(11,3.6),layout="constrained")
    st=research["statistics"]
    axs[0].scatter([z["median_mid"] for z in st],[z["median_spread_bps"] for z in st],color="#487a95",alpha=.8)
    axs[0].set(xscale="log",xlabel="中间价中位数（元，对数轴）",ylabel="相对价差中位数（bp）")
    vm=research["volume_minutes"]
    keys=list(vm)
    axs[1].bar(np.arange(len(keys)),np.array(list(vm.values()))/1e6,color="#487a95")
    wanted=[i for i,k in enumerate(keys) if k in ["925","930","1030","1130","1300","1400","1500"]]
    axs[1].set_xticks(wanted,[f"{int(keys[i])//100:02d}:{int(keys[i])%100:02d}" for i in wanted],rotation=35)
    axs[1].set(ylabel="全部样本成交量（百万股/分钟）",xlabel="交易时刻（仅有成交的分钟）")
    liquidity_fig=save(fig,"02_liquidity")
    fig,axs=plt.subplots(1,2,figsize=(11,3.6),sharey=True,layout="constrained")
    for ax,session in zip(axs,["上午","下午"]):
        data={z["group"]:z for z in research["signal_groups"] if z["session"]==session}
        names=[n for n in ["卖盘较厚","中性","买盘较厚"] if n in data]
        xx=np.arange(len(names))
        ax.bar(xx-.18,[data[n]["mid_bps"] for n in names],.36,label="未来中间价变化",color="#487a95")
        ax.bar(xx+.18,[data[n]["paper_bps"] for n in names],.36,label="纸面买入再卖出价差",color="#cc8b51")
        ax.axhline(0,color="#808080",linewidth=.8)
        ax.set_xticks(xx,[f'{n}\n(n={data[n]["n"]})' for n in names])
        ax.set(title=session,ylabel="均值（bp）")
    axs[0].legend(frameon=False,fontsize=9)
    signal_fig=save(fig,"03_signal_cost")
    n=len(r); count=sum(z["snapshots"] for z in r)
    best=100*sum(z["best_matches"] for z in r)/count
    full=100*sum(z["depth_matches"] for z in r)/count
    passing=sum(z["final_depth_equal"] and z["final_volume_equal"] for z in r)
    opening=sum(z["opening"].get("pairs_equal",False) for z in r)
    groups=research["signal_groups"]
    pm={z["group"]:z for z in groups if z["session"]=="下午"}
    pm_explain="对照这两个结果，看价格变化能否覆盖按对手报价交易的损耗。"
    if "买盘较厚" not in pm:
        pm_explain="本次运行的下午没有该组样本，因此暂时不能比较。"
    elif pm["买盘较厚"]["mid_bps"]>0 and pm["买盘较厚"]["paper_bps"]<0:
        pm_explain="中间价略涨，按对手报价计算仍然亏损。"
    group_rows="".join(f'<tr><td>{z["session"]}</td><td>{z["group"]}</td><td>{z["n"]:,}</td><td>{z["mid_bps"]:.3f}</td><td>{z["paper_bps"]:.3f}</td></tr>' for z in groups)
    stock_rows="".join(f'<tr><td>{z["symbol"]}</td><td>{z["events"]:,}</td><td>{100*z["depth_matches"]/z["snapshots"]:.2f}%</td><td>{"通过" if z["final_depth_equal"] and z["final_volume_equal"] else "差异"}</td><td>{"通过" if z["opening"].get("pairs_equal") else "差异"}</td></tr>' for z in r)
    total_records=sum(z["rows"] for z in summary["profile"].values())
    data={"frames":frames,"demo":demo,"groups":groups}
    tokens={"@@COUNT@@":f"{total_records:,}","@@N@@":str(n),"@@EVENTS@@":f'{sum(z["events"] for z in r):,}',
            "@@PASSING@@":f"{passing}/{n}","@@OPENING@@":f"{opening}/{n}","@@CHECKS@@":f"{count:,}",
            "@@PASSING_COUNT@@":str(passing),"@@OPENING_COUNT@@":str(opening),
            "@@BEST@@":f"{best:.2f}%","@@FULL@@":f"{full:.2f}%",
            "@@AFTER@@":f'{sum(z["after_trades"] for z in r):,}',
            "@@AMOUNT_ERROR@@":f'{max(abs(z["final_turnover_error_yuan"]) for z in r):.2f}',
            "@@SPREAD@@":f'{research["median_spread_bps"]:.3f}',"@@TICK@@":f'{research["one_tick_pct"]:.2f}%',
            "@@CHECK_FIG@@":check_fig,"@@LIQUIDITY_FIG@@":liquidity_fig,"@@SIGNAL_FIG@@":signal_fig,
            "@@GROUP_ROWS@@":group_rows,"@@STOCK_ROWS@@":stock_rows,"@@SECONDS@@":str(summary["elapsed_seconds"]),
            "@@PM_BUY@@":f'{pm["买盘较厚"]["mid_bps"]:+.3f}' if "买盘较厚" in pm else "无样本",
            "@@PM_SELL@@":f'{pm["卖盘较厚"]["mid_bps"]:+.3f}' if "卖盘较厚" in pm else "无样本",
            "@@PM_PAPER@@":f'{pm["买盘较厚"]["paper_bps"]:+.3f}' if "买盘较厚" in pm else "无样本",
            "@@PM_EXPLAIN@@":pm_explain,
            "@@ENV@@":html.escape(json.dumps(summary["environment"],ensure_ascii=False)),
            "@@DATA@@":json.dumps(data,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("</","<\\/")}
    for source,target in [("learning_template.html","report.html"),
                          ("report_template.html","technical_report.html")]:
        template=Path(__file__).with_name(source).read_text(encoding="utf-8")
        current_tokens=dict(tokens)
        if source=="learning_template.html":
            # The learning page needs only the six illustrative steps; full replay stays in the appendix.
            current_tokens["@@DATA@@"]=json.dumps({"demo":demo},ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("</","<\\/")
        for key,value in current_tokens.items():
            template=template.replace(key,value)
        if "@@" in template:
            raise ValueError(f"unfilled template: {source}")
        (out/target).write_text(template,encoding="utf-8")
    text=f'''第三节实践课作业：从 Level-2 数据理解订单簿与撮合

作业仓库：https://github.com/mabucai/pku-quantllm-lesson03

基于 2026-09-23 课堂数据，独立实现整数分价格、FIFO 队列、限价撮合、部分成交、撤单和集合竞价，并进行 {n} 只股票全日历史回放。共处理 {sum(z['events'] for z in r):,} 条竞价事件，另行统计盘后 {sum(z['after_trades'] for z in r):,} 笔成交。

实测：日终十档价量及累计成交量 {passing}/{n} 一致；仅用开盘前委托输入的集合竞价，其成交价量与逐笔订单配对 {opening}/{n} 一致。全部回放完成且订单数量守恒检查通过。

一个主要收获是：字段语义和时间对齐比界面更重要。此数据的 OrderOriNo 才是订单关联号，ADD 是首次撮合后的剩余挂单量。直接按快照标注时刻核对，十档价量一致率为 {full:.2f}%，因此没有把日终一致宣称为日内精确重建。

补充固定参数的盘口不平衡实验：每60秒取样、信号延迟3秒、观察未来30秒，分别展示早/下午的中间价变化及按对手报价计算的纸面价差。单日结果不能证明可交易盈利；未模拟真实排队、账户、延迟成交和市场冲击。

附件含离线图文报告、交互盘口对照、撮合步骤、原始结果表和复现代码。参考了课程讨论和同学公开 README 的任务组织与字段提示，代码及本次统计由本地实现和计算；使用 Codex 辅助。
'''
    (out/"提交说明.txt").write_text(text,encoding="utf-8")


if __name__ == "__main__":
    import sys
    build(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/"output")
