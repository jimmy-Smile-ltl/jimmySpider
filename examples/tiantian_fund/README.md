# 天天基金数据抓取 (tiantian_fund)

## 站点

- 站点页面：https://fund.eastmoney.com/data/fundranking.html — 天天基金网基金排名，覆盖全市场基金
- 数据接口：`rankhandler.aspx`（列表，每页 50 条）+ `pingzhongdata/{fcode}.js`（单只基金累计净值曲线）

## 展示特性

- **列表 + 详情双表架构**：`tiantian_fund`（基金基本档案，`fcode` 唯一）+ `tiantian_fund_detail`（累计净值时序，`fcode + TradingDay` 联合唯一）
- **JSON5 解析 JS 数据**：响应是 `var rankData = {...};` 形式，用 json5 直接解析，无需正则手工拆字段
- **字段清洗**：22 个逗号分隔字段缺位补齐，净值转 float，`is_buyable` 按 doSale 逻辑映射，涨跌颜色正红负绿
- **详情并发抓取**：ThreadPoolExecutor 每页 5 线程抓详情，单只基金失败不阻断整页
- **双通道落库**：先写 CSV 本地兜底（最小可恢复），再写 MySQL
- **断点 + 失败重试**：页码断点续爬；失败列表页/失败详情分别入 Redis Set，主流程结束后最多重试 3 轮

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件） |

## 运行方式

```bash
cd examples/tiantian_fund
python spider.py          # 抓全部页
python spider.py          # 调试：修改 __main__ 传 run(end_page=3)
```

## 前置条件

- `pip install json5`（解析 JS 数据）
- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`；测试残留（<10 条）自动删表重建并重置断点
- **Redis**：断点续爬（key 前缀 `tiantian_fund_`）
- CSV 输出在当前目录：`tiantian_fund_list.csv` / `tiantian_fund_detail.csv`
