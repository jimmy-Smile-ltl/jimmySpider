"""
tiantian_fund — 天天基金网基金数据抓取（fund.eastmoney.com）

分页抓取基金排名列表（rankhandler.aspx），再并发抓取每只基金的
累计净值曲线（pingzhongdata/{fcode}.js）。MySQL 双表落库 + CSV 本地兜底，
Redis 断点续爬 + 失败任务自动重试。迁移自北大信研院 pro46 天天基金。
"""

import concurrent.futures
import csv
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

import json5
import requests

from jimmyspider import Cache, JimmySpider
from jimmyspider.config import get_config
from jimmyspider.mysql import MySQLHandler

PROJECT_DIR = Path(__file__).parent


class Spider(JimmySpider):
    """天天基金爬虫：列表页（全量基金排名）+ 详情页（累计净值序列）。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("db_type", "mysql")
        kwargs.setdefault("test_url", "https://fund.eastmoney.com/data/fundranking.html")
        kwargs.setdefault("table_name", "tiantian_fund")
        super().__init__(**kwargs)

        self.site = "https://fund.eastmoney.com/"
        self.source = "天天基金"
        self.category = "证券"
        self.language = "zh"
        self.detail_table = f"{self.table_name}_detail"
        self.db_detail = MySQLHandler(
            db_name=get_config().MYSQL_DB, table_name=self.detail_table
        )

        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://fund.eastmoney.com/data/fundranking.html",
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

        # Redis 断点缓存
        self.log_page = Cache(f"log_page_{self.table_name}")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self.error_detail_set = Cache(f"{self.table_name}_error_detail_set")

        # CSV 本地兜底（字段与数据库一一对应）
        self.list_csv_path = PROJECT_DIR / f"{self.table_name}_list.csv"
        self.detail_csv_path = PROJECT_DIR / f"{self.table_name}_detail.csv"
        self.list_fieldnames = [
            "fcode", "short_name", "pinyin", "date", "unit_nav", "accumulated_nav",
            "daily_growth_rate", "last_week", "last_month", "last_3_month",
            "last_6_month", "last_1_year", "last_2_year", "last_3_year",
            "this_year", "since_inception", "establish_date", "is_buyable",
            "custom_period", "original_rate", "current_rate", "discount", "growth_color",
        ]
        self.detail_fieldnames = ["fcode", "short_name", "TradingDay", "Return"]
        self.thread_lock = Lock()  # 多线程写入 CSV 时加锁

        self.init_csv()
        self.create_table()

    # ---- 建表 ----

    def create_table(self) -> None:
        """创建列表/详情两张表；残留测试数据（<10 条）时先删表并重置缓存。"""
        create_list_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.table_name}`(
            `id` BIGINT AUTO_INCREMENT COMMENT '主键ID',
            `fcode` VARCHAR(32) NOT NULL COMMENT '基金代码',
            `short_name` VARCHAR(256) COMMENT '基金简称',
            `pinyin` VARCHAR(128) COMMENT '拼音缩写',
            `date` DATE COMMENT '净值日期',
            `unit_nav` DECIMAL(18,6) DEFAULT 0 COMMENT '单位净值',
            `accumulated_nav` DECIMAL(18,6) DEFAULT 0 COMMENT '累计净值',
            `daily_growth_rate` VARCHAR(32) COMMENT '日增长率',
            `last_week` VARCHAR(32), `last_month` VARCHAR(32), `last_3_month` VARCHAR(32),
            `last_6_month` VARCHAR(32), `last_1_year` VARCHAR(32), `last_2_year` VARCHAR(32),
            `last_3_year` VARCHAR(32), `this_year` VARCHAR(32), `since_inception` VARCHAR(32),
            `establish_date` DATE COMMENT '成立日期',
            `is_buyable` TINYINT(1) DEFAULT 0 COMMENT '是否可购',
            `custom_period` VARCHAR(32), `original_rate` VARCHAR(32),
            `current_rate` VARCHAR(32), `discount` VARCHAR(32),
            `growth_color` VARCHAR(16) COMMENT '日增长率颜色(正红负绿)',
            `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uniq_fcode` (`fcode`),
            KEY `idx_list_date` (`date`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        create_detail_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.detail_table}`(
            `id` BIGINT AUTO_INCREMENT COMMENT '主键ID',
            `fcode` VARCHAR(32) NOT NULL COMMENT '基金代码',
            `short_name` VARCHAR(256) COMMENT '基金简称',
            `TradingDay` DATE NOT NULL COMMENT '交易日期',
            `Return` DECIMAL(18,6) COMMENT '累计收益率/累计净值',
            `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uniq_fcode_tradingday` (`fcode`, `TradingDay`),
            KEY `idx_trading_day` (`TradingDay`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        is_delete_list = self.db_manager.drop_table(max_num=10)
        is_delete_detail = self.db_detail.drop_table(max_num=10)
        if is_delete_list or is_delete_detail:
            # 表被重建，Redis 断点也一并重置
            self.log_page.clear_value()
            self.error_page_set.clear_value()
            self.error_detail_set.clear_value()
        self.db_manager.create_table(create_list_sql)
        self.db_detail.create_table(create_detail_sql)

    # ---- CSV 兜底 ----

    def init_csv(self) -> None:
        self._append_csv_rows(self.list_csv_path, self.list_fieldnames, [])
        self._append_csv_rows(self.detail_csv_path, self.detail_fieldnames, [])

    @staticmethod
    def _append_csv_rows(file_path: Path, fieldnames: List[str], rows: List[Dict]) -> None:
        """追加写入 CSV；文件为空时先写表头。"""
        with open(file_path, mode="a+", encoding="utf-8-sig", newline="") as csvfile:
            csvfile.seek(0, 2)
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if csvfile.tell() == 0:
                writer.writeheader()
            if rows:
                writer.writerows(rows)

    # ---- 列表页 ----

    def get_list(self, page_num: int) -> Optional[requests.Response]:
        """请求基金排名列表接口（最近一年区间）。"""
        today = datetime.now()
        params = {
            "op": "ph", "dt": "kf", "ft": "all", "rs": "", "gs": "0",
            "sc": "1nzf", "st": "desc",
            "sd": (today - timedelta(days=365)).strftime("%Y-%m-%d"),
            "ed": today.strftime("%Y-%m-%d"),
            "qdii": "", "tabSubtype": ",,,,,",
            "pi": str(page_num), "pn": "50", "dx": "1",
            "v": random.random(),
        }
        url = "https://fund.eastmoney.com/data/rankhandler.aspx"
        return self.single_fetcher.fetch(url=url, params=params, headers=self.headers)

    def extract_list(self, response) -> Tuple[List[Dict], Optional[int]]:
        """解析 'var rankData = {...};' 结构，返回 (基金列表, 总页数)。"""
        if not response:
            return [], None
        pattern = re.compile(r"var\s*rankData\s*=\s*(.*?)\s*;", re.S)
        match = pattern.search(response.text)
        if not match:
            self.log_print.error("extract_list 未找到 rankData 数据")
            return [], None
        try:
            data_json = json5.loads(match.group(1))
            fund_list = [self.parse_fund_to_dict(item) for item in data_json.get("datas", [])]
            return fund_list, data_json.get("allPages")
        except Exception as e:
            self.log_print.error(f"extract_list 解析数据失败: {e}")
            return [], None

    @staticmethod
    def parse_fund_to_dict(raw_item: str) -> Dict:
        """单条基金字符串（逗号分隔）转为字段字典，缺失位补空。"""
        arr = raw_item.split(",")
        if len(arr) < 22:
            arr.extend([""] * (22 - len(arr)))

        def to_float(val: str) -> float:
            return float(val) if val else 0.0

        fund_dict = {
            "fcode": arr[0], "short_name": arr[1], "pinyin": arr[2], "date": arr[3],
            "unit_nav": to_float(arr[4]), "accumulated_nav": to_float(arr[5]),
            "daily_growth_rate": arr[6], "last_week": arr[7], "last_month": arr[8],
            "last_3_month": arr[9], "last_6_month": arr[10], "last_1_year": arr[11],
            "last_2_year": arr[12], "last_3_year": arr[13], "this_year": arr[14],
            "since_inception": arr[15], "establish_date": arr[16],
            "is_buyable": arr[17] in ["1", "2", "3", "8", "9"],
            "custom_period": arr[18], "original_rate": arr[19],
            "current_rate": arr[20], "discount": arr[21],
        }
        # 正红负绿
        try:
            v = float(arr[6])
            fund_dict["growth_color"] = "red" if v > 0 else "green" if v < 0 else "black"
        except ValueError:
            fund_dict["growth_color"] = "black"
        return fund_dict

    def save_list(self, fund_dict_list: List[Dict]) -> None:
        if not fund_dict_list:
            self.log_print.error("save_list 接收到的 fund_dict_list 为空，无法保存")
            return
        self._append_csv_rows(self.list_csv_path, self.list_fieldnames, fund_dict_list)
        self.save_result(fund_dict_list)  # 写 MySQL（fcode 唯一键 upsert）
        self.log_print.print(f"已保存 {len(fund_dict_list)} 条基金列表数据")

    # ---- 详情页 ----

    def get_detail(self, fcode: str) -> Optional[requests.Response]:
        """请求基金累计净值曲线 JS 数据。"""
        url = f"https://fund.eastmoney.com/pingzhongdata/{fcode}.js"
        params = {"v": datetime.now().strftime("%Y%m%d%H%M%S")}
        return self.single_fetcher.fetch(url=url, params=params, headers=self.headers)

    def extract_detail(self, response, detail_dict: Dict) -> List[Dict]:
        """解析 Data_ACWorthTrend，毫秒时间戳转日期，输出净值序列。"""
        if not response:
            return []
        fcode = detail_dict["fcode"]
        short_name = detail_dict["short_name"]
        pattern = re.compile(r"var\s*Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", re.S)
        match = pattern.search(response.text)
        if not match:
            self.log_print.error(f"基金 {fcode} 未找到 Data_ACWorthTrend 数据")
            return []
        try:
            detail_list = []
            for item in json5.loads(match.group(1)):
                trading_day = datetime.fromtimestamp(item[0] / 1000).strftime("%Y-%m-%d")
                detail_list.append({
                    "fcode": fcode,
                    "short_name": short_name,
                    "TradingDay": trading_day,
                    "Return": item[1],
                })
            return detail_list
        except Exception as e:
            self.log_print.error(f"基金 {fcode} 详情解析失败: {e}")
            return []

    def handle_detail(self, detail_dict: Dict) -> Optional[int]:
        """处理单只基金详情：请求 -> 解析 -> 加锁写入，单只失败不阻断整页。"""
        fcode = detail_dict.get("fcode")
        if not fcode:
            raise ValueError("detail_dict 必须包含 'fcode' 键")
        response = self.get_detail(fcode)
        if not response:
            self.error_detail_set.add_to_set(json.dumps(detail_dict, ensure_ascii=False))
            self.log_print.error(f"获取基金 {fcode} 详情失败")
            return 0
        detail_list = self.extract_detail(response, detail_dict)
        with self.thread_lock:
            self.save_detail(detail_list)
        return len(detail_list)

    def save_detail(self, detail_dict_list: List[Dict]) -> None:
        if not detail_dict_list:
            self.log_print.error("save_detail 接收到的 detail_info 为空，无法保存")
            return
        self._append_csv_rows(self.detail_csv_path, self.detail_fieldnames, detail_dict_list)
        self.db_detail.insert_data_list(data_list=detail_dict_list)

    def handle_details(self, fund_dict_list: List[Dict]) -> None:
        """线程池并发抓取一页中所有基金的详情。"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(self.handle_detail, fund_dict): fund_dict
                for fund_dict in fund_dict_list
            }
            total = len(fund_dict_list)
            finished = 0
            for future in concurrent.futures.as_completed(futures):
                fund_dict = futures[future]
                finished += 1
                try:
                    insert_num = future.result()
                    self.log_print.print(
                        f"详情进度 {finished}/{total} 基金 {fund_dict['fcode']} "
                        f"{fund_dict.get('short_name')} 插入 {insert_num} 条"
                    )
                except Exception as e:
                    self.log_print.error(f"基金 {fund_dict.get('fcode')} 详情处理失败: {e}")
                    self.error_detail_set.add_to_set(
                        json.dumps(fund_dict, ensure_ascii=False)
                    )

    # ---- 失败重试 ----

    def handle_error_page(self) -> bool:
        """重试失败列表页，成功后从错误集合移除。"""
        error_pages = self.error_page_set.get_set_members()
        if not error_pages:
            self.log_print.print("没有需要重试的错误页面")
            return True
        self.log_print.print(f"开始重试 {len(error_pages)} 个错误页面")
        for page in error_pages:
            response = self.get_list(page_num=int(page))
            if not response:
                self.log_print.error(f"重试第 {page} 页失败，继续保留在错误集合中")
                continue
            fund_dict_list, _ = self.extract_list(response)
            self.save_list(fund_dict_list)
            self.error_page_set.remove_from_set(page)
            self.handle_details(fund_dict_list)
        return len(self.error_page_set.get_set_members()) == 0

    def handle_error_detail(self) -> bool:
        """重试失败详情任务，成功后从错误集合移除。"""
        error_details = self.error_detail_set.get_set_members()
        if not error_details:
            self.log_print.print("没有需要重试的错误详情")
            return True
        self.log_print.print(f"开始重试 {len(error_details)} 个错误详情")
        for detail_str in error_details:
            detail_dict = json.loads(detail_str)
            try:
                self.handle_detail(detail_dict)
                self.error_detail_set.remove_from_set(detail_str)
            except Exception as e:
                self.log_print.error(f"重试基金 {detail_dict.get('fcode')} 失败: {e}")
        return len(self.error_detail_set.get_set_members()) == 0

    def handle_error(self) -> None:
        """统一错误重试入口，最多 3 轮，避免死循环。"""
        for retry in range(3):
            self.log_print.print(f"错误处理尝试 {retry + 1}/3")
            pages_cleared = self.handle_error_page()
            details_cleared = self.handle_error_detail()
            if pages_cleared and details_cleared:
                self.log_print.print("所有错误页面和详情已成功处理")
                break

    # ---- 主流程 ----

    def run(self, end_page: Optional[int] = None) -> None:
        """列表页逐页抓取 + 详情并发抓取 + 失败统一重试。

        end_page 用于调试提前停止；None 表示抓到最后一页。
        """
        current_page = self.log_page.get_int(default=1)
        max_page = current_page + 1  # 先给临时上界，拿到 allPages 后覆盖
        while current_page <= max_page:
            self.log_page.record_int(current_page)
            response = self.get_list(page_num=current_page)
            if not response:
                self.log_print.error(f"第 {current_page} 页列表请求失败，跳过")
                self.error_page_set.add_to_set(current_page)
                current_page += 1
                continue
            fund_dict_list, max_page_temp = self.extract_list(response)
            if max_page_temp:
                max_page = max_page_temp
            self.log_print.print(
                f"当前页: {current_page}/{max_page}，本页基金数量: {len(fund_dict_list)}"
            )
            self.save_list(fund_dict_list)
            self.handle_details(fund_dict_list)
            if end_page and current_page >= end_page:
                self.log_print.print(f"达到指定结束页 {end_page}，提前结束")
                break
            current_page += 1
        self.log_print.print(f"{current_page}/{max_page} 列表页爬取完成")
        self.handle_error()


if __name__ == "__main__":
    Spider(pro_path=PROJECT_DIR).run()  # 调试可传 end_page=3
