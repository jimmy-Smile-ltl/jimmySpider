# 人卫智网 CCDAS 临床指南抓取 (pmphai_ccdas)

双 POST 接口流水线示例：分类接口构建科室树 → 叶子科室分页拉取指南列表，断点精确到「科室 × 页码」。

## 站点

- 站点页面：https://ccdas.pmphai.com — 人民卫生出版社人卫智网 CCDAS 临床指南数据库（医学会议临床指南 / 临床诊疗指南）
- 数据接口（均 POST form 编码，无签名鉴权）：
  - 分类接口：`/tagc/facetguide` — 返回扁平科室列表 `data.keshi`（含 `id / name / level / parent` 字段）
  - 列表接口：`/appguide/list` — 按「科室叶子 × 页码」返回指南 JSON，每页 10 条
- 详情页：`/appguide/toPcDetail?sessionId=&knowledgeLibPrefix=guide&id={id}`（由 id 拼接，示例不抓详情正文）

## 展示特性

- **分类树构建**：`build_tree()` 将扁平 `keshi` 列表按 `parent` 字段构建多级树（根兜底取 level 1 节点），`extract_leaves()` 递归收集全部叶子科室
- **叶子元数据透传**：叶子节点的直接父级为 level 2 时记录 `father_id` / `father_name`，作为科室分类归属写入每条记录（对照 `leaf_list.json`：`心血管内科 → 父科室 内科`）
- **双接口流水线**：分类接口一次取全科室 → 按「叶子科室 × 页码」两级遍历列表接口（`pageSize=10`，`$search_ke_shi_fen_lei` 为科室过滤参数）
- **Redis 断点续爬**（key 前缀 `pmphai_ccdas_`）：
  - `log_page` — 记录 `{"leaf_idx", "page"}` 断点，中断后从科室 + 页码精确恢复
  - `error_page_set` — 失败页（`{"page", "keshi_id"}`）入集合，主流程后最多 3 轮重试
- **完成即清断点**：主流程跑完后 `log_page.clear_value()`，下次运行从头开始
- **会话 Cookie 已清空**：`self.cookies = {}`（原实现含 JSESSIONID 等会话与统计 Cookie），当前接口匿名可访问；若站点调整风控需自行登录补充

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：分类树 → 叶子科室 → 分页抓取 → 入库 → 断点与重试 |
| `keshi_tree.json` | 参考数据：科室分类树（level 1-3 层级示例，`build_tree()` 产出结构） |
| `leaf_list.json` | 参考数据：叶子科室列表（含 `father_id` / `father_name` 归属，`extract_leaves()` 产出结构） |

## 运行方式

```bash
cd examples/pmphai_ccdas
python spider.py
```

## 前置条件

- 无需登录 cookie；会话 Cookie 已清空，接口匿名可访问
- 依赖服务：
  - **Redis**：断点续爬（key `pmphai_ccdas_log_page` / `pmphai_ccdas_error_page_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `pmphai_ccdas`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 读取 log_page 断点 → {start_leaf_idx, start_page}
 ├─ get_leaf_list()
 │   ├─ POST /tagc/facetguide (searchText="", knowledgeLibPrefix="guide")
 │   │   └─ 响应 data.keshi 扁平科室列表
 │   ├─ build_tree()      # 按 parent 字段建多级树
 │   └─ extract_leaves()  # 递归收集叶子，level-2 直接父级透传 father_id/name
 ├─ for leaf_idx in [start_leaf_idx, len(leaf_list)):
 │   └─ while page ≤ totalPage:
 │       ├─ POST /appguide/list (pageNo, pageSize=10, $search_ke_shi_fen_lei=keshi_id)
 │       ├─ code=="000000" → result.datas + result.totalPage
 │       ├─ parse_page_data() → 中文字段 + 科室归属 + detail_url
 │       ├─ save_result() 并每页写 log_page 断点
 │       └─ 失败 → error_page_set.add_to_set({"page", "keshi_id"})，跳过该科室
 ├─ log_page.clear_value()
 └─ 最多 3 轮 handle_error_page()   # 按 keshi_id 重查失败页
```

数据流向：科室树 → 叶子列表 → 逐科室分页 JSON → 指南记录（名称/概述/浏览量/详情链接 + 科室归属）→ MongoDB `pmphai_ccdas`。

## 核心代码片段

**分类树构建与叶子收集**（`build_tree` + `extract_leaves`）：

```python
@staticmethod
def extract_leaves(node: Dict, parent: Optional[Dict] = None) -> List[Dict]:
    leaves = []
    children = node.get("children", [])
    if not children:
        if parent and parent.get("level") == 2:   # 直接父级为 level 2 才透传
            father_id, father_name = parent["id"], parent["name"]
        else:
            father_id = father_name = None
        leaves.append({"id": node["id"], "name": node["name"],
                       "level": node["level"],
                       "father_id": father_id, "father_name": father_name})
    else:
        for child in children:
            leaves.extend(Spider.extract_leaves(child, node))
    return leaves
```

**列表接口调用**（form 编码 + 科室过滤参数）：

```python
data = {
    "pageNo": str(page), "pageSize": "10", "userId": "userId",
    "searchText": "", "$search_zhi_nan_fen_lei": "",
    "$search_ke_shi_fen_lei": keshi_id,        # 科室叶子过滤
    "knowledgeLibPrefix": "guide",
}
response = self.single_fetcher.fetch(self.list_api_url, headers=self.headers,
                                     cookies=self.cookies, data=data,
                                     method="POST", check_size=False)
```

**记录组装**（中文字段名 + 详情链接拼接 + 科室归属）：

```python
detail_url = (f"{self.base_url}/appguide/toPcDetail"
              f"?sessionId=&knowledgeLibPrefix=guide&id={raw_id}")
record = {
    "_id": generate_string_id(detail_url),
    "名称": item.get("ming_cheng", ""),
    "概述": item.get("gai_shu", ""),
    "浏览量": item.get("view_count", 0),
    "detail_url": detail_url,
    "科室id": leaf.get("id"), "科室": leaf.get("name"),
    "科室level": leaf.get("level"),
    "父科室id": leaf.get("father_id"), "父科室": leaf.get("father_name"),
}
```
