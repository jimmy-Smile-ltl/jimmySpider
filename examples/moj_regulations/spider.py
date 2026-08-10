"""
示例：司法部规章库抓取 (moj_regulations)

演示内容：
- 显式装配 SingleRequestHandler，向搜索接口发送 JSON POST 请求并分页
- rename_keys_inplace 将接口加密字段名映射为可读中文名
- safe_extract_json 安全提取嵌套 JSON 字段（带默认值，避免 KeyError）
- Redis 断点续爬 + 失败页自动重试
"""

import json
from pathlib import Path

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import safe_extract_json, rename_keys_inplace, generate_string_id

rename_keys = rename_mapping = {
    # 法规发布/修订信息（主要字段）
    "f_202321136868": "发布修订信息",
    # 备用字段，内容与上类似（两者通常只有一个非空）
    "f_202344311304": "发布修订信息_备用",

    # 来源相关
    "doc_pub_url": "发布网址",
    "f_20232124962": "原文链接",            # 可能是字符串或列表
    "f_202323394765": "来源网站/单位",
    "f_202355832506": "发布机关_备用",       # 如“国家发展改革委 国家能源局”
    "f_20232151076": "发布机关",            # 如“汕头市人民政府”
    "f_202328191239": "发布部门",            # 如“国家发展和改革委员会”
    "f_2023425676953": "发布部门_备用",

    # 法规属性
    "f_202321807875": "法规类别",            # 如“地方政府规章”“部门规章”
    "f_202321360426": "法规名称",
    "f_202321758948": "法规正文",
    "f_202321864401": "附件数量",            # 整数值 1,2,3
    "f_20232380533": "适用城市",             # 部分有值（汕头、云浮、兰州、中山）

    # 日期
    "f_202321915922": "发布日期",

    # 地域
    "f_202321423473": "所属省份",
    "f_2023425808265": "所属省份_备用",

    # 其他标识
    "f_202321159816": "特殊标记",             # 出现过 ["是"]，可能表示“有解读”或“已修订”
    "f_202321124775": "数据唯一标识",         # 数值ID
}


class Spider(JimmySpider):
    def __init__(self, list_api_url=None, *args, **kwargs):
        super(Spider, self).__init__(*args, **kwargs)
        self.log_page_num = Cache(f"{self.table_name}_page_num")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self.test_url = "https://www.moj.gov.cn/pub/sfbgw/gwygzk/index.html"
        self.list_api_url =  "https://sousuoht.www.gov.cn/athena/forward/BD8730CDDA12515E2D9E1B21AA11C0D6"
        self.page_size = 7
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.gov.cn",
            "Pragma": "no-cache",
            "Referer": "https://www.gov.cn/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            # athenaAppKey 从浏览器请求中获取（Chrome DevTools → Network → 找到 API 请求 → 复制该值）
            "athenaAppKey": "YOUR_ATHENA_APP_KEY_HERE",
            "athenaAppName": "%E8%A7%84%E7%AB%A0%E5%BA%93",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
        }

    def build_list_payload(self, page_num):
        return {
            "code": "18258ab0ac9",
            "preference": None,
            "searchFields": [
                {"fieldName": "f_202321360426", "searchWord": "", "withHighLight": True},
                {"fieldName": "f_202321758948", "searchWord": "", "withHighLight": True},
            ],
            "sorts": [
                {},
                {"sortField": "f_202321915922", "sortOrder": "DESC"},
            ],
            "resultFields": [
                "f_202355832506",
                "f_20232124962",
                "f_202321124775",
                "f_202321159816",
                "f_202321360426",
                "f_202321423473",
                "f_202321758948",
                "f_202321807875",
                "f_202321864401",
                "f_202321915922",
                "f_202323394765",
                "f_202328191239",
                "f_202344311304",
                "f_202355832506",
                "f_2023425676953",
                "f_2023425808265",
                "f_202321136868",
                "f_20232380533",
                "f_20232151076",
                "doc_pub_url",
            ],
            "trackTotalHits": "true",
            "granularity": "ALL",
            "orderByFields": [],
            "tableName": "t_1860c735d31",
            "pageSize": self.page_size,
            "pageNo": page_num,
        }

    def get_one_page(self, page_num):
        if not self.list_api_url:
            self.log_print.error("list_api_url is not set; set MOJ_LIST_API_URL or pass list_api_url")
            return None
        payload = self.build_list_payload(page_num)
        data = json.dumps(payload, separators=(",", ":"))
        return self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            method="POST",
            data=data,
        )

    def extract_one_page(self, response):
        data_list = []
        max_page = None
        has_next = True
        if not response:
            return data_list, max_page, has_next
        try:
            res_json = response.json()
            code = safe_extract_json(res_json, path=["resultCode", "code"], default=None)
            cn_msg = safe_extract_json(res_json, path=["resultCode", "cnMsg"], default=None)
            if not code or not cn_msg:
                self.log_print.error(f"extract_one_page invalid response: {res_json}")
                return data_list, max_page, has_next
            page_info = safe_extract_json(res_json, path=["result", "data", "pager"], default={})
            data_list = safe_extract_json(res_json, path=["result", "data", "list"], default=[])
            max_page = safe_extract_json(page_info, path=["pageCount"], default=None)
            current_page = safe_extract_json(page_info, path=["pageNo"], default=None)
            if max_page and current_page:
                has_next = current_page < max_page
            else:
                has_next = bool(data_list)
            return data_list, max_page, has_next
        except Exception as e:
            self.log_print.error(f"error in extract_one_page: {str(e)}")
            return data_list, max_page, has_next

    def clean_data(self, data):
        data_list = []
        for item in data or []:
            doc_pub_url = item.get("doc_pub_url")
            if isinstance(doc_pub_url, list):
                doc_pub_url = doc_pub_url[0] if doc_pub_url else None
            item_new = rename_keys_inplace(original_dict=item, key_mapping=rename_keys)
            if doc_pub_url:
                item_new["_id"] = generate_string_id(doc_pub_url)
            else:
                self.log_print.warning(f"missing doc_pub_url, cannot create _id: {item_new}")
            data_list.append(item_new)
        return data_list

    def handle_error_page(self):
        error_page_set = self.error_page_set.get_set_members()
        if error_page_set:
            for current_page in error_page_set:
                response = self.get_one_page(current_page)
                if response:
                    data_list, max_page, has_next = self.extract_one_page(response)
                    cleaned_list = self.clean_data(data_list)
                    self.save_result(insert_list=cleaned_list)
                    self.error_page_set.remove_from_set(current_page)
                else:
                    self.log_print.print(
                        f"handle_error_page page_num:{current_page} list 采集失败 问题已记录"
                    )
                    return False
            return True
        self.log_print.print("非常完美，handle_error_page 无 page_num 需要处理")
        return True

    def run_list(self):
        current_page = self.log_page_num.get_int(default=1)
        max_page = current_page + 1
        has_next = True
        while current_page <= max_page:
            response = self.get_one_page(current_page)
            if response:
                data_list, max_page_temp, has_next = self.extract_one_page(response)
                if max_page_temp:
                    max_page = max_page_temp
                if not data_list:
                    self.log_print.print(f"data_list empty, page {current_page}")
                    self.error_page_set.add_to_set(current_page)
                    self.log_print.warning(f"page_num:{current_page} 已加入 error_page_set")
                cleaned_list = self.clean_data(data_list)
                self.save_result(insert_list=cleaned_list)
                self.log_print.print(
                    f"page_num:{current_page} max_page:{max_page} list 采集成功 {len(cleaned_list)} 条"
                )
            else:
                self.log_print.print(f"page_num:{current_page} list 采集失败 问题已记录")
                self.error_page_set.add_to_set(current_page)
                self.log_print.warning(f"page_num:{current_page} 已加入 error_page_set")
            current_page += 1
            self.log_page_num.record_int(current_page)
            if not has_next:
                self.log_print.warning(
                    f"run_list has_next:{has_next} 当前 page_num:{current_page} 已经大于 max_page:{max_page}"
                )
                break
        while True:
            self.log_print.warning("主流程采集完成，开始处理错误的 page_num")
            finished_page = self.handle_error_page()
            if finished_page:
                break

if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_list()
