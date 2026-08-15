"""
统一的 Request / Response 数据结构

两种调度器共享:
  - Request: 爬取请求
  - Response: 爬取响应
"""

import json
import time
from typing import Any, Optional
from dataclasses import dataclass, field


@dataclass
class Request:
    """爬取请求"""
    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    body: Optional[bytes] = None
    meta: dict = field(default_factory=dict)   # 传递数据用
    callback: str = "parse"                     # 回调方法名
    dont_filter: bool = False                   # 跳过去重
    priority: int = 0                           # 优先级 (越大越优先)
    _id: str = ""                               # 去重 ID (自动生成)

    def __post_init__(self):
        if not self._id:
            self._id = self.url

    def __hash__(self):
        return hash(self._id)

    def __eq__(self, other):
        return isinstance(other, Request) and self._id == other._id

    def replace(self, **kwargs) -> "Request":
        """创建修改后的副本"""
        data = {
            "url": self.url, "method": self.method, "headers": dict(self.headers),
            "body": self.body, "meta": dict(self.meta), "callback": self.callback,
            "dont_filter": self.dont_filter, "priority": self.priority, "_id": self._id,
        }
        data.update(kwargs)
        return Request(**data)


@dataclass
class Response:
    """爬取响应"""
    url: str
    status: int = 200
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    text: str = ""
    request: Optional[Request] = None
    meta: dict = field(default_factory=dict)
    _encoding: str = "utf-8"

    def __post_init__(self):
        if not self.text and self.body:
            try:
                self.text = self.body.decode(self._encoding)
            except UnicodeDecodeError:
                self.text = self.body.decode(self._encoding, errors="replace")
        # 继承请求的 meta（Scrapy 兼容行为）
        if self.request and self.request.meta:
            self.meta = {**self.request.meta, **self.meta}

    @classmethod
    def from_body(cls, url: str, status: int, headers: dict, body: bytes,
                  request: Request = None) -> "Response":
        return cls(url=url, status=status, headers=headers, body=body, request=request)

    def xpath(self, expr: str):
        """XPath 选择器 (需 lxml)"""
        try:
            from lxml import etree
            tree = etree.HTML(self.text)
            return tree.xpath(expr)
        except ImportError:
            raise ImportError("需要安装 lxml: pip install lxml")

    def css(self, expr: str):
        """CSS 选择器 (需 parsel)"""
        try:
            from parsel import Selector
            return Selector(text=self.text).css(expr)
        except ImportError:
            raise ImportError("需要安装 parsel: pip install parsel")
