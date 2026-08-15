# BlackRock 投资研究院全球洞察抓取 (blackrock_insights)

## 站点

- 站点页面：https://www.blackrock.com/corporate/insights/blackrock-investment-institute/archives — BlackRock 投资研究院（BII）全球洞察归档页
- 数据来源：静态 HTML 归档页，一次返回全部洞察条目（无分页接口），无需登录
- 覆盖 BlackRock 投资研究院发布的市场评论、投资展望等英文研究报告列表

## 展示特性

- **静态 HTML 页面抓取**：GET 归档页直接解析，无接口、无登录、无加密参数
- **BeautifulSoup 卡片解析**：`div.gls-related-literature div.item` 卡片提取 标题（h2）/ 日期（div.attribution）/ 链接（a）/ 摘要（div.description）
- **英文日期标准化**：`convert_date_robust` 将 `December 31, 2025` 等任意格式日期统一转为 `YYYY-MM-DD`
- **相对链接补全**：`urljoin` 将卡片内相对 href 拼接为完整详情页 URL
- **`_id` 降级策略**：详情 URL 缺失时回退为标题文本 MD5
- **Redis 完成标记**：`log_page` 记录 `{"done": true}`，避免重复采集同一归档页

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：GET 归档页 → 卡片解析 → 字段标准化 → 入库 |

## 运行方式

```bash
cd examples/blackrock_insights
python spider.py
```

## 前置条件

- 无需登录 cookie；直接请求归档页即可
- 依赖服务：
  - **Redis**：完成标记（key 前缀 `blackrock_insights_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `blackrock_insights`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
