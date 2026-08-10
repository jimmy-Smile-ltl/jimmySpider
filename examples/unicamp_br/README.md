# UNICAMP 学术库抓取 (unicamp_br)

## 站点

- 站点页面：https://repositorio.unicamp.br — 巴西坎皮纳斯州立大学（UNICAMP）机构知识库，收录学位论文、期刊文章等学术成果，按「研究生项目 (Programa de Pós-Graduação) → 研究领域 (Área de Concentração)」两级组织
- 列表 API：`/Resultado/CarregarPaginaLayoutDetalhe`（POST，参数 `paginaInicial` + `guid`），返回 HTML 片段；第一页从领域页 HTML 直接解析
- ASP.NET MVC 站点的会话机制：领域页内嵌 `window.AntiForgeryToken`，需 POST `/acervo/validaacessodetalhe` 完成访问验证取得会话，`guid` 以 `guid=xxx` 形式存在于领域页 URL 中
- 学术层级：`unicamp_academic_hierarchy.json`（program → `AreasConcentracao`，含 `CodigoArea` / `CodigoPrograma` / `UrlArea` / `QuantidadeRegistros`）

## 展示特性

- **同一站点三种抓取策略对比**（本目录三个文件互为对照）：
  - `spider_list_by_area.py`：完整会话策略 — AntiForgeryToken + validaacessodetalhe 验证后再翻页
  - `spider_list_by_year.py`：简化会话策略 — 只访问 `/Busca/Avancada` 初始化会话、提取 `__RequestVerificationToken`，`guid` 从响应 URL 提取，跳过验证接口
  - `spider_supplement_by_area.py`：数据校验驱动的最小化补采 — 用 JSON 中的 `QuantidadeRegistros` 与 MongoDB 实有数量对比，只重爬有缺口的领域
- **JSON 层级数据驱动**：从 `unicamp_academic_hierarchy.json` 读取 program → area 两级结构，双层循环遍历全部领域（几百个领域全覆盖）
- **多文件链接收集**（by_year 版）：同一条目下收集 `div.arquivos` 内全部文件链接与标题
- **作者信息结构化**：正则解析作者 title 中的「姓名, 出生年-逝世年」，拆出 `birth` / `death` 字段（by_area 版）
- **`_id` 生成策略**：条目含 DOI 时用 `generate_doi_id`（DOI 归一化 + 哈希），否则回退 `generate_string_id(标题)`
- **序列化断点续爬**：`{prog_idx, area_idx, page}` 三元组 JSON 序列化写入 Redis，中断后精确恢复；错误页（含领域上下文）入 Set，最多 3 轮重试
- **多线程并发查库**（supplement 版）：20 线程 `ThreadPoolExecutor` 对比全部 area 数量，按原始顺序输出缺口报告（`supplement_missing_report.json`），缺口 ≤5 条时建议人工核对而非自动补采
- **补采后二次核对**：补采完成再次统计，仍有缺口则写 `supplement_missing_report_v2.json`

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_list_by_area.py` | 按领域抓取（完整会话版）：AntiForgeryToken + validaacessodetalhe 验证，支持总页数解析、断点、错误页重试 |
| `spider_list_by_year.py` | 按领域抓取（简化会话版）：/Busca/Avancada 初始化 + URL 提取 guid，多文件链接收集，while 循环翻页 |
| `spider_supplement_by_area.py` | 数量核对 + 补采：多线程对比 DB 数量与 QuantidadeRegistros，缺口领域全量重爬，二次核对；`--check` 只统计不补采 |
| `unicamp_academic_hierarchy.json` | 学术层级数据（项目 → 领域），三个脚本的运行时依赖 |

## 运行方式

```bash
cd examples/unicamp_br
python spider_list_by_area.py          # 策略一：完整会话按领域抓取
python spider_list_by_year.py          # 策略二：简化会话按领域抓取
python spider_supplement_by_area.py    # 策略三：统计 + 补采
python spider_supplement_by_area.py --check   # 只统计缺口，不补采
```

## 前置条件

- `unicamp_academic_hierarchy.json` 必须与本目录文件同目录（运行时依赖，仓库已提供）
- 依赖服务：
  - **Redis**：断点续爬（`{表名}_log_progress`）、错误页集合（`{表名}_error_page_set`，supplement 版另用独立的 `{表名}_supp_error_set` 隔离）
  - **MongoDB**：结果存储，collection 名 = 目录名 `unicamp_br`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 无需登录，请求头已按浏览器模板配置（Chrome UA + sec-ch-ua 全家桶）

## 爬虫架构

**spider_list_by_area.py（完整会话版）**：

```
run_all()
 ├─ 加载 unicamp_academic_hierarchy.json，读取序列化断点 {prog_idx, area_idx, page}
 ├─ 先处理遗留错误页（最多 3 轮）
 ├─ for prog_idx → program_list[]
 │   └─ for area_idx → AreasConcentracao[]
 │       ├─ open_area()              # GET 领域页 → 提取 AntiForgeryToken
 │       │                           # POST validaacessodetalhe → 会话 cookies
 │       ├─ 解析第一页 + data-total-paginas 总页数
 │       ├─ for page 2..N: POST CarregarPaginaLayoutDetalhe 翻页
 │       │   ├─ parse_list_page()    # ficha-acervo-detalhe 条目解析
 │       │   ├─ save_result()        # MongoDB upsert
 │       │   └─ 每页后 record_string({prog_idx, area_idx, page+1})
 │       └─ 失败页 → error_page_set（带领域上下文）
 ├─ 主流程结束：错误页清空则清除断点，否则保留以便重启
 └─ handle_error_page() 还原上下文重试失败页
```

**spider_supplement_by_area.py（校验 + 补采）**：

```
run_supplement(check_only)
 ├─ Step1 统计: 20 线程并发 count_documents(领域代码+项目代码) vs QuantidadeRegistros
 │   └─ 缺口列表 → supplement_missing_report.json（保持原始 prog/area 顺序）
 ├─ [--check 模式到此为止]
 ├─ Step2 补采: 对每个缺口 area（missing>5）open_area + 全量重爬所有分页
 └─ Step3 二次核对: 重新统计，仍有缺口写 supplement_missing_report_v2.json
```

数据流向：领域页/分页 POST API → HTML 片段 → BeautifulSoup 提取 标题/作者/主题/DOI/文件链接 → MongoDB（`_id` = DOI 或标题哈希）。

## 核心代码片段

**ASP.NET 会话建立**（AntiForgeryToken → validaacessodetalhe 验证）：

```python
pattern = re.compile(r"window.AntiForgeryToken\s*=\s*'(.*?)'\s*;")
match = re.search(pattern, response.text)
if match:
    validate_headers["requestverificationtoken"] = match.group(1)
    validate_headers["referer"] = area_url
response_valid = self.single_fetcher.fetch(
    self.validate_url, headers=validate_headers,
    cookies=cookies, method="POST", check_size=False,
)
```

**分页 POST API + 总页数解析**：

```python
params = {"paginaInicial": str(page), "guid": guid}
response = self.single_fetcher.fetch(
    self.list_api_url, headers=headers, cookies=cookies,
    params=params, method="POST", check_size=False,
)
# 总页数从第一页头部解析
header = soup.find("div", class_="cabecalho-resultado-busca")
total_pages = int(header.attrs.get("data-total-paginas"))
```

**`_id` 生成策略**（DOI 优先，标题兜底）：

```python
if "DOI" in site_dict:
    id_code = generate_doi_id(site_dict.get("DOI") or site_dict.get("doi"))
    site_dict["doi"] = normalize_doi(site_dict.get("DOI"))
else:
    id_code = generate_string_id(title)
```

**作者生卒年解析**（by_area 版）：

```python
pattern = r"^(.*?)(?:,\s*(\d{4})-(.*))?$"
match = re.match(pattern, text.strip())
if match:
    return {
        "name": match.group(1).rstrip(','),
        "birth": match.group(2),
        "death": match.group(3).strip() if match.group(3) else None,
    }
```

**多线程并发查库**（supplement 版，保持原始顺序输出缺口）：

```python
with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {pool.submit(_worker, idx, task): idx for idx, task in enumerate(tasks)}
    for future in as_completed(futures):
        exc = future.exception()
        if exc:
            self.log_print.warning(f"线程异常: {exc}")
missing_list = [raw_results[i] for i in range(len(tasks)) if raw_results.get(i) is not None]
```
