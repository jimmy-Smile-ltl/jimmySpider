# 中央结算公司《债券》期刊抓取 (ccdc_bond_journal)

## 站点

- 站点页面：https://www.ccdc.com.cn/Fmi/Thinktank/Article/ — 中央国债登记结算有限责任公司（CCDC）《债券》期刊栏目
- 数据来源：各期次目录页 HTML（如 `https://www.ccdc.com.cn/Fmi/Thinktank/Article/cb2026/cb202604/`），按期次返回当期文章列表
- 期次清单由本地 `issues.json` 维护（年份 → 期次标题与 URL），无需爬列表页

## 展示特性

- **本地 JSON 驱动采集**：`issues.json` 保存「年份 → 各期次 URL」清单，主流程按期次顺序抓取，扩展清单即可覆盖更多期次
- **期刊页结构解析**：`div.journal_qlist` 分类块（`div.journal_stitle` 为分类名），块内每个 `li` 提取 标题 / 作者 / `docPubUrl` / `docId` / `curPage`
- **`_id` 降级策略**：文档 ID → 文档 URL → 标题，依次兜底生成唯一 ID
- **Redis 断点续爬**（key 前缀 `ccdc_bond_journal_`）：
  - `log_issue` — 记录 `issue_idx` 断点，中断后从该期次精确恢复
  - `error_issue_set` — 失败期次入集合，主流程后最多 3 轮重试
- **限速**：期次间 `time.sleep(1)`，降低触发风控的概率

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：读取期次清单 → 逐期解析 → 入库 → 断点与重试 |
| `issues.json` | 期次清单（年份 → 期次标题与 URL），运行前可按需补充新期次 |

## 运行方式

```bash
cd examples/ccdc_bond_journal
python spider.py
```

## 前置条件

- 无需登录 cookie；期次页面直接 GET 即可
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `ccdc_bond_journal_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `ccdc_bond_journal`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
