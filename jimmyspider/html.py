"""
jimmySpider - HTML 清洗与保存模块

用于清洗和按日期归档保存网页 HTML 内容。
"""

import os
import re
from datetime import date

from jimmyspider.config import get_config


class handleHTML:
    """HTML 清洗与保存"""

    def __init__(self, pro_name):
        self.pro_name = pro_name
        config = get_config()
        self.file_path = os.path.join(config.DATA_DIR, pro_name)
        self.sub_path_name = "html_by_date"

        os.makedirs(self.file_path, exist_ok=True)
        os.makedirs(
            os.path.join(self.file_path, self.sub_path_name), exist_ok=True
        )

    @staticmethod
    def clean_html(html):
        """清洗 HTML：移除 style、link、注释，保留 script 和 meta"""
        if not html:
            return ""
        html = str(html)
        html = re.sub(
            r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE
        )
        html = re.sub(r"<link[^>]*>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r"\n+", "\n", html, flags=re.IGNORECASE)
        return html

    def save_html(self, html, file_id, clean=True):
        """保存 HTML 到按日期归档的文件"""
        if not file_id:
            print("❌ 文件ID不能为空")
            return None
        file_full_path = self.get_full_path(file_id)
        if clean:
            html = self.clean_html(html)
        with open(file_full_path, "w", encoding="utf-8") as f:
            f.write(html)
        return file_full_path

    def get_full_path(self, file_id):
        """生成带日期的完整文件路径"""
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        today_path = os.path.join(
            self.file_path, self.sub_path_name, today_str
        )
        os.makedirs(today_path, exist_ok=True)
        return os.path.join(today_path, file_id + ".html")
