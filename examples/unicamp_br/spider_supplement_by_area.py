"""
unicamp_br/spider_supplement_by_area.py
───────────────────────────────────────
示例：UNICAMP 学术库 (https://repositorio.unicamp.br) 数量核对 + 补采。

演示内容：
  1. 统计阶段 —— 多线程并发查询 MongoDB，将每个 area 的实有数量与
     hierarchy JSON 中的 QuantidadeRegistros 对比，输出缺口报告
  2. 补采阶段 —— 对数量不足的 area 重新全量爬取所有分页并写库，
     补采完成后二次核对
  3. 与 spider_list_by_area.py / spider_list_by_year.py 对比，
     展示同一站点的第三种策略：数据校验驱动的最小化补采

────────────────────────────
功能：
  1. 统计阶段 —— 遍历 hierarchy JSON，对每个 area 查询数据库已有数量，
               与 QuantidadeRegistros 对比，打印缺口报告。
  2. 补采阶段 —— 对数量不足的 area 重新全量爬取所有分页并写库。

用法：
  python spider_supplement_by_area.py          # 统计 + 补采
  python spider_supplement_by_area.py --check  # 只统计，不补采

注意：
  - unicamp_academic_hierarchy.json 为运行时依赖的学术层级数据文件，
    需与本文件同目录。
"""

import os
import re
import json
import time
import datetime
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id, generate_doi_id, normalize_doi


class SupplementSpider(JimmySpider):
    """在原 Spider 基础上增加「按 area 对比 + 补采」能力。"""

    def __init__(self, *args, **kwargs):
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = Path(__file__).parent
        super().__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url      = "https://repositorio.unicamp.br"
        self.advance_url   = "https://repositorio.unicamp.br/Busca/Avancada"
        self.area_url_tpl  = (
            "https://repositorio.unicamp.br/Busca/AreaConcentracao"
            "?codigo={area}&programa={programa}"
        )
        self.validate_url  = "https://repositorio.unicamp.br/acervo/validaacessodetalhe"
        self.list_api_url  = "https://repositorio.unicamp.br/Resultado/CarregarPaginaLayoutDetalhe"

        self.headers_page = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
        }

        self.headers_list = {
            "accept": "*/*",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "content-length": "0",
            "origin": self.base_url,
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "x-requested-with": "XMLHttpRequest",
        }

        self.hierarchy_path = os.path.join(Path(__file__).parent, "unicamp_academic_hierarchy.json")

        # 补采专用的错误集合（与主流程隔离，避免互相干扰）
        self.supp_error_set = Cache(f"{self.table_name}_supp_error_set")

    # ------------------------------------------------------------------ #
    #  Hierarchy                                                           #
    # ------------------------------------------------------------------ #

    def load_hierarchy(self) -> List[Dict]:
        if not os.path.exists(self.hierarchy_path):
            self.log_print.error(f"hierarchy not found: {self.hierarchy_path}")
            return []
        with open(self.hierarchy_path, "r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except Exception as exc:
                self.log_print.error(f"hierarchy decode error: {exc}")
                return []

    # ------------------------------------------------------------------ #
    #  DB count helper                                                     #
    # ------------------------------------------------------------------ #

    def count_db_by_area(self, area_code: str, prog_code: str) -> int:
        """
        查询数据库中该 area + program 已有多少条记录。
        JimmySpider 基类通常提供 self.col（MongoDB collection）或
        self.db_query 等方法；这里使用通用写法，请根据实际基类调整。
        """
        try:
            # ── MongoDB 写法（最常见） ──────────────────────────────
            if hasattr(self, "db_manager"):
                return self.db_manager.collection.count_documents({
                    "领域代码": area_code,
                    "项目代码": prog_code,
                })
            # ── 若基类提供 count_by_filter ────────────────────────
            if hasattr(self, "count_by_filter"):
                return self.db_manager.count_by_filter({
                    "领域代码": area_code,
                    "项目代码": prog_code,
                })
            # ── 兜底：返回 -1 表示无法查询 ────────────────────────
            return -1
        except Exception as exc:
            self.log_print.error(f"count_db_by_area error: {exc}")
            return -1

    # ------------------------------------------------------------------ #
    #  Step 1 — 统计缺口                                                   #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  统计缺口（多线程并发查库）                                          #
    # ------------------------------------------------------------------ #

    # 每批并发查询的线程数；DB 连接池够用时可调大
    CHECK_WORKERS: int = 20

    def _check_one_area(
        self,
        prog_idx: int,
        area_idx: int,
        program: Dict,
        area: Dict,
    ) -> Optional[Dict]:
        """
        单线程工作单元：查询一个 area 的 DB 数量并返回结果字典。
        返回 None 表示数据正常（无缺口）；返回 dict 表示有缺口或查询失败。
        """
        prog_code = program.get("CodigoPrograma", "")
        prog_name = program.get("NomePrograma", "")
        area_code = area.get("CodigoArea", "")
        area_name = area.get("NomeArea", "")
        area_url  = area.get("UrlArea", "")
        expected  = int(area.get("QuantidadeRegistros") or 0)

        actual = self.count_db_by_area(area_code, prog_code)

        if actual < 0:
            self.log_print.warning(
                f"  无法查询 DB  prog:{prog_code} area:{area_code} ({area_name})"
            )
            return None  # 无法统计，跳过

        if actual < expected:
            missing = expected - actual
            self.log_print.warning(
                f"  ⚠ 缺少 {missing:>5} 条  "
                f"prog:{prog_code}  area:{area_code}  [{area_name}]  "
                f"(期望:{expected}  实有:{actual})"
            )
            return {
                "prog_idx":  prog_idx,
                "area_idx":  area_idx,
                "prog_code": prog_code,
                "prog_name": prog_name,
                "area_code": area_code,
                "area_name": area_name,
                "area_url":  area_url,
                "expected":  expected,
                "actual":    actual,
                "missing":   missing,
            }
        else:
            self.log_print.print(
                f"  ✓ OK  prog:{prog_code}  area:{area_code}  [{area_name}]"
                f"  (期望:{expected}  实有:{actual})"
            )
            return None

    def check_missing(
        self,
        program_list: List[Dict],
        workers: int = CHECK_WORKERS,
    ) -> List[Dict]:
        """
        多线程并发版：遍历所有 program → area，对比 QuantidadeRegistros
        与数据库实际数量。返回缺口列表，保持原始 (prog_idx, area_idx) 顺序。

        每项格式：
        {
            "prog_idx":   int,
            "area_idx":   int,
            "prog_code":  str,
            "prog_name":  str,
            "area_code":  str,
            "area_name":  str,
            "area_url":   str,
            "expected":   int,   # JSON 中声明的数量
            "actual":     int,   # 数据库实际数量
            "missing":    int,   # 差值
        }
        """
        self.log_print.print("=" * 60)
        self.log_print.print(
            f"【统计阶段】开始对比 JSON 与数据库数量（并发线程数: {workers}）"
        )
        self.log_print.print("=" * 60)

        # 把所有 area 平铺成任务列表，保留原始索引以便结果排序
        tasks: List[Tuple[int, int, Dict, Dict]] = []
        total_expected = 0
        for prog_idx, program in enumerate(program_list):
            area_list = program.get("AreasConcentracao", [])
            for area_idx, area in enumerate(area_list):
                total_expected += int(area.get("QuantidadeRegistros") or 0)
                tasks.append((prog_idx, area_idx, program, area))

        self.log_print.print(
            f"  共 {len(tasks)} 个 area 待统计，总期望条数: {total_expected}"
        )

        # 用于收集 (orig_task_index, result_dict | None) 的原子计数器
        _lock            = threading.Lock()
        completed_count  = 0
        raw_results: Dict[int, Optional[Dict]] = {}  # task_index → result

        def _worker(task_idx: int, args: Tuple) -> None:
            nonlocal completed_count
            result = self._check_one_area(*args)
            with _lock:
                raw_results[task_idx] = result
                completed_count += 1
                if completed_count % 50 == 0 or completed_count == len(tasks):
                    self.log_print.print(
                        f"  进度: {completed_count}/{len(tasks)}"
                    )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_worker, idx, task): idx
                for idx, task in enumerate(tasks)
            }
            for future in as_completed(futures):
                exc = future.exception()
                if exc:
                    self.log_print.warning(
                        f"  线程异常 task_idx={futures[future]}: {exc}"
                    )

        # 按原始顺序整理缺口列表（保持 prog_idx / area_idx 升序）
        missing_list: List[Dict] = [
            raw_results[i]
            for i in range(len(tasks))
            if raw_results.get(i) is not None
        ]

        # 汇总实有数量
        total_actual = sum(item["actual"] for item in missing_list)
        # 加上 OK 的 area（actual == expected，从 tasks 反推）
        for i, (prog_idx, area_idx, program, area) in enumerate(tasks):
            if raw_results.get(i) is None:
                # OK 的 area，actual ≈ expected
                total_actual += int(area.get("QuantidadeRegistros") or 0)

        self.log_print.print("=" * 60)
        self.log_print.print(
            f"【统计汇总】"
            f"总期望:{total_expected}  "
            f"缺口 area 数:{len(missing_list)}  "
            f"缺少总条数:{sum(i['missing'] for i in missing_list)}"
        )
        self.log_print.print("=" * 60)
        return missing_list

    # ------------------------------------------------------------------ #
    #  HTTP helpers（与原 Spider 完全相同）                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_guid(text: str) -> str:
        if not text:
            return ""
        match = re.search(r"guid=([a-zA-Z0-9]+)", text)
        return match.group(1) if match else ""

    def open_area(
        self, area: Dict, program: Dict
    ) -> Tuple[Optional[str], Optional[dict], Dict, str]:
        cookies  = {}
        area_url = area.get("UrlArea") or self.area_url_tpl.format(
            area=area.get("CodigoArea"),
            programa=program.get("CodigoPrograma"),
        )
        if not area_url:
            return None, None, {}, ""

        response = self.single_fetcher.fetch(
            area_url, headers=self.headers_page, method="GET", check_size=False
        )
        if not response:
            return None, None, {}, ""

        response.encoding = response.apparent_encoding
        cookies.update(response.cookies.get_dict())

        validate_headers = dict(self.headers_list)
        pattern = re.compile(r"window\.AntiForgeryToken\s*=\s*'(.*?)'\s*;")
        match   = re.search(pattern, response.text)
        if match:
            validate_headers["requestverificationtoken"] = match.group(1)
            validate_headers["referer"] = area_url

        response_valid = self.single_fetcher.fetch(
            self.validate_url,
            headers=validate_headers,
            cookies=cookies,
            method="POST",
            check_size=False,
        )
        if response_valid:
            cookies.update(response_valid.cookies.get_dict())
        else:
            self.log_print.warning("response_valid 请求失败")
            return "", {}, {}, ""

        guid = self._extract_guid(area_url)
        html = response.text
        if not guid or not html:
            self.log_print.warning("guid 或首页为空，跳过该领域")
        return html, cookies, validate_headers, guid

    def get_list_page(
        self, page: int, guid: str, cookies: Dict, headers: Dict
    ) -> Optional[str]:
        params   = {"paginaInicial": str(page), "guid": guid}
        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=headers,
            cookies=cookies,
            params=params,
            method="POST",
            check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    @staticmethod
    def _parse_total_pages(html_text: str) -> int:
        soup   = BeautifulSoup(html_text, "html.parser")
        header = soup.find("div", class_="cabecalho-resultado-busca")
        if not header:
            return 1
        try:
            return int(header.attrs.get("data-total-paginas", 1))
        except Exception:
            return 1

    def parse_author(self, text: str) -> Dict:
        pattern = r"^(.*?)(?:,\s*(\d{4})-(.*))?$"
        match   = re.match(pattern, text.strip())
        if match:
            return {
                "name":  match.group(1).rstrip(",").strip(),
                "birth": match.group(2),
                "death": match.group(3).strip() if match.group(3) else None,
            }
        return {}

    def parse_list_page(self, html_text: str, program: Dict, area: Dict) -> List[Dict]:
        soup    = BeautifulSoup(html_text, "html.parser")
        now_ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = []

        for item in soup.select("div.col-xs-12.ficha-acervo-detalhe"):
            title_tag  = item.select_one("p.titulo a")
            title      = title_tag.get_text(strip=True) if title_tag else ""
            href       = title_tag.get("href") if title_tag else ""
            detail_url = (
                __import__("urllib.parse", fromlist=["urljoin"]).urljoin(
                    self.base_url, href
                )
                if href else ""
            )

            codigo_registro = ""
            m = re.search(r"/acervo/detalhe/(\d+)", href or "")
            if m:
                codigo_registro = m.group(1)

            author_tag   = item.select_one("p.autor a.link-autor")
            author_id    = author_tag.get("data-codigo-autor") if author_tag else ""
            author_title = author_tag.get("title") if author_tag else ""
            author_info  = self.parse_author(author_title)
            author_info["author_id"]    = author_id
            author_info["author_title"] = author_title

            material          = item.select_one("p.material")
            material_text     = material.get_text(strip=True) if material else ""

            numero_chamada    = item.select_one("p.numeroChamada")
            numero_chamada_text = (
                numero_chamada.get_text(" ", strip=True).replace("Número de chamada:", "").strip()
                if numero_chamada else ""
            )

            publicacao        = item.select_one("p.publicacao")
            publicacao_text   = (
                publicacao.get_text(" ", strip=True).replace("Publicação:", "").strip()
                if publicacao else ""
            )

            resumo_short = ""
            resumo_span  = item.select_one("span.texto-truncado")
            if resumo_span:
                resumo_short = resumo_span.get_text(" ", strip=True)

            assuntos = [
                {
                    "assunto":    tag.get_text(strip=True),
                    "assunto_id": tag.get("data-codigo-assunto"),
                }
                for tag in item.select("p.assunto a")
            ]

            site_dict = {}
            for site in item.select("p.sites"):
                rotulo      = site.select_one("span.rotulo")
                rotulo_text = (
                    (rotulo.attrs.get("title") or rotulo.get_text(strip=True).replace(":", "").strip())
                    if rotulo else ""
                )
                site_tag = site.select_one("a")
                if site_tag:
                    site_dict[str(rotulo_text)] = site_tag.attrs.get("href")

            file_url = ""
            for file_tag in item.select("div.arquivos a[href]"):
                fh = file_tag.get("href")
                if fh:
                    from urllib.parse import urljoin
                    file_url = urljoin(self.base_url, fh)
                    break

            if "DOI" in site_dict:
                id_code           = generate_doi_id(site_dict.get("DOI") or site_dict.get("doi"))
                site_dict["doi"]  = normalize_doi(site_dict.get("DOI"))
            else:
                id_code = generate_string_id(title)

            results.append({
                "_id":        id_code,
                "项目代码":   program.get("CodigoPrograma"),
                "项目名称":   program.get("NomePrograma"),
                "领域代码":   area.get("CodigoArea"),
                "领域名称":   area.get("NomeArea"),
                "标题":       title,
                "详情链接":   detail_url,
                "记录ID":     codigo_registro,
                "作者":       author_info,
                "材质":       material_text,
                "索书号":     numero_chamada_text,
                "出版信息":   publicacao_text,
                "摘要":       resumo_short,
                "file_url":   file_url,
                "主题":       assuntos,
                **site_dict,
                "create_time": now_ts,
            })
        return results

    # ------------------------------------------------------------------ #
    #  Step 2 — 补采单个 area（全量重爬）                                  #
    # ------------------------------------------------------------------ #

    def refetch_area(self, info: Dict, program_list: List[Dict]) -> bool:
        """
        重新爬取某个 area 的全部分页并写库。
        返回 True 表示本次补采成功（所有页采集完毕）。
        """
        prog_idx  = info["prog_idx"]
        area_idx  = info["area_idx"]
        area_name = info["area_name"]
        area_code = info["area_code"]

        program  = program_list[prog_idx]
        area     = program["AreasConcentracao"][area_idx]

        self.log_print.print(
            f"  → 补采 [{area_code}] {area_name}  "
            f"(期望:{info['expected']}  实有:{info['actual']}  缺:{info['missing']})"
        )

        first_html, cookies, headers, guid = self.open_area(area, program)
        if not guid or not first_html:
            self.log_print.warning(f"    open_area 失败，跳过 [{area_code}]")
            return False

        total_pages = self._parse_total_pages(first_html)
        self.log_print.print(f"    共 {total_pages} 页")

        # page 1 来自 first_html
        data_list = self.parse_list_page(first_html, program, area)
        if data_list:
            self.save_result(insert_list=data_list)
            self.log_print.print(f"    page:1/{total_pages} 写入 {len(data_list)} 条")
        else:
            self.log_print.warning(f"    page:1 解析无数据，跳过该 area")
            return False

        success = True
        for page in range(2, total_pages + 1):
            html_text = self.get_list_page(
                page=page, guid=guid, cookies=cookies, headers=headers
            )
            if html_text:
                data_list = self.parse_list_page(html_text, program, area)
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(
                        f"    page:{page}/{total_pages} 写入 {len(data_list)} 条"
                    )
                else:
                    self.log_print.warning(f"    page:{page} 解析无数据，中止该 area")
                    success = False
                    break
            else:
                self.log_print.warning(f"    page:{page} 请求失败，中止该 area")
                success = False
                break

            time.sleep(1)  # 礼貌间隔

        if success:
            self.log_print.print(f"    ✓ [{area_code}] 补采完成")
        return success

    # ------------------------------------------------------------------ #
    #  主入口                                                              #
    # ------------------------------------------------------------------ #

    def run_supplement(self, check_only: bool = False):
        program_list = self.load_hierarchy()
        if not program_list:
            self.log_print.error("program_list 为空，终止")
            return

        # ── Step 1：统计缺口 ─────────────────────────────────────────
        missing_list = self.check_missing(program_list)

        if not missing_list:
            self.log_print.print("🎉 所有 area 数量核对通过，无需补采！")
            return

        # 将缺口报告写到本地 JSON，便于人工查看
        report_path = os.path.join(Path(__file__).parent, "supplement_missing_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(missing_list, fh, ensure_ascii=False, indent=2)
        self.log_print.print(f"缺口报告已保存至: {report_path}")

        if check_only:
            self.log_print.print("--check 模式，跳过补采")
            return

        # ── Step 2：依次补采缺口 area ────────────────────────────────
        self.log_print.print("=" * 60)
        self.log_print.print(f"【补采阶段】共 {len(missing_list)} 个 area 需要补采")
        self.log_print.print("=" * 60)

        failed_list = []
        for idx, info in enumerate(missing_list, 1):
            self.log_print.print(
                f"[{idx}/{len(missing_list)}] 开始补采: "
                f"{info['prog_name']} - {info['area_name']}"
            )
            if info.get("missing","") and info["missing"] <=5:
                self.log_print.print(
                    f'{info["missing"]} 条缺口较小，建议人工核对后再决定是否补采 ,暂时跳过'
                )
                continue
            ok = self.refetch_area(info, program_list)
            if not ok:
                failed_list.append(info)
            time.sleep(2)  # area 之间稍作间隔

        # ── Step 3：补采后再次核对 ───────────────────────────────────
        self.log_print.print("=" * 60)
        self.log_print.print("【二次核对】补采完毕，重新统计数量……")
        self.log_print.print("=" * 60)

        still_missing = self.check_missing(program_list)

        if still_missing:
            self.log_print.warning(
                f"仍有 {len(still_missing)} 个 area 数量不足，"
                f"已记录至 supplement_missing_report_v2.json"
            )
            report2_path = os.path.join(
                Path(__file__).parent, "supplement_missing_report_v2.json"
            )
            with open(report2_path, "w", encoding="utf-8") as fh:
                json.dump(still_missing, fh, ensure_ascii=False, indent=2)
        else:
            self.log_print.print("🎉 全部 area 数量对齐，补采任务完成！")


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unicamp 补采工具")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只统计缺口，不执行补采",
    )
    args = parser.parse_args()

    spider = SupplementSpider()
    spider.run_supplement(check_only=args.check)
