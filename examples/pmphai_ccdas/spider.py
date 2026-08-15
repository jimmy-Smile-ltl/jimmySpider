"""
pmphai_ccdas/spider.py
──────────────────────
示例：人卫智网 CCDAS 临床指南数据库抓取 (https://ccdas.pmphai.com)
—— 医学会议临床指南（Guidelines），按「分类树 → 叶子科室 → 分页」三级采集。

演示内容：
  1. 分类树构建：POST /tagc/facetguide 返回扁平科室列表，build_tree() 按 parent
     字段构建多级树，extract_leaves() 递归收集叶子节点
  2. 叶子节点元数据透传：叶子节点在直接父级为 level 2 时携带
     father_id / father_name，写入每条记录实现科室分类归属
  3. 双接口流水线：分类接口 + 列表接口（/appguide/list，pageNo/pageSize 分页）
  4. 断点续爬：log_page 记录 {"leaf_idx", "page"}，按科室 + 页码精确恢复
  5. 错误重试：error_page_set 记录失败页，主流程后最多 3 轮重试

数据字段：名称、概述、浏览量、detail_url + 科室分类元数据。
"""

import json
import datetime
from pathlib import Path
from typing import Dict, List, Optional

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        # 自动定位项目目录（等价于入口处传 pro_path=Path(__file__).parent）
        kwargs.setdefault("pro_path", Path(__file__).parent)
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://ccdas.pmphai.com"
        self.category_api_url = "https://ccdas.pmphai.com/tagc/facetguide"
        self.list_api_url = "https://ccdas.pmphai.com/appguide/list"

        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://ccdas.pmphai.com",
            "Pragma": "no-cache",
            "Referer": "https://ccdas.pmphai.com/Pc/Guidelines/index",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
        # 会话 Cookie 已清空（原实现含 JSESSIONID 等会话与统计 Cookie）。
        # 当前接口匿名可访问；若站点调整风控，请用浏览器登录
        # https://ccdas.pmphai.com 后复制 Cookie 填入 self.cookies。
        self.cookies = {}

        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Step 1: Fetch & parse category tree                                 #
    # ------------------------------------------------------------------ #

    def get_category_data(self) -> Optional[Dict]:
        """POST /tagc/facetguide — returns full keshi tree + zhinan list."""
        data = {
            "searchText": "",
            "knowledgeLibPrefix": "guide",
        }
        response = self.single_fetcher.fetch(
            self.category_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=data,
            method="POST",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                self.log_print.error(f"Category JSON decode error: {e}")
        return None

    @staticmethod
    def build_tree(keshi_list: List[Dict]) -> Optional[Dict]:
        """Build a parent-child tree from the flat keshi list."""
        node_map = {}
        for item in keshi_list:
            node_map[item["id"]] = {
                "id": item["id"],
                "name": item["name"],
                "level": item["level"],
                "children": [],
            }
        root = None
        for item in keshi_list:
            node = node_map[item["id"]]
            parent_id = item.get("parent")
            if parent_id and parent_id in node_map:
                node_map[parent_id]["children"].append(node)
            elif not parent_id:
                root = node
        if root is None:
            for node in node_map.values():
                if node["level"] == 1:
                    root = node
                    break
        return root

    @staticmethod
    def extract_leaves(node: Dict, parent: Optional[Dict] = None) -> List[Dict]:
        """
        Recursively collect leaf nodes.
        Leaf carries father_id/father_name only when the direct parent is level 2.
        """
        leaves = []
        children = node.get("children", [])
        if not children:
            if parent and parent.get("level") == 2:
                father_id = parent["id"]
                father_name = parent["name"]
            else:
                father_id = None
                father_name = None
            leaves.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "level": node["level"],
                    "father_id": father_id,
                    "father_name": father_name,
                }
            )
        else:
            for child in children:
                leaves.extend(Spider.extract_leaves(child, node))
        return leaves

    def get_leaf_list(self) -> List[Dict]:
        """Full pipeline: fetch categories → build tree → extract leaves."""
        res_json = self.get_category_data()
        if not res_json:
            self.log_print.error("get_leaf_list: 无法获取分类数据")
            return []
        data = res_json.get("data", {})
        keshi = data.get("keshi", [])
        if not keshi:
            self.log_print.error("get_leaf_list: keshi 列表为空")
            return []
        tree_root = self.build_tree(keshi)
        if not tree_root:
            self.log_print.error("get_leaf_list: 构建树失败")
            return []
        leaf_list = self.extract_leaves(tree_root)
        self.log_print.print(f"get_leaf_list: 共 {len(leaf_list)} 个叶子科室")
        return leaf_list

    # ------------------------------------------------------------------ #
    #  Step 2: Fetch list page                                            #
    # ------------------------------------------------------------------ #

    def get_list_page(self, page: int, keshi_id: str) -> Optional[Dict]:
        """POST /appguide/list — returns one page of guidelines for a keshi leaf."""
        data = {
            "pageNo": str(page),
            "pageSize": "10",
            "userId": "userId",
            "searchText": "",
            "$search_zhi_nan_fen_lei": "",
            "$search_ke_shi_fen_lei": keshi_id,
            "knowledgeLibPrefix": "guide",
        }
        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=data,
            method="POST",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                self.log_print.error(f"List JSON decode error: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Step 3: Parse & enrich records                                     #
    # ------------------------------------------------------------------ #

    def parse_page_data(self, datas: List[Dict], leaf: Dict) -> List[Dict]:
        """
        Transform raw API datas into structured records.
        Each item gets Chinese field names + category metadata + create_time.
        """
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = []
        for item in datas:
            raw_id = item.get("id", "")
            detail_url = (
                f"{self.base_url}/appguide/toPcDetail"
                f"?sessionId=&knowledgeLibPrefix=guide&id={raw_id}"
            )
            record = {
                "_id": generate_string_id(detail_url),
                "名称": item.get("ming_cheng", ""),
                "概述": item.get("gai_shu", ""),
                "浏览量": item.get("view_count", 0),
                "detail_url": detail_url,
                # category metadata from the leaf node
                "科室id": leaf.get("id"),
                "科室": leaf.get("name"),
                "科室level": leaf.get("level"),
                "父科室id": leaf.get("father_id"),
                "父科室": leaf.get("father_name"),
                "create_time": now_ts,
            }
            results.append(record)
        return results

    # ------------------------------------------------------------------ #
    #  Step 4: Retry error pages                                          #
    # ------------------------------------------------------------------ #

    def handle_error_page(self, leaf_list: List[Dict]) -> bool:
        """Re-fetch any pages that previously failed."""
        leaf_map = {leaf["id"]: leaf for leaf in leaf_list}
        error_keys = list(self.error_page_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_keys:
            page_info = self._decode_cache(error_key)
            page = page_info.get("page")
            keshi_id = page_info.get("keshi_id")
            leaf = leaf_map.get(keshi_id, {})

            res_json = self.get_list_page(page, keshi_id)
            if res_json and res_json.get("code") == "000000":
                datas = res_json.get("result", {}).get("datas", [])
                data_list = self.parse_page_data(datas, leaf)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(
                    f"handle_error_page page:{page} keshi:{keshi_id} 采集失败"
                )
                return False

        return len(self.error_page_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Main entry                                                         #
    # ------------------------------------------------------------------ #

    def run_all(self):
        # ---------- load / resume progress ----------
        progress_str = self.log_page.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_leaf_idx = progress.get("leaf_idx", 0)
            start_page = progress.get("page", 1)
        else:
            start_leaf_idx = 0
            start_page = 1

        # ---------- build leaf list ----------
        leaf_list = self.get_leaf_list()
        if not leaf_list:
            self.log_print.error("run_all: 叶子科室列表为空，终止")
            return

        self.log_print.print(
            f"开始抓取 ccdas 指南列表, "
            f"恢复自 leaf_idx:{start_leaf_idx}, page:{start_page}..."
        )

        # ---------- iterate leaves ----------
        for leaf_idx in range(start_leaf_idx, len(leaf_list)):
            leaf = leaf_list[leaf_idx]
            keshi_id = leaf["id"]
            keshi_name = leaf["name"]
            father_name = leaf.get("father_name") or ""
            display_name = f"{father_name}-{keshi_name}" if father_name else keshi_name
            page = start_page if leaf_idx == start_leaf_idx else 1
            self.log_print.print(f"开始抓取科室: {display_name} (id={keshi_id})")

            while True:
                res_json = self.get_list_page(page, keshi_id)
                if res_json and res_json.get("code") == "000000":
                    result = res_json.get("result", {})
                    datas = result.get("datas", [])
                    total_page = result.get("totalPage", 1)

                    data_list = self.parse_page_data(datas, leaf)
                    if data_list:
                        self.save_result(insert_list=data_list)
                        self.log_print.print(
                            f"  [{display_name}] page:{page}/{total_page} "
                            f"采集成功 {len(data_list)} 条"
                        )
                    else:
                        self.log_print.warning(
                            f"  [{display_name}] page:{page}/{total_page} 解析无数据"
                        )

                    if page >= total_page:
                        self.log_print.print(f"  [{display_name}] 已采集完毕")
                        break
                    page += 1

                else:
                    page_info = {"page": page, "keshi_id": keshi_id}
                    self.log_print.print(
                        f"  [{display_name}] page:{page} 列表请求失败，记录错误页"
                    )
                    self.error_page_set.add_to_set(self._encode_cache(page_info))
                    page += 1
                    # stop this leaf to avoid infinite loop on repeated failure
                    break

                # save progress after every page
                self.log_page.record_string(
                    json.dumps({"leaf_idx": leaf_idx, "page": page})
                )

        self.log_print.print("主流程采集完成")
        self.log_page.clear_value()

        # ---------- retry error pages ----------
        for retry in range(3):
            self.log_print.warning(f"开始处理错误 page (第 {retry + 1} 次)")
            if self.handle_error_page(leaf_list):
                break


if "__main__" == __name__:
    spider = Spider()
    spider.run_all()
