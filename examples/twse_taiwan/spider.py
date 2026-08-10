"""
示例：台湾证券交易所公开资讯观测站 (twse_taiwan)

抓取 MOPS 公开资讯观测站（台湾证券交易所辖下）的重大讯息公告：
- 数据接口：https://mops.twse.com.tw/mops/api/t146sb10 —— POST JSON，
  按「民国年份 × 市场类别（上市/上柜/兴柜/公开发行）」分片请求
- 返回的公告表格列为动态结构（header + titles + data），需按 titles 逐列对齐组装
- 日期为民国纪年（如 115/03/16），入库前转换为公历
- 详情页使用 ThreadPoolExecutor 5 线程并发抓取，提取正文并保存 HTML 快照
- Redis 断点续爬：分片完成集合 + 公告类型去重 + 失败分片/失败详情自动重试
"""

import datetime
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        super(Spider, self).__init__(*args, **kwargs)
        # Redis 断点缓存（key 前缀 = table_name = twse_taiwan）
        self.completed_segment_set = Cache(f"{self.table_name}_completed_segment_set")
        self.complete_detail_type = Cache(f"{self.table_name}_complete_detail_type")
        self.error_year_type_set = Cache(f"{self.table_name}_error_year_type_set")
        self.error_detail_set = Cache(f"{self.table_name}_error_detail_set")
        self.list_api_url = "https://mops.twse.com.tw/mops/api/t146sb10"
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.marketKinds = {
            "sii": "上市",
            "otc": "上櫃",
            "rotc": "興櫃",
            "pub": "公開發行"
        }
        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Origin": "https://mops.twse.com.tw",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "content-type": "application/json",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\""
        }
        self.header_detail = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\""
        }

        self.cookies = {}

        self.start_year = 106  # 民国 106 年 = 公元 2017
        self.end_year = 115    # 民国 115 年 = 公元 2026

    def _encode_cache(self, value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value):
        return json.loads(value)

    def get_one_year_type(self, year: int, marketKind: str):
        if year > 1911:
            year = year - 1911
        data = {
            "scopeType": "2",
            "companyId": "",
            "dateType": "1",
            "firstDate": f"{year}0101",
            "lastDate": f"{year+1}0101",
            "marketKind": f"{marketKind}",
            "announcementBasis": "0",
            "dateRangeType": "",
            "announcementType": "1",
            "sort": "1",
            "encodeURIComponent": 1,
            "step": 1,
            "firstin": 1,
            "off": 1
        }
        data_json = json.dumps(data, separators=(',', ':'))
        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=data_json,
            check_size=False,
            method="POST",
        )
        return response

    def extract_one_year_type(self, response):
        data_list_all = []
        if response.status_code == 200:
            try:
                res_json = response.json()
                message = res_json.get("message")
                code = res_json.get("code")
                self.log_print.print(f"code = {code} , message = {message}")
                result = res_json.get("result")
                if not result:
                    return data_list_all
                for result_item in result:
                    data_list_item = self.handle_result_item(result_item)
                    if data_list_item:
                        data_list_all.append(data_list_item)
                return data_list_all
            except Exception as e:
                self.log_print.error(f"error in extract_one_year_type: {str(e)} check temp.html")
                with open(self.project_root / "temp.html", "w", encoding="utf-8") as f:
                    f.write(response.text)

        return data_list_all

    def roc_to_gregorian(self, roc_date_str, sep='/', output_format='%Y-%m-%d'):
        """
        Convert a ROC (Taiwan) date string (e.g., '115/03/16') to Gregorian.
        Handles 1-3 digit years, any separator (default '/').
        """
        parts = roc_date_str.split(sep)
        if len(parts) != 3:
            raise ValueError(f"Expected format YYY/MM/DD, got {roc_date_str}")
        roc_year_str, month_str, day_str = parts
        gregorian_year = int(roc_year_str) + 1911
        # Validate that month/day are valid (raises ValueError if not)
        gregorian_dt = datetime.datetime(gregorian_year, int(month_str), int(day_str))
        return gregorian_dt.strftime(output_format)

    def handle_result_item(self, result_item):
        data_list = []
        data = result_item.get("data", [])
        if len(data) == 0:
            return {}
        header = result_item.get("header")
        titles = result_item.get("titles")
        for data_item in data:
            if len(data_item) == len(titles):
                temp_dict = {"类型": header}
                for idx in range(len(data_item)):
                    if titles[idx].get("main") == "公告日期":
                        # 转换日期格式
                        temp_dict[titles[idx].get("main")] = self.roc_to_gregorian(data_item[idx])
                    else:
                        temp_dict[titles[idx].get("main")] = data_item[idx]
                data_list.append(temp_dict)
            else:
                raise ValueError(f"标题与内容长度不匹配，标题长度：{len(titles)}，内容长度：{len(data_item)}")
        self.log_print.print(f"handle_result_item: header: {header} , data_list length: {len(data_list)}")
        return {
            "header": header,
            "data_list": data_list
        }

    def handle_one_year_type(self, year, marketKind: str):
        response = self.get_one_year_type(year, marketKind)
        insert_num = 0
        if response:
            data_dict_list = self.extract_one_year_type(response)
            page_info = {"year": year, "marketKind": marketKind}
            if not data_dict_list:
                self.log_print.print(
                    f"data_list empty, year:{year} marketKind:{marketKind}"
                )
                self.error_year_type_set.add_to_set(self._encode_cache(page_info))
                return None
            else:
                complete_detail_types = self.complete_detail_type.get_set_members()
                for idx, data_dict_header in enumerate(data_dict_list, 1):
                    if data_dict_header['header'] in complete_detail_types:
                        self.log_print.print(f"{data_dict_header['header']} 已经处理过  跳过 year:{year} marketKind:{marketKind}")
                        continue
                    self.log_print.print(f"现在开始处理 {year} {marketKind} 进度： {idx} / {len(data_dict_list)} header:  {data_dict_header['header']} ")
                    data_list = data_dict_header.get("data_list")
                    insert_list = self.handle_detail_batch(detail_dict_list=data_list)
                    insert_num += len(insert_list)
                    self.save_result(insert_list=insert_list)
                    self.complete_detail_type.add_to_set(data_dict_header['header'])
                    self.log_print.print(
                        f"year:{year} marketKind:{marketKind}  {data_dict_header['header']} 详情采集成功 {len(insert_list)} 条"
                    )
                self.log_print.print(f" year:{year} marketKind:{marketKind} 采集完成，insert_num: {insert_num}")
            return insert_num
        else:
            page_info = {"year": year, "marketKind": marketKind}
            self.log_print.print(
                f"year:{year} marketKind:{marketKind} list 采集失败"
            )
            self.error_year_type_set.add_to_set(self._encode_cache(page_info))
            return None

    def extract_one_detail(self, detail_response):
        if not detail_response:
            return None
        detail_soup = BeautifulSoup(detail_response.text, "html.parser")
        return self.extract_soup.extract_content(detail_soup)

    def handle_one_detail(self, detail_dict, record_error=True):
        detail_url = detail_dict.get("內容", "") or detail_dict.get("", "")
        if not detail_url:
            if record_error:
                self.error_detail_set.add_to_set(self._encode_cache(detail_dict))
            self.log_print.warning(f"missing 详情链接: {detail_dict}")
            return False
        response = self.single_fetcher.fetch(
            detail_url,
            headers=self.header_detail,
            cookies=self.cookies,
            check_size=False
        )
        if response:
            file_id = detail_dict.get("_id") or generate_string_id(detail_url)
            detail_dict["_id"] = file_id
            detail_dict["detail_url"] = detail_url
            html_path = self.html_saver.save_html(html=response.text, file_id=file_id)
            detail_dict["html_path"] = html_path
            detail_dict["正文内容"] = self.extract_one_detail(response)
            if "" in detail_dict:
                detail_dict["內容"] = detail_dict.pop("")
            return detail_dict
        self.log_print.print(f"detail_url:{detail_url} 详情页采集失败 问题已记录")
        if record_error:
            self.error_detail_set.add_to_set(self._encode_cache(detail_dict))
        return False

    def handle_detail_batch(self, detail_dict_list):
        # 按照 header 再次细分一下 不然 一批也有几千条
        results = []
        if not detail_dict_list:
            return results
        all_count = len(detail_dict_list)
        finished_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_detail = {
                executor.submit(self.handle_one_detail, detail_dict): detail_dict
                for detail_dict in detail_dict_list
            }
            for future in as_completed(future_to_detail):
                detail_dict = future_to_detail[future]
                try:
                    result = future.result()
                    if result:
                        finished_count += 1
                        results.append(result)
                        print(f"handle_detail_batch : 进度 ：{finished_count}/{all_count} ", end="\r")
                except Exception as e:
                    self.log_print.error(
                        f"error in handle_detail_batch for url {detail_dict.get('详情链接')}: {str(e)}"
                    )
                    self.error_detail_set.add_to_set(self._encode_cache(detail_dict))
        return results

    def handle_error_detail(self):
        error_detail_set = list(self.error_detail_set.get_set_members())
        if not error_detail_set:
            self.log_print.print("handle_error_detail: no error details to retry")
            return True
        batch_size = 20
        total = len(error_detail_set)
        for start in range(0, total, batch_size):
            batch_keys = error_detail_set[start:start + batch_size]
            self.log_print.print(
                f"handle_error_detail retry batch {start // batch_size + 1} "
                f"size={len(batch_keys)} total={total}"
            )
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_key = {}
                for key in batch_keys:
                    detail_dict = self._decode_cache(key)
                    future = executor.submit(self.handle_one_detail, detail_dict, False)
                    future_to_key[future] = key
                for future in as_completed(future_to_key):
                    key = future_to_key[future]
                    try:
                        result = future.result()
                        if result:
                            self.error_detail_set.remove_from_set(key)
                            self.save_result(insert_list=result)
                    except Exception as e:
                        self.log_print.error(f"error in handle_error_detail: {str(e)}")
        return len(self.error_detail_set.get_set_members()) == 0

    def handle_error_page(self):
        error_page_set = list(self.error_year_type_set.get_set_members())
        if not error_page_set:
            self.log_print.print("非常完美，handle_error_page 无 page 需要处理")
            return True
        for error_key in error_page_set:
            page_info = self._decode_cache(error_key)
            response = self.get_one_year_type(
                year=page_info.get("year"),
                marketKind=page_info.get("marketKind")
            )
            if response:
                data_list = self.extract_one_year_type(response)
                insert_list = self.handle_detail_batch(detail_dict_list=data_list)
                self.save_result(insert_list=insert_list)
                self.error_year_type_set.remove_from_set(error_key)
            else:
                self.log_print.print(
                    f"handle_error_page year:{page_info.get('year')} "
                    f"marketKind:{page_info.get('marketKind')} 采集失败"
                )
                return False
        return len(self.error_year_type_set.get_set_members()) == 0

    def handle_error(self):
        for retry in range(0, 5):
            self.log_print.warning("主流程采集完成，开始处理错误的 page")
            finished_page = self.handle_error_page()
            if finished_page:
                break
        for retry in range(0, 5):
            self.log_print.warning("开始处理错误的 detail")
            finished_detail = self.handle_error_detail()
            if finished_detail:
                break

    def run_list(self):
        finished_list = self.completed_segment_set.get_set_members()
        for year in range(self.start_year, self.end_year + 1):
            for marketKind in self.marketKinds:
                segment = f"{year}-{marketKind}"
                if segment in finished_list:
                    self.log_print.print(f"{segment} finished 跳过")
                else:
                    self.log_print.print(f"{segment} 开始采集")
                    insert_num = self.handle_one_year_type(year=year, marketKind=marketKind)
                    self.complete_detail_type.clear_value()
                    if insert_num or insert_num == 0:
                        self.completed_segment_set.add_to_set(segment)
                        self.log_print.print(f"{segment} finished, insert_num: {insert_num}")
                    else:
                        self.log_print.print(f"error in handle_one_year_type, {segment} finished, insert_num: {insert_num}")
                        page_info = {"year": year, "marketKind": marketKind}
                        self.log_print.print(
                            f"year:{year} marketKind:{marketKind} list 采集失败"
                        )
                        self.error_year_type_set.add_to_set(self._encode_cache(page_info))
        self.handle_error()


if "__main__" == __name__:
    test_url = "https://mops.twse.com.tw/mops/#/web/t146sb10"
    spider = Spider(pro_path=Path(__file__).parent, test_url=test_url)
    spider.run_list()
