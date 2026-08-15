# monitor_demo —— 监控告警集成演示

> 演示 `jimmyspider.distributed.monitor` 三件套：**指标采集（MetricsCollector）+
> 健康检查（HealthChecker）+ 告警（AlertManager）**。完全离线运行：
> 无网络、无 Redis、无 webhook，磁盘/内存检查基于 psutil 本地完成。

```bash
python spider.py                  # 默认：40 个请求，错误率 33%
python spider.py --fail-every 2   # 调整失败密度（每 2 个失败 1 个 → 50%）
```

## 演示链路

1. **模拟爬虫运行**：`record_request(worker, category, success, latency_ms)` 逐条记录
   成功/失败/耗时，另上报队列深度、代理池存活数、DB 连接数
2. **健康检查**：内置 `check_disk` / `check_memory`（psutil）+ 自定义 worker 心跳检查
   （连查 3 轮从健康演变为宕机，触发 `on_unhealthy` 回调）
3. **告警评估**：3 条规则全部触发 —— 错误率 33% > 30%（critical）、
   队列深度 12345 > 10000（warning）、代理存活 1 < 3（critical）；
   并演示**冷却期抑制**与**故障恢复**（错误率下降后规则不再触发）
4. **运维输出**：Prometheus 文本格式指标、集群健康摘要、告警历史

## 接 WebhookChannel（企微 / 钉钉 / 飞书 / Slack）

```python
from jimmyspider.distributed.monitor import WebhookChannel

alerts.add_channel(WebhookChannel("wecom",    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"))
alerts.add_channel(WebhookChannel("dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=xxx"))
alerts.add_channel(WebhookChannel("feishu",   "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"))
alerts.add_channel(WebhookChannel("slack",    "https://hooks.slack.com/services/xxx"))
```

替换 `ConsoleChannel()` 即可，无需改其他代码（示例用控制台通道保证零依赖可跑）。

## 监控大盘（Prometheus + Grafana）

```python
from jimmyspider.distributed.monitor import DashboardExporter, GRAFANA_DASHBOARD_JSON

exporter = DashboardExporter(metrics, health, alerts)
await exporter.start_http_server(port=9090)   # /metrics /health /stats /alerts 四端点
```

Prometheus 抓取 `http://localhost:9090/metrics`，`GRAFANA_DASHBOARD_JSON` 是现成的
Dashboard 模板（请求量/成功率/耗时/队列深度/告警面板），导入 Grafana 即可。

## 生产要点

- `record_request` 写入带 label 的明细计数（Prometheus 用）；框架的
  `summary()` / `error_rate_rule` 按**无 label 汇总键**聚合 —— 示例同步维护了汇总计数
- 框架自带的 `error_rate_rule()` 消息模板引用了未定义的 `{threshold}`，
  触发时抛 KeyError 被静默吞掉；示例用等价的显式 `AlertRule` 演示（见 spider.py 注释）
- `check_redis` / `check_mongodb` 需要对应服务，本演示未启用；集群环境注册即可
