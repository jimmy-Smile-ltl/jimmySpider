# 人卫智网 CCDAS 临床指南抓取 (pmphai_ccdas)

## 站点

- 站点页面：https://ccdas.pmphai.com — 人民卫生出版社人卫智网 CCDAS 临床指南数据库（医学会议指南/临床诊疗指南）
- 数据接口：
  - 分类接口：`/tagc/facetguide`（POST form，返回扁平科室列表 `keshi`）
  - 列表接口：`/appguide/list`（POST form，按科室叶子 + 页码返回指南 JSON，每页 10 条）

## 展示特性

- **分类树构建**：`build_tree()` 将接口返回的扁平科室列表按 `parent` 字段构建多级树，`extract_leaves()` 递归收集全部叶子科室
- **叶子元数据透传**：叶子节点的直接父级为 level 2 时记录 `father_id` / `father_name`，作为科室分类归属写入每条记录
- **双接口流水线**：分类接口一次性取全科室 → 按「叶子科室 × 页码」两级遍历列表接口
- **Redis 断点续爬**（key 前缀 `pmphai_ccdas_`）：
  - `log_page` — 记录 `{"leaf_idx", "page"}` 断点，中断后从科室 + 页码精确恢复
  - `error_page_set` — 失败页入集合，主流程后最多 3 轮重试
- **会话 Cookie 已清空**：原实现含 JSESSIONID 等会话/统计 Cookie，当前接口匿名可访问；若站点调整风控需自行登录补充

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：分类树 → 叶子科室 → 分页抓取 → 入库 → 断点与重试 |
| `keshi_tree.json` | 参考数据：科室分类树（level 1-3 层级示例） |
| `leaf_list.json` | 参考数据：叶子科室列表（含父科室归属） |

## 运行方式

```bash
cd examples/pmphai_ccdas
python spider.py
```

## 前置条件

- 无需登录 cookie；会话 Cookie 已清空，接口匿名可访问
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `pmphai_ccdas_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `pmphai_ccdas`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
