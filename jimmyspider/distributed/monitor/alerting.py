"""
分布式告警系统

支持多渠道告警:
  - Webhook（企业微信/钉钉/飞书/Slack）
  - Email
  - 控制台日志

告警规则:
  - 连续失败 N 次 → WARNING
  - 错误率超过阈值 → CRITICAL
  - 队列堆积超过阈值 → WARNING
  - 代理池可用率低于阈值 → CRITICAL
  - Worker 心跳超时 → CRITICAL
"""

import json
import time
import asyncio
from typing import Optional, Callable
from collections import defaultdict

import aiohttp


class AlertRule:
    """告警规则"""
    def __init__(self, name: str, condition: Callable, severity: str = "warning",
                 cooldown_seconds: float = 300,
                 message_template: str = "{name}: {value}"):
        self.name = name
        self.condition = condition  # Callable[[MetricsCollector], bool]
        self.severity = severity   # info / warning / critical
        self.cooldown_seconds = cooldown_seconds
        self.message_template = message_template
        self._last_triggered: float = 0

    def should_trigger(self) -> bool:
        return time.time() - self._last_triggered > self.cooldown_seconds

    def triggered(self):
        self._last_triggered = time.time()


class AlertChannel:
    """告警通道基类"""
    async def send(self, title: str, message: str, severity: str) -> bool:
        raise NotImplementedError


class WebhookChannel(AlertChannel):
    """Webhook 告警通道（企业微信/钉钉/飞书/Slack）"""

    TEMPLATES = {
        "wecom": {
            "url": "{webhook_url}",
            "body": {"msgtype": "markdown", "markdown": {"content": "## {title}\n{message}"}},
        },
        "dingtalk": {
            "url": "{webhook_url}",
            "body": {"msgtype": "markdown", "markdown": {"title": "{title}", "text": "{message}"}},
        },
        "feishu": {
            "url": "{webhook_url}",
            "body": {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "{title}"}}, "elements": [{"tag": "markdown", "content": "{message}"}]}},
        },
        "slack": {
            "url": "{webhook_url}",
            "body": {"text": "*{title}*\n{message}"},
        },
    }

    def __init__(self, channel_type: str, webhook_url: str):
        self.channel_type = channel_type
        self.webhook_url = webhook_url
        self.template = self.TEMPLATES.get(channel_type, self.TEMPLATES["slack"])

    async def send(self, title: str, message: str, severity: str) -> bool:
        try:
            body_str = json.dumps(self.template["body"])
            body_str = body_str.replace("{title}", title).replace("{message}", message)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    data=body_str.encode(),
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            print(f"[Alert] Webhook 发送失败: {e}")
            return False


class EmailChannel(AlertChannel):
    """邮件告警通道"""
    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str,
                 to_addrs: list[str]):
        self.smtp_host = smtp_host; self.smtp_port = smtp_port
        self.username = username; self.password = password
        self.to_addrs = to_addrs

    async def send(self, title: str, message: str, severity: str) -> bool:
        import smtplib
        from email.mime.text import MIMEText
        try:
            msg = MIMEText(message, "plain", "utf-8")
            msg["Subject"] = f"[{severity.upper()}] {title}"
            msg["From"] = self.username
            msg["To"] = ", ".join(self.to_addrs)
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                server.login(self.username, self.password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"[Alert] 邮件发送失败: {e}")
            return False


class ConsoleChannel(AlertChannel):
    """控制台告警"""
    async def send(self, title: str, message: str, severity: str) -> bool:
        prefix = {"info": "ℹ", "warning": "⚠", "critical": "🚨"}.get(severity, "⚠")
        print(f"{prefix} [{severity.upper()}] {title}: {message}")
        return True


class AlertManager:
    """
    告警管理器

    注册规则 + 通道，定期评估并触发告警。

    使用:
        mgr = AlertManager()
        mgr.add_rule(AlertRule("high_error_rate", lambda m: m.summary()["success_rate"] < 0.8))
        mgr.add_channel(WebhookChannel("wecom", "https://qyapi.weixin.qq.com/..."))
        await mgr.evaluate(metrics_collector)
    """

    SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

    def __init__(self, min_severity: str = "warning"):
        self.min_severity = min_severity
        self._rules: list[AlertRule] = []
        self._channels: list[AlertChannel] = []
        self._alert_history: list[dict] = []

    def add_rule(self, rule: AlertRule) -> "AlertManager":
        self._rules.append(rule)
        return self

    def add_channel(self, channel: AlertChannel) -> "AlertManager":
        self._channels.append(channel)
        return self

    async def evaluate(self, metrics) -> list[dict]:
        """评估所有规则并触发告警"""
        triggered = []
        for rule in self._rules:
            if not rule.should_trigger():
                continue
            if self.SEVERITY_ORDER.get(rule.severity, 0) < self.SEVERITY_ORDER.get(self.min_severity, 0):
                continue
            try:
                if rule.condition(metrics):
                    rule.triggered()
                    msg = rule.message_template.format(name=rule.name, value="triggered")
                    for channel in self._channels:
                        await channel.send(rule.name, msg, rule.severity)
                    triggered.append({"rule": rule.name, "severity": rule.severity, "message": msg})
                    self._alert_history.append(triggered[-1])
            except Exception as e:
                print(f"[Alert] 规则评估失败 {rule.name}: {e}")
        return triggered

    # ---- 内置规则工厂 ----
    @staticmethod
    def error_rate_rule(threshold: float = 0.3, cooldown: float = 600) -> AlertRule:
        def check(m):
            s = m.summary()
            return s["total_requests"] > 10 and (1 - s["success_rate"]) > threshold
        return AlertRule("high_error_rate", check, severity="critical",
                        cooldown_seconds=cooldown,
                        message_template="错误率超过 {threshold}")

    @staticmethod
    def queue_backlog_rule(max_depth: int = 10000, cooldown: float = 300) -> AlertRule:
        return AlertRule("queue_backlog",
                        lambda m: any(s.value > max_depth for s in m.snapshot() if "queue_depth" in s.name),
                        severity="warning", cooldown_seconds=cooldown)

    @staticmethod
    def proxy_pool_low_rule(min_alive: int = 3, cooldown: float = 300) -> AlertRule:
        return AlertRule("proxy_pool_low",
                        lambda m: any(s.value < min_alive for s in m.snapshot() if "proxy_alive" in s.name),
                        severity="critical", cooldown_seconds=cooldown)
