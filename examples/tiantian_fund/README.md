# 天天基金网基金数据抓取 (tiantian_fund)

分页抓取天天基金网基金排名列表（rankhandler.aspx），再并发抓取每只基金的累计净值曲线（pingzhongdata/{fcode}.js），MySQL 双表落库 + CSV 本地兜底，Redis 断点续爬 + 失败任务自动重试。迁移自北大信研院 pro46「天天基金」。

## 站点

- 站点页面：https://fund.eastmoney.com/data/fundranking.html — 天天基金网基金排名，覆盖全市场基金
- 列表接口：`https://fund.eastmoney.com/data/rankhandler.aspx`（GET，每页 50 条，返回 `var rankData = {...};` JS 数据）
- 详情接口：`https://fund.eastmoney.com/pingzhongdata/{fcode}.js`（返回 `var Data_ACWorthTrend = [...];` 净值序列）

## 展示特性

- **列表 + 详情双表架构**：`tiantian_fund`（基金档案，`fcode` 唯一）+ `tiantian_fund_detail`（累计净值时序，`fcode + TradingDay` 联合唯一）
- **JSON5 解析 JS 数据**：列表/详情响应均为 `var xxx = {...};` 形式，用 `json5.loads` 直接解析，无需手工正则拆字段
- **字段清洗**：22 个逗号分隔字段缺位补齐（`parse_fund_to_dict`），净值转 float，`is_buyable` 按 `arr[17] in ["1","2","3","8","9"]` 映射，涨跌颜色正红负绿
- **详情并发抓取**：`ThreadPoolExecutor(max_workers=5)` 并发抓一页所有基金的净值曲线，单只失败不阻断整页
- **双通道落库**：先写 CSV 本地兜底（`tiantian_fund_list.csv` / `tiantian_fund_detail.csv`，utf-8-sig，多线程加锁），再写 MySQL
- **断点 + 失败重试**：页码断点 `log_page_tiantian_fund`；失败列表页/失败详情分别入 Redis Set（`tiantian_fund_error_page_set` / `tiantian_fund_error_detail_set`），主流程结束后统一重试最多 3 轮
- **调试参数**：`run(end_page=N)` 提前停止，方便联调

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件，列表 + 详情 + 重试一体） |
| `tiantian_fund_list.csv` / `tiantian_fund_detail.csv` | CSV 本地兜底（运行后自动生成） |

## 运行方式

```bash
cd examples/tiantian_fund
python spider.py          # 抓全部页
python spider.py          # 调试：改 __main__ 传 run(end_page=3)
```

## 前置条件

- `pip install json5`（解析 JS 数据）
- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`；测试残留（<10 条）自动删表重建并重置断点
- **Redis**：断点缓存（key 前缀 `tiantian_fund_`，共 3 个）
- CSV 输出在当前目录，字段与数据库一一对应

两张表的关键列（`create_table` 自动执行，`drop_table(max_num=10)` 残留自清理）：

| 表 | 关键列 | 索引 |
|----|--------|------|
| `tiantian_fund` | `fcode`、`short_name`、`unit_nav` DECIMAL(18,6)、`accumulated_nav`、`daily_growth_rate`、`establish_date`、`is_buyable` TINYINT、`growth_color` | `uniq_fcode` 唯一键 + `idx_list_date(date)` |
| `tiantian_fund_detail` | `fcode`、`short_name`、`TradingDay` DATE、`Return` DECIMAL(18,6) | `uniq_fcode_tradingday(fcode, TradingDay)` + `idx_trading_day` |

## 爬虫架构

```
run(end_page=None)
 ├─ current_page = log_page_tiantian_fund.get_int(default=1)
 ├─ while current_page <= max_page:
 │   ├─ get_list：rankhandler.aspx（op=ph, dt=kf, ft=all, sc=1nzf, sd=一年前, ed=今天, pn=50, v=随机）
 │   │   └─ 请求失败 → error_page_set.add_to_set(page)，跳过本页
 │   ├─ extract_list：正则抠 rankData + json5 解析 → 基金列表（allPages 覆盖 max_page）
 │   ├─ save_list：写 CSV → save_result 写 MySQL（fcode 唯一键 upsert）
 │   └─ handle_details：5 线程并发抓 pingzhongdata/{fcode}.js
 │       └─ Data_ACWorthTrend 毫秒时间戳 → TradingDay 日期序列，写入 detail 表 + CSV
 ├─ 全部页完成 → handle_error()：最多 3 轮重试 error_page_set / error_detail_set
```

数据流向：列表接口 → 22 字段基金档案 → `tiantian_fund` 表；净值曲线接口 → (fcode, TradingDay, Return) 序列 → `tiantian_fund_detail` 表；两份 CSV 全程兜底。

## 核心代码片段

**列表接口参数与 rankData 解析**：

```python
params = {"op": "ph", "dt": "kf", "ft": "all", "sc": "1nzf", "st": "desc",
          "sd": (today - timedelta(days=365)).strftime("%Y-%m-%d"),
          "ed": today.strftime("%Y-%m-%d"), "pi": str(page_num),
          "pn": "50", "dx": "1", "v": random.random()}
url = "https://fund.eastmoney.com/data/rankhandler.aspx"

pattern = re.compile(r"var\s*rankData\s*=\s*(.*?)\s*;", re.S)
data_json = json5.loads(pattern.search(response.text).group(1))
fund_list = [self.parse_fund_to_dict(item) for item in data_json.get("datas", [])]
```

**净值序列解析（毫秒时间戳转日期）**：

```python
pattern = re.compile(r"var\s*Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", re.S)
for item in json5.loads(pattern.search(response.text).group(1)):
    trading_day = datetime.fromtimestamp(item[0] / 1000).strftime("%Y-%m-%d")
    detail_list.append({"fcode": fcode, "short_name": short_name,
                        "TradingDay": trading_day, "Return": item[1]})
```

**失败统一重试（最多 3 轮）**：

```python
def handle_error(self) -> None:
    for retry in range(3):
        pages_cleared = self.handle_error_page()    # 重试失败列表页
        details_cleared = self.handle_error_detail()  # 重试失败详情
        if pages_cleared and details_cleared:
            self.log_print.print("所有错误页面和详情已成功处理")
            break
```
