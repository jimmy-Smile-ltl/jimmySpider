"""
jimmySpider - 日期解析工具

支持多种日期格式的智能解析，包括相对时间（"昨天"、"几分钟前"）。
"""

from datetime import datetime, timedelta

import dateparser
from dateutil import parser


class HandleDatetime:
    """日期时间工具类"""

    @staticmethod
    def get_current_datetime():
        """获取当前时间"""
        return datetime.now()

    @staticmethod
    def format_datetime(dt, fmt="%Y-%m-%d %H:%M:%S"):
        """格式化时间"""
        return dt.strftime(fmt)

    @staticmethod
    def parse_datetime(date_str, fmt="%Y-%m-%d %H:%M:%S"):
        """解析字符串为时间"""
        return datetime.strptime(date_str, fmt)

    @staticmethod
    def add_days(dt, days):
        """在时间上加上指定天数"""
        return dt + timedelta(days=days)

    @staticmethod
    def convert_date_robust(date_str: str) -> str | None:
        """智能解析各种日期格式为 'YYYY-MM-DD HH:MM:SS' 字符串。

        支持格式:
        - 'November 15, 2024'
        - 'Mar 1, 2022'
        - 'Jun 24, 2025 15:30:00 +0800'
        - '昨天'、'3分钟前' 等相对时间
        """
        if not isinstance(date_str, str):
            print(
                f"handleDatetime Error: Input must be a string, "
                f"but got {type(date_str)} value {date_str}"
            )
            return None

        date_str = date_str.strip()

        try:
            dt_object = parser.parse(date_str)
            return dt_object.strftime("%Y-%m-%d %H:%M:%S")
        except (parser.ParserError, ValueError):
            try:
                now = datetime.now()
                dt_object = dateparser.parse(
                    date_str, settings={"RELATIVE_BASE": now}
                )
                return dt_object.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return None


# 模块级函数，方便直接导入使用
convert_date_robust = HandleDatetime.convert_date_robust
