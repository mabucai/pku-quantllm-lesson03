# 第三节实践课：从逐笔数据理解订单簿与撮合

先打开 **[简明学习讲义](output/report.html)**。2026-10-16按初学者的阅读顺序重写，用“限价10.01元买900股”贯穿盘口、成交和成本；包含撮合步骤、bp换算和五道自测题。

原有字段说明、全量图表和真实盘口回放保留在 **[实验明细](output/technical_report.html)**。两个页面都可离线打开，第一次学习先读简明讲义即可。

任务来自[课程 Issue #6](https://github.com/aslan9/pku_quantllm/issues/6)。公开页面没有完整评分细则，本项目参考同学公开作业的范围，独立实现数据回放、撮合和一个微观结构实验。参考来源和 Codex 辅助说明见报告末尾；没有直接复制同学的项目源码，提交方式见文末。

## 先学习，再运行

1. 第1步看报价：理解买一、卖一和等待成交的订单。
2. 第2步买900股：点击“下一步”，看价格优先、同价排队、部分成交和撤单。
3. 第3步算成本：把bp换成元，理解买卖价差。
4. 第4步读数据：理解委托、成交、快照分别记录什么。
5. 第5步理解作业结果，然后做五道自测题。
6. 想动手时，先运行 `examples/buy_600.json` 小练习，再阅读实验明细及全量分析。

## 环境与复现

Python 3.12以上。下载本仓库并进入仓库根目录；安装依赖后，把课程数据解压到 `sse50_20260923` 文件夹。原始数据沿用[课程下载链接](https://github.com/aslan9/pku_quantllm/releases/download/data-sse50-20260923/sse50_20260923.zip)，不随本仓库上传。

```text
python -m venv .venv
```

Windows PowerShell使用 `.\.venv\Scripts\Activate.ps1` 激活；macOS/Linux使用 `source .venv/bin/activate` 激活。然后运行：

```text
python -m pip install -r requirements.txt

# 18项小样本测试，不需要原始行情
python -m unittest discover -s . -p "test_*.py" -v

# 运行600股买入练习，或默认的六步演示
python run_demo.py --orders examples/buy_600.json
python run_demo.py

# 先复现一只股票，另存结果
python analyze.py --data-dir sse50_20260923 --symbols 600036 --output output_smoke

# 全部50只股票：重新生成分析、图表和报告
python analyze.py --data-dir sse50_20260923

# 核对结果文件与时间约束
python verify_outputs.py

# 独立集合竞价示例
python run_demo.py --orders examples/orders.json --auction
```

数据放在其他位置时，把 `--data-dir` 后的路径替换为实际目录。只有课堂 `20260923` 数据格式经过验证；实际运行版本记录在 `output/summary.json`。

下载后，直接用浏览器打开 `output/report.html` 阅读简明学习讲义，或打开 `output/technical_report.html` 查看实验明细。页面可以离线使用，无需启动服务器。GitHub文件页显示HTML源码，需要下载后打开。

## 实现与证据

| 文件 | 用途 |
| --- | --- |
| `book.py` | 整数分订单簿、FIFO、限价连续撮合、集合竞价、撤单、数量审计 |
| `analyze.py` | 三文件解析、按业务序号回放、快照核对、固定信号实验 |
| `run_demo.py`、`examples/orders.json` | 自定义原始委托的独立撮合入口 |
| `test_core.py` | 18项测试，包括1000个随机订单的逐步数量守恒 |
| `verify_outputs.py` | 独立读回CSV与摘要，核对统计及信号时间约束 |
| `build_report.py`、`learning_template.html`、`report_template.html` | 从本次结果生成简明讲义与实验明细；结果数值不手填 |
| `output/report.html` | 简明讲义，最先阅读，可单文件分享 |
| `output/technical_report.html` | 选读：字段、图表、盘口回放与实验方法 |
| `output/summary.json`、`verification.json` | 真实运行摘要、验证记录 |
| `output/checks_*.csv` | 每只股票、每个可比快照的原始核对结果 |
| `output/signal_observations.csv` | 每个信号、入口、未来标签的时间与收益 |
| `output/figures/` | 可单独用于汇报的PNG与SVG图 |
| `output/提交说明.txt` | 可改写的课程提交文字，填入仓库链接后使用 |

## 方法边界

- 历史回放使用真实成交扣减；不能称为独立预测或复现全部连续竞价成交。独立撮合使用自备订单；开盘集合核对仅以开盘委托作为撮合输入。
- 每只股票内部按唯一竞价通道的 `BizIndex` 排序；用 `OrderOriNo` 关联，新增为所披露的剩余量。价格转成整数分。重复业务序号、缺失被动订单、负量或超扣均报错。
- 快照对齐只消费事件时间累计最大值不晚于标签的业务序号前缀。没有向前/后搜索匹配状态，也不用快照修补簿。因此不能与采用秒内搜索或±3秒匹配的同学直接比较百分比。
- 快照核对范围：09:30–11:30、13:00–14:57（均左闭右开），以及15:00以后；排除集合竞价虚拟盘口。不额外去重，所以收盘重复快照各计一次。
- 盘后通道2301单独累计，与快照AfterVolume核对，不重建未提供的盘后委托队列。
- 固定阈值±0.5、60秒取样、3秒信号延迟、30秒标签；不跨午休。下午只作同日时间留出；不称为独立跨日样本外。未进行统计显著性检验。
- 纸面按对手价买卖仅作成本算术，不是账户回测；没有持仓、T+1、排队、成交延迟、深度容量、市场冲击和完整申报风控。
- 集合与连续撮合处理已被接受的限价订单，不能替代券商或交易所的完整业务系统。

## 提交方式

作业仓库：https://github.com/mabucai/pku-quantllm-lesson03

课程提交入口：[第三节实践课 Issue #6](https://github.com/aslan9/pku_quantllm/issues/6)。代码、说明、报告与验证结果均在本仓库；原始行情不入库。
