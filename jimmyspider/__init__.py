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
    "HandleMongoDB",
    "FileDownloader",
    "LogPrint",
    "generate_string_id",
    "safe_extract_json",
    "extractSoup",
    "get_config",
]
