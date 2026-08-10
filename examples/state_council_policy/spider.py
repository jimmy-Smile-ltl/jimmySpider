"""
示例：国务院政策文件库抓取 (state_council_policy)

演示内容：
- 列表页 + 详情页分页抓取模式（列表接口提取 URL，详情页解析正文入库）
- 详情页多线程并发抓取（ThreadPoolExecutor + as_completed）
- BeautifulSoup 详情页字段解析（表格键值对 + 正文内容提取）
- Redis 断点续爬 + 失败列表页/详情页的统一重试
- 请求处理器由基类自动装配（self.single_fetcher 默认 SingleRequestHandler）
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import safe_extract_json, generate_string_id

# 国务院政策文件库
class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        super(Spider, self).__init__(*args, **kwargs)
        self.log_page_num = Cache(f"{self.table_name}_page_num")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self.error_detail_set = Cache(f"{self.table_name}_error_detail_set")
        self.headers =  {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?q=&t=zhengcelibrary&orpro=",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\""
        }


    def get_one_page(self,page_num):

        cookies = {
            # "arialoadData": "false",
            # "wdcid": "16c014ffb578366f",
            # "wdlast": "1776934835",
            # "wdses": "0b9781855e18e678",
            # "ariauseGraymode": "false"
        }
        params = {
            "t": "zhengcelibrary",
            "q": "",
            "timetype": "",
            "mintime": "",
            "maxtime": "",
            "sort": "score",
            "sortType": "1",
            "searchfield": "",
            "puborg": "",
            "pcodeYear": "",
            "pcodeNum": "",
            "filetype": "",
            "p": f"{page_num}",
            "n": "5",
            "inpro": "",
            "dup": "",
            "orpro": "",
            "type": "gwyzcwjk"
        }
        url = "https://sousuo.www.gov.cn/search-gov/data"
        response = self.single_fetcher.fetch(url, headers=self.headers, cookies=cookies, params=params,verify=False)
        return response


    def extract_one_page(self,response):
        data_list = []
        max_page = None
        has_next = True
        if not response:
            return data_list , max_page , has_next
        try:
            search_json = response.json()
            msg = safe_extract_json(data=search_json, path=["msg"],default=None)
            code = safe_extract_json(data=search_json, path=["code"],default=500)
            if not msg or code != 200:
                self.log_print.error(f"error in  extract_detail_urls:{ search_json}")
                return data_list, max_page, has_next
            page_list = []
            cat_list_len = []
            results = safe_extract_json(data=search_json, path=["searchVO", "catMap"])
            for item_key in results.keys():
                item_value = results.get(item_key, '')
                listVO = item_value.get('listVO', [])
                cat_list_len.append(len(listVO) < 5)
                page_list.append(int(item_value.get("totalCount") /5 ) +1 )
                for one_row in listVO:
                    one_row["type"] = item_key
                    data_list.append(one_row)
            max_page = max(page_list)
            has_next = not all(cat_list_len) # 全都小于5 说明没有下一页了
            return data_list , max_page ,has_next
        except Exception as e:
            self.log_print.error(f"error in  extract_one_page:{ str(e)}")
            return data_list, max_page, has_next

    def  clean_text(self , text):
        # 替换空格
        text = re.sub(r'\s+', '', text)
        # 删去除特殊字符
        text = re.sub(r'[^\w]', '', text)
        return text.strip()

    def extract_one_detail(self, detail_response):
        one_row = {}
        type_should = ""
        if not detail_response:
            return one_row
        detail_soup = BeautifulSoup(detail_response.text, "html.parser")
        head_table = detail_soup.select_one("table.bd1") if detail_soup else None
        if not head_table:
            head_table = detail_soup.select_one("div.policyLibraryOverview_header table") or detail_soup.select_one("div.pctoubukuang1 table")
        if not head_table:
            # type 解讀沒有
            type_should  = "解读"
        else:
            for tr in head_table.select("tr"):
                tds = tr.select("td")
                if len(tds) % 2 != 0:  # 如果td的数量不是偶数，说明有缺失
                    print(tds)
                else:
                    for idx in range(0, len(tds), 2):
                        key = self.clean_text(tds[idx].get_text(strip=True))
                        value = tds[idx + 1].get_text(strip=True)
                        # print(f"{key}: {value}")
                        one_row[key] = value
        candidates = [
            "div.article",
            "table.marauto.table2",
            "table.pages_content",
            "div.pages_content",
        ]
        content_table = None
        for selector in candidates:
            content_table = detail_soup.select_one(selector)
            if content_table:
                break

        content = self.extract_soup.extract_content_recursively(soup=content_table)
        one_row["content"] = content
        type_text = self.get_type(detail_soup)
        if not type_text:
            self.log_print.warning(f"error in  extract_one_detail get_type :{type_text} ")
        one_row["type"] = type_text
        # if type_should == "解读" and type_text !="解读":
        # https://www.gov.cn/gongbao/content/2020/content_5492508.htm
        #     self.log_print.error(f"error in extract_one_detail:type_text :{ type_text }")
        #     return False
        return one_row

    def get_type(self,detail_soup):
        type_text = ""
        # BreadcrumbNav
        BreadcrumbNav_tag = detail_soup.select_one("div.BreadcrumbNav")
        # delete pages_print mhide
        if BreadcrumbNav_tag and BreadcrumbNav_tag.select_one("div.pages_print.mhide"):
            BreadcrumbNav_tag.select_one("div.pages_print.mhide").decompose()
        BreadcrumbNav_text = BreadcrumbNav_tag.get_text(strip=True, separator="")
        type_text_list = BreadcrumbNav_text.split(">")
        if type_text_list:
            pattern = re.compile(r"\d")
            type_text_list.reverse()
            # 倒着來 不含數字
            for item_text in type_text_list:
                match = pattern.search(item_text)
                if not match:
                    type_text = self.clean_text(item_text)
                    return type_text
            else:
                self.log_print.warning(f"error in extract_one_detail:type_text :{ type_text }")
        return type_text

    def handle_one_detail(self, detail_dict):
        detail_url = detail_dict.get("url")
        response = self.single_fetcher.fetch(detail_url, headers=self.headers, verify=False)
        if response:
            file_id = generate_string_id(detail_url)
            detail_dict["_id"] = file_id
            html_path = self.html_saver.save_html(html=response.text, file_id=file_id)
            one_row = self.extract_one_detail(response)
            if not one_row:
                self.log_print.error(f"error in handle_one_detail extract_one_detail failed for url {detail_url}")
                self.error_detail_set.add_to_set(detail_dict)
                return False
            detail_dict["html_path"] = html_path
            detail_dict.update(one_row)
            return detail_dict
        else:
            self.log_print.print(f"detail_url:{detail_url} 详情页采集失败 问题已记录")
            self.error_detail_set.add_to_set(detail_dict)
            return False

    def handle_detail_batch(self, detail_dict_list, handle_error = False):
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_detail = {executor.submit(self.handle_one_detail, detail_dict): detail_dict for detail_dict in detail_dict_list}
            for future in as_completed(future_to_detail):
                detail_dict = future_to_detail[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        if handle_error:
                            self.error_detail_set.remove_from_set(detail_dict)
                except Exception as e:
                    self.log_print.error(f"error in handle_detail_batch for url {detail_dict.get('url')}: {str(e)}")
                    self.error_detail_set.add_to_set(detail_dict)
        return results

    def handle_error_detail(self):
        error_detail_set = self.error_detail_set.get_set_members()
        if not error_detail_set:
            self.log_print.print("handle_error_detail: no error details to retry")
            return True
        batch_size = 20
        total = len(error_detail_set)
        error_detail_list = list(error_detail_set)
        for start in range(0, total, batch_size):
            batch = error_detail_list[start:start + batch_size]
            self.log_print.print(
                f"handle_error_detail retry batch {start // batch_size + 1} "
                f"size={len(batch)} total={total}"
            )
            insert_list = self.handle_detail_batch(detail_dict_list=batch, handle_error=True)
            self.save_result(insert_list=insert_list)
        else:
            return len(self.error_detail_set.get_set_members()) == 0


    def handle_error_page(self):
        error_page_set = self.error_page_set.get_set_members()
        if error_page_set:
            for current_page in error_page_set:
                response = self.get_one_page(current_page)
                if response:
                    self.log_print.print(f" handle_error_page page_num:{current_page} list 采集成功 按公布日期降序")
                    data_list, max_page, has_next = self.extract_one_page(response)
                    self.save_result(insert_list=data_list)
                    self.error_page_set.remove_from_set(current_page)
                else:
                    self.log_print.print(f"handle_error_page  page_num:{current_page} list 采集失败 按公布日期降序 问题已记录")
            else:
                return len(self.error_detail_set.get_set_members()) == 0
        else:
            self.log_print.print(f"非常完美 ，handle_error_page  无 page_num 需要处理")
            return True

    def run_list(self):
        current_page  = self.log_page_num.get_int(default=1)
        max_page  =  current_page + 1 # 这个不能写死
        has_next = True
        while current_page <= max_page:
            response = self.get_one_page(current_page)
            if response:
                self.log_print.print(f" page_num:{current_page} list 采集成功 按公布日期降序")
                data_list ,max_page_temp, has_next = self.extract_one_page(response)
                if max_page_temp:
                    max_page = max_page_temp
                if not data_list:
                    self.log_print.print(f"data_list :{data_list} 当前 page {current_page}")
                    self.error_page_set.add_to_set(current_page)
                insert_list = self.handle_detail_batch(detail_dict_list=data_list)
                self.save_result(insert_list =  insert_list)
                self.log_print.print(f" page_num:{current_page} max_page: {max_page} list 采集成功 按公布日期降序 详情页采集成功 {len(insert_list)} 条")
            else:
                self.log_print.print(f" page_num:{current_page} list 采集失败 按公布日期降序 问题已记录")
                self.error_page_set.add_to_set(current_page)
            current_page += 1
            self.log_page_num.record_int(current_page)
            if not has_next:
                self.log_print.warning(f"run_list has_next: {has_next} 当前 page_num :{current_page} 已经大于 max_page :{max_page}")
                break
        # while True:
        for retry in range(0, 3):
            self.log_print.warning("主流程采集完成，开始处理错误的 page_num 和 detail_url")
            finished_page = self.handle_error_page()
            finished_detail = self.handle_error_detail()
            if finished_page and finished_detail:
                break
            else:
                self.log_print.print(f"finished_page :{finished_page} 继续处理错误的 page_num 或者 detail_url")
        else:
            self.log_print.warning(f"经过多次重试，依然有错误 手动排查一下问题 大概率是网站问题")


if "__main__" == __name__:
    test_url = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?q=&t=zhengcelibrary&orpro="
    spider = Spider(pro_path = Path(__file__).parent ,test_url=  test_url)
    spider.run_list()
