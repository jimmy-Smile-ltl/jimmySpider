"""
jimmySpider - Python 爬虫框架

一个成熟、灵活的 Python 爬虫框架，提供：
- 6 种请求处理器（单线程/多线程/异步 + curl_cffi TLS 指纹伪装）
- MongoDB / MySQL / PostgreSQL 三种数据库支持
- Redis 断点续爬（页码/日期/错误 URL 缓存）
- 代理管理（隧道代理 + Clash 多节点代理池）
- 文件下载器（多线程/异步/curl_cffi）
- 日志系统（控制台 + 按天轮转文件）
- HTML 清洗与归档
- 日期智能解析

分布式子系统（可选，按需导入）：
- jimmyspider.mq          消息队列（Redis/Kafka/RabbitMQ 统一接口）
- jimmyspider.scheduler   调度引擎（Scrapy 式 / AioSpider 式）
- jimmyspider.parser      智能解析（5 层成本级联 + LLM 兜底）
- jimmyspider.distributed 分布式代理 / 存储 / 监控
"""

from jimmyspider.spider import JimmySpider
from jimmyspider.cache import Cache
from jimmyspider.request import (
    SingleRequestHandler,
    AsyncRequestHandler,
    ThreadRequestHandler,
    CurlRequestHandler,
    CurlCffiThreadRequestHandler,
    CurlCffiAsyncRequestHandler,
    RFPDupeFilter,
    DomainRateLimiter,
)
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.file import FileDownloader
from jimmyspider.log_print import LogPrint
from jimmyspider.tool import generate_string_id, safe_extract_json
from jimmyspider.soup import extractSoup
from jimmyspider.config import get_config

__version__ = "1.0.0"
__author__ = "Jimmy Smile"
__license__ = "MIT"

__all__ = [
    "JimmySpider",
    "Cache",
    "SingleRequestHandler",
    "AsyncRequestHandler",
    "ThreadRequestHandler",
    "CurlRequestHandler",
    "CurlCffiThreadRequestHandler",
    "CurlCffiAsyncRequestHandler",
    "RFPDupeFilter",
    "DomainRateLimiter",
    "HandleMongoDB",
    "FileDownloader",
    "LogPrint",
    "generate_string_id",
    "safe_extract_json",
    "extractSoup",
    "get_config",
]
