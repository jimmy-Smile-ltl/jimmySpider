"""
unicamp_br/spider_list_by_year.py
──────────────────────────────────
示例：UNICAMP 学术库 (https://repositorio.unicamp.br) 列表抓取 —— 简化会话策略版。

演示内容：
  1. 从 unicamp_academic_hierarchy.json 读取 program → area 学术层级，
     双层循环遍历全部领域
  2. 与 spider_list_by_area.py 的会话流程不同：本版本先访问 /Busca/Avancada
     初始化会话并提取 __RequestVerificationToken，再从响应 URL 中提取 guid
     （不经过 validaacessodetalhe 验证接口）
  3. 分页 POST API（CarregarPaginaLayoutDetalhe）抓取列表，
     解析 ficha-acervo-detalhe 条目（多文件链接收集等）
  4. 断点续爬（log_progress）+ 错误页重试（error_page_set）

与 spider_list_by_area.py、spider_supplement_by_area.py 对比，
展示同一站点的三种不同抓取策略。

注意：
  - unicamp_academic_hierarchy.json 为运行时依赖的学术层级数据文件，
    需与本文件同目录。
"""

import os
import re
import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id, generate_doi_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = Path(__file__).parent
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://repositorio.unicamp.br"
        self.advance_url = "https://repositorio.unicamp.br/Busca/Avancada"
        self.area_url_tpl = "https://repositorio.unicamp.br/Busca/AreaConcentracao?codigo={area}&programa={programa}"
        self.list_api_url = "https://repositorio.unicamp.br/Resultado/CarregarPaginaLayoutDetalhe"

        self.headers_page = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://repositorio.unicamp.br/Resultado/Listar?guid=",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
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
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }

        self.hierarchy_path = os.path.join(Path(__file__).parent, "unicamp_academic_hierarchy.json")

        self.log_progress = Cache(f"{self.table_name}_log_progress")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Hierarchy                                                           #
    # ------------------------------------------------------------------ #

    def load_hierarchy(self) -> List[Dict]:
        if not os.path.exists(self.hierarchy_path):
            self.log_print.error(f"hierarchy not found: {self.hierarchy_path}")
            return []
        with open(self.hierarchy_path, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except Exception as exc:
                self.log_print.error(f"hierarchy decode error: {exc}")
                return []

    # ------------------------------------------------------------------ #
    #  Session / guid helpers                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_token(html_text: str) -> str:
        if not html_text:
            return ""
        patterns = [
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
            r'requestverificationtoken"\s*content="([^"]+)"',
            r'__RequestVerificationToken"\s*:\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_guid(text: str) -> str:
        if not text:
            return ""
        match = re.search(r"guid=([a-zA-Z0-9]+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _strip_guid(url: str) -> str:
        if not url:
            return url
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "guid" in query:
            query.pop("guid", None)
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _init_session(self) -> Tuple[Dict, str]:
        response = self.single_fetcher.fetch(
            self.advance_url,
            headers=self.headers_page,
            method="GET",
            check_size=False,
        )
        if not response:
            return {}, ""
        response.encoding = response.apparent_encoding
        cookies = response.cookies.get_dict()
        token = self._extract_token(response.text)
        return cookies, token

    def _open_area(self, area: Dict, program: Dict) -> Tuple[str, Dict, str]:
        cookies, token = self._init_session()
        area_url = area.get("UrlArea") or self.area_url_tpl.format(
            area=area.get("CodigoArea"),
            programa=program.get("CodigoPrograma"),
        )
        area_url = self._strip_guid(area_url)

        response = self.single_fetcher.fetch(
            area_url,
            headers=self.headers_page,
            cookies=cookies,
            method="GET",
            check_size=False,
        )
        if response:
            response.encoding = response.apparent_encoding
            cookies.update(response.cookies.get_dict())
            token = token or self._extract_token(response.text)
            guid = self._extract_guid(response.url) or self._extract_guid(response.text)
            if guid:
                return guid, cookies, token
        guid = self._extract_guid(area.get("UrlArea", ""))
        return guid, cookies, token

    # ------------------------------------------------------------------ #
    #  List pages                                                          #
    # ------------------------------------------------------------------ #

    def get_list_page(self, page: int, guid: str, cookies: Dict, token: str) -> Optional[str]:
        params = {
            "paginaInicial": str(page),
            "guid": guid,
        }
        headers = dict(self.headers_list)
        headers["referer"] = f"https://repositorio.unicamp.br/Resultado/Listar?guid={guid}"
        if token:
            headers["requestverificationtoken"] = token
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

    def parse_list_page(self, html_text: str, program: Dict, area: Dict) -> List[Dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results = []
        for item in soup.select("div.col-xs-12.ficha-acervo-detalhe"):
            title_tag = item.select_one("p.titulo a")
            title = title_tag.get_text(strip=True) if title_tag else ""
            href = title_tag.get("href") if title_tag else ""
            detail_url = urljoin(self.base_url, href) if href else ""

            codigo_registro = ""
            match = re.search(r"/acervo/detalhe/(\d+)", href or "")
            if match:
                codigo_registro = match.group(1)

            author_tag = item.select_one("p.autor a.link-autor")
            author_text = author_tag.get_text(strip=True) if author_tag else ""
            author_id = author_tag.get("data-codigo-autor") if author_tag else ""
            author_title = author_tag.get("title") if author_tag else ""

            material = item.select_one("p.material")
            material_text = material.get_text(strip=True) if material else ""

            numero_chamada = item.select_one("p.numeroChamada")
            numero_chamada_text = numero_chamada.get_text(" ", strip=True) if numero_chamada else ""
            numero_chamada_text = numero_chamada_text.replace("Número de chamada:", "").strip()

            publicacao = item.select_one("p.publicacao")
            publicacao_text = publicacao.get_text(" ", strip=True) if publicacao else ""
            publicacao_text = publicacao_text.replace("Publicação:", "").strip()

            resumo_short = ""
            resumo_span = item.select_one("span.texto-truncado")
            if resumo_span:
                resumo_short = resumo_span.get_text(" ", strip=True)

            assuntos = []
            for assunto_tag in item.select("p.assunto a"):
                assuntos.append({
                    "assunto": assunto_tag.get_text(strip=True),
                    "assunto_id": assunto_tag.get("data-codigo-assunto"),
                })

            site_dict = {}
            for site in item.select("p.sites"):
                rotulo = site.select_one("span.rotulo")
                rotulo_text = (rotulo.atts.get("title") if rotulo else "") or (rotulo.get_text(strip=True).replace(":", "").strip() if rotulo else "")
                site_tag = site.select_one("a")
                if site_tag:
                    site_dict[str(rotulo_text)] = site_tag.attrs.get("href")

            file_urls = []
            file_titles = []
            for file_tag in item.select("div.arquivos a[href]"):
                file_href = file_tag.get("href")
                if not file_href:
                    continue
                file_urls.append(urljoin(self.base_url, file_href))
                file_titles.append(file_tag.get_text(strip=True))
            if "DOI" in site_dict or "doi" in site_dict:
                id_code = generate_doi_id(site_dict.get("DOI") or site_dict.get("doi"))
            else:
                id_code = generate_string_id(title)
            result = {
                "_id": id_code,
                "程序代码": program.get("CodigoPrograma"),
                "程序名称": program.get("NomePrograma"),
                "领域代码": area.get("CodigoArea"),
                "领域名称": area.get("NomeArea"),
                "标题": title,
                "详情链接": detail_url,
                "记录ID": codigo_registro,
                "作者": author_text,
                "作者ID": author_id,
                "作者原文": author_title,
                "材质": material_text,
                "索书号": numero_chamada_text,
                "出版信息": publicacao_text,
                "摘要": resumo_short,
                "文件链接": file_urls,
                "文件标题": file_titles,
                "主题": assuntos,
                **site_dict,
                "create_time": now_ts,
            }
            results.append(result)

        return results

    # ------------------------------------------------------------------ #
    #  Error handling                                                      #
    # ------------------------------------------------------------------ #

    def handle_error_page(self, program_list: List[Dict]) -> bool:
        error_keys = list(self.error_page_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_keys:
            page_info = self._decode_cache(error_key)
            prog_idx = page_info.get("prog_idx")
            area_idx = page_info.get("area_idx")
            page = page_info.get("page")
            if prog_idx is None or area_idx is None:
                self.error_page_set.remove_from_set(error_key)
                continue
            if prog_idx >= len(program_list):
                self.error_page_set.remove_from_set(error_key)
                continue
            program = program_list[prog_idx]
            area_list = program.get("AreasConcentracao", [])
            if area_idx >= len(area_list):
                self.error_page_set.remove_from_set(error_key)
                continue
            area = area_list[area_idx]

            guid, cookies, token = self._open_area(area, program)
            if not guid:
                self.log_print.print(f"handle_error_page: 无 guid prog:{prog_idx} area:{area_idx}")
                return False
            html_text = self.get_list_page(page, guid, cookies, token)
            if html_text:
                data_list = self.parse_list_page(html_text, program, area)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_page prog:{prog_idx} area:{area_idx} page:{page} 采集失败")
                return False

        return len(self.error_page_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        program_list = self.load_hierarchy()
        if not program_list:
            self.log_print.error("program_list 为空，终止")
            return

        progress_str = self.log_progress.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_prog_idx = progress.get("prog_idx", 0)
            start_area_idx = progress.get("area_idx", 0)
            start_page = progress.get("page", 1)
        else:
            start_prog_idx = 0
            start_area_idx = 0
            start_page = 1

        self.log_print.print(
            f"开始抓取 Unicamp 列表, 恢复自 prog_idx:{start_prog_idx}, area_idx:{start_area_idx}, page:{start_page}..."
        )

        for prog_idx in range(start_prog_idx, len(program_list)):
            program = program_list[prog_idx]
            area_list = program.get("AreasConcentracao", [])
            if not area_list:
                continue

            area_start = start_area_idx if prog_idx == start_prog_idx else 0
            for area_idx in range(area_start, len(area_list)):
                area = area_list[area_idx]
                area_code = area.get("CodigoArea")
                area_name = area.get("NomeArea")

                page = start_page if (prog_idx == start_prog_idx and area_idx == area_start) else 1

                self.log_print.print(
                    f"开始抓取: {program.get('NomePrograma')} - {area_name} (area={area_code})"
                )

                guid, cookies, token = self._open_area(area, program)
                if not guid:
                    self.log_print.warning("  guid 为空，跳过该领域")
                    continue

                while True:
                    html_text = self.get_list_page(page, guid, cookies, token)
                    if html_text:
                        data_list = self.parse_list_page(html_text, program, area)
                        if data_list:
                            self.save_result(insert_list=data_list)
                            self.log_print.print(
                                f"  page:{page} 采集成功 {len(data_list)} 条"
                            )
                        else:
                            self.log_print.warning("  解析无数据，结束该领域")
                            break

                        self.log_progress.record_string(json.dumps({
                            "prog_idx": prog_idx,
                            "area_idx": area_idx,
                            "page": page + 1,
                        }))

                        page += 1
                        time.sleep(1)
                    else:
                        self.log_print.print(
                            f"  page:{page} 列表请求失败，记录错误页"
                        )
                        self.error_page_set.add_to_set(self._encode_cache({
                            "prog_idx": prog_idx,
                            "area_idx": area_idx,
                            "page": page,
                        }))
                        break

        self.log_print.print("主流程采集完成")
        self.log_progress.clear_value()

        for retry in range(3):
            self.log_print.warning(f"开始处理错误 page (第 {retry + 1} 次)")
            if self.handle_error_page(program_list):
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()
