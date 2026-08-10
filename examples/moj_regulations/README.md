# 司法部规章库抓取 (moj_regulations)

## 站点

- 站点页面：https://www.moj.gov.cn/pub/sfbgw/gwygzk/index.html — 司法部政府规章库，收录部门规章、地方政府规章等法规全文
- 数据接口：`https://sousuoht.www.gov.cn/athena/forward/BD8730CDDA12515E2D9E1B21AA11C0D6` — 国务院统一检索平台（athena）的规章库搜索接口，POST JSON 返回法规结构化字段
- 特色：接口字段名为混淆后的编号（`f_202321360426` 等），需映射为可读中文名后入库

## 展示特性

- **JSON POST 接口分页**：显式装配 `SingleRequestHandler`，构造结构化搜索 payload（`code` / `tableName` / `searchFields` / `resultFields` / `pageSize` / `pageNo`），按发布日期倒序分页
- **`rename_keys_inplace` 字段映射**：将接口的加密字段名批量映射为中文可读名（发布机关、法规类别、法规正文、发布日期、所属省份等约 20 个字段），并保留「备用字段」处理同一信息多个候选值的情况
- **`safe_extract_json` 安全取值**：`path` 传嵌套路径、`default` 兜底，彻底避免 `KeyError` 导致整页解析失败
- **`_id` 生成策略**：以 `doc_pub_url`（原文链接）为唯一标识生成 `_id`，并兼容该字段为「字符串或列表」两种形态；缺失时告警但不中断
- **Redis 断点续爬**：页码（`log_page_num`）+ 失败页 Set（`error_page_set`），主流程结束后循环重试失败页直至清空

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：JSON POST 分页 → 字段映射 → 入库 → 断点与重试 |

## 运行方式

```bash
cd examples/moj_regulations
python spider.py
```

## 前置条件

- **必须填写 `athenaAppKey`**：代码中 `self.headers["athenaAppKey"]` 为占位符 `YOUR_ATHENA_APP_KEY_HERE`，需从浏览器 DevTools（Network 面板打开规章库搜索请求）复制真实值填入，否则接口返回鉴权失败
- 无需登录 cookie；`athenaAppName` 已内置（URL 编码的「规章库」）
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `moj_regulations_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `moj_regulations`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_list()
 ├─ 从 log_page_num 恢复页码
 ├─ while current_page <= max_page
 │   ├─ get_one_page(page)
 │   │   ├─ build_list_payload(page)   # 构造 athena 搜索 JSON
 │   │   └─ POST list_api_url（pageSize=7）
 │   ├─ extract_one_page(response)
 │   │   ├─ safe_extract_json 取 resultCode 校验
 │   │   └─ 取 result.data.list / pager（pageCount/pageNo → has_next）
 │   ├─ clean_data(data_list)
 │   │   ├─ doc_pub_url 列表兼容
 │   │   ├─ rename_keys_inplace 字段映射
 │   │   └─ _id = generate_string_id(doc_pub_url)
 │   ├─ save_result(cleaned_list)      # MongoDB upsert
 │   └─ 失败页入 error_page_set
 ├─ 主流程结束
 └─ handle_error_page() 循环重试失败页，直到 Set 清空
```

数据流向：athena 接口 JSON（混淆字段）→ `rename_keys_inplace` 映射为中文名 → 补 `_id` → MongoDB（collection = 目录名）。

## 核心代码片段

**字段映射表**（接口混淆字段 → 可读中文名，`f_` 开头为检索平台动态字段）：

```python
rename_keys = rename_mapping = {
    "f_202321136868": "发布修订信息",
    "f_20232124962": "原文链接",            # 可能是字符串或列表
    "f_20232151076": "发布机关",            # 如“汕头市人民政府”
    "f_202321807875": "法规类别",            # 如“地方政府规章”“部门规章”
    "f_202321360426": "法规名称",
    "f_202321758948": "法规正文",
    "f_202321864401": "附件数量",            # 整数值 1,2,3
    "f_202321915922": "发布日期",
    "f_202321423473": "所属省份",
    "f_202321124775": "数据唯一标识",         # 数值ID
    "doc_pub_url": "发布网址",
    ...
}
```

**数据清洗与 `_id` 生成**（兼容 `doc_pub_url` 为列表的形态）：

```python
def clean_data(self, data):
    data_list = []
    for item in data or []:
        doc_pub_url = item.get("doc_pub_url")
        if isinstance(doc_pub_url, list):
            doc_pub_url = doc_pub_url[0] if doc_pub_url else None
        item_new = rename_keys_inplace(original_dict=item, key_mapping=rename_keys)
        if doc_pub_url:
            item_new["_id"] = generate_string_id(doc_pub_url)
        else:
            self.log_print.warning(f"missing doc_pub_url, cannot create _id: {item_new}")
        data_list.append(item_new)
    return data_list
```

**安全取嵌套字段**（避免 KeyError，解析失败返回默认值而非抛异常）：

```python
page_info = safe_extract_json(res_json, path=["result", "data", "pager"], default={})
data_list = safe_extract_json(res_json, path=["result", "data", "list"], default=[])
max_page  = safe_extract_json(page_info, path=["pageCount"], default=None)
```
