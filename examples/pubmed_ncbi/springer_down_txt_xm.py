#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Example: pubmed_ncbi — Springer PDF batch downloader (companion utility).

This example demonstrates a standalone multi-threaded file-downloader:
it reads DOIs from a local JSONL/TXT file, downloads PDFs from
link.springer.com with retries (including the *_reference.pdf fallback),
writes files into timestamped batch directories (max batch_size per dir),
and keeps success/failure records on disk for resume (断点续传).

Note: the original carried a full set of hardcoded session cookies; those have
been removed. Obtain a fresh session (cookies dict) before running.

Springer PDF 下载脚本（本地TXT版本）
从本地 TXT 文件读取 DOI 列表，下载成功后记录到本地文件
"""

import hashlib
import json
import os
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from jimmyspider.tool import normalize_doi

from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
from urllib3.exceptions import InsecureRequestWarning

import requests
from tqdm import tqdm

warnings.filterwarnings("ignore", category=InsecureRequestWarning)


# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('springer_download_txt.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# 请求配置
# 原版本硬编码了一整套 Springer 会话 cookies，已移除。
# 运行前请通过浏览器登录抓取新的会话 cookies 填入此字典。
cookies = {}

headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'zh-CN,zh;q=0.9',
    'cache-control': 'no-cache',
    'pragma': 'no-cache',
    'priority': 'u=0, i',
    'referer': 'https://springer.z.scidown.top/search?query=10.3103%2Fs0025654420080129',
    'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
}


class SpringerPDFTxtDownloader:
    """Springer PDF下载器（本地TXT版本）"""

    def __init__(
        self,
        input_file: str,
        output_dir: str = "./springer_pdfs",
        max_workers: int = 10,
        batch_size: int = 10000,
    ):
        """初始化下载器

        Args:
            input_file: 输入的DOI列表文件路径（每行一个DOI）
            output_dir: PDF输出目录
            max_workers: 并发线程数
            batch_size: 每批次文件数量
        """
        self.input_file = input_file
        self.output_dir = output_dir
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.max_retries = 3
        self.timeout = 60

        # 记录文件
        self.success_file = os.path.join(output_dir, 'download_success.txt')
        self.failed_file = os.path.join(output_dir, 'download_failed.jsonl')

        # 批次目录管理
        self._batch_lock = threading.Lock()
        self._current_batch_dir: Optional[str] = None
        self._current_batch_count: int = 0

        # 记录锁
        self._success_lock = threading.Lock()
        self._failed_lock = threading.Lock()

        # 创建输出目录
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        # 检查输入文件
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        logger.info(f"初始化完成，输入文件: {input_file}")

    def load_dois_from_txt(self) -> List[str]:
        """从TXT文件加载DOI列表

        Returns:
            DOI列表
        """
        logger.info(f"正在从文件加载DOI列表: {self.input_file}")

        dois = []
        with open(self.input_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                doi = line.strip()
                if doi.startswith('10.1007'):
                    dois.append(doi)
                elif doi.startswith('10.1186'):
                    dois.append(doi)
                elif doi.startswith('10.1038'):
                    dois.append(doi)
                continue
                # data = json.loads(line)
                # doi = data.get('doi', '').strip()
                # _id = data.get('_id', '').strip()
                # if doi and int(_id) > 10000000:
                dois.append(doi)
        # dois = dois[100:]
        logger.info(f"从文件中读取到 {len(dois)} 个DOI")

        return dois

    def load_downloaded_dois(self) -> Set[str]:
        """加载已下载成功的DOI列表（用于断点续传）

        Returns:
            已下载的DOI集合
        """
        if not os.path.exists(self.success_file):
            return set()

        downloaded = set()
        with open(self.success_file, 'r', encoding='utf-8') as f:
            for line in f:
                doi = line.strip()
                if doi:
                    downloaded.add(doi)

        logger.info(f"已下载 {len(downloaded)} 个DOI（断点续传）")
        return downloaded

    def record_success(self, doi: str) -> None:
        """记录下载成功的DOI

        Args:
            doi: DOI
        """
        with self._success_lock:
            with open(self.success_file, 'a', encoding='utf-8') as f:
                f.write(f"{doi}\n")

    def record_failed(self, doi: str, error: str) -> None:
        """记录下载失败的DOI

        Args:
            doi: DOI
            error: 错误信息
        """
        with self._failed_lock:
            record = {
                'doi': doi,
                'error': error,
                'timestamp': datetime.now().isoformat()
            }
            with open(self.failed_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def allocate_output_dir(self) -> str:
        """按时间戳创建批次目录，每批最多batch_size个文件。线程安全。

        Returns:
            批次目录路径
        """
        with self._batch_lock:
            # 未初始化或当前批次已满 -> 新建批次
            if self._current_batch_dir is None or self._current_batch_count >= self.batch_size:
                today_str = datetime.now().strftime('%Y-%m-%d')
                self._current_batch_dir = os.path.join(self.output_dir, f"{today_str}")
                Path(self._current_batch_dir).mkdir(parents=True, exist_ok=True)
                self._current_batch_count = 0
                logger.info(f"创建新批次目录: {self._current_batch_dir}")

            # 分配当前批次计数
            self._current_batch_count += 1
            return self._current_batch_dir

    def download_single_pdf(self, doi: str, output_dir: str) -> Dict[str, Any]:
        """下载单个PDF

        Args:
            doi: DOI
            output_dir: 输出目录

        Returns:
            下载结果字典 {doi, status, filename, file_size, error}
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        key = normalize_doi(doi) or str(doi).strip().lower()
        filename = f"{hashlib.md5(key.encode('utf-8')).hexdigest()}.pdf"
        filepath = output_path / filename

        # 已存在且大小>0，跳过
        if filepath.exists() and filepath.stat().st_size > 0:
            logger.info(f"文件已存在，跳过: {filename}")
            return {
                'doi': doi,
                'status': 'skipped',
                'filename': str(filepath.name),
                'file_size': filepath.stat().st_size
            }

        # 构建PDF下载URL
        if doi.startswith('10.1007'):
            pdf_url = f"https://link.springer.com/content/pdf/{doi}.pdf"
        elif doi.startswith('10.1186'):
            pdf_url = f"https://link.springer.com/content/pdf/{doi}_reference.pdf"
        elif doi.startswith('10.1038'):
            pdf_url = f"https://link.springer.com/content/pdf/{doi}_reference.pdf"

        # 尝试下载（最多重试3次）
        for attempt in range(self.max_retries):
            if attempt > 1:
                pdf_url = pdf_url.replace('.pdf', '_reference.pdf')
                # time.sleep((attempt + 1) * 2)
            try:
                logger.info(f"下载 {doi} 尝试 {attempt + 1}/{self.max_retries}: {pdf_url}")

                response = requests.get(
                    pdf_url,
                    cookies=cookies,
                    headers=headers,
                    verify=False,
                    timeout=self.timeout,
                    proxies={},
                    stream=True
                )

                if response.status_code == 200:
                    # 检查是否是有效的PDF
                    if response.content.startswith(b'%PDF'):
                        with open(filepath, 'wb') as f:
                            f.write(response.content)

                        if filepath.exists() and filepath.stat().st_size > 0:
                            file_size = filepath.stat().st_size
                            logger.info(f"下载成功: {doi} ({file_size} bytes)")
                            return {
                                'doi': doi,
                                'status': 'success',
                                'filename': str(filepath.name),
                                'file_size': file_size
                            }
                        else:
                            logger.warning(f"{doi} 下载的文件为空")
                            if filepath.exists():
                                filepath.unlink()
                    else:
                        logger.warning(f"{doi} 响应内容不是有效PDF")
                        if filepath.exists():
                            filepath.unlink()
                else:
                    logger.warning(f"{doi} HTTP状态码: {response.status_code}")

            except Exception as e:
                logger.warning(f"{doi} 下载异常 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if filepath.exists():
                    filepath.unlink()

            # 等待后重试
            if attempt < self.max_retries - 1:
                time.sleep((attempt + 1) * 2)

        logger.error(f"下载失败（超过最大重试）: {doi}")
        return {
            'doi': doi,
            'status': 'failed',
            'filename': filename,
            'error': f'下载失败，已重试{self.max_retries}次'
        }

    def worker(self, doi: str) -> Dict[str, Any]:
        """工作线程函数

        Args:
            doi: DOI字符串

        Returns:
            处理结果字典
        """
        if not doi:
            return {
                'success': False,
                'doi': 'unknown',
                'error': '空DOI'
            }

        # 分配输出目录
        target_dir = self.allocate_output_dir()

        # 下载PDF
        result = self.download_single_pdf(doi, target_dir)
        status = result.get('status')

        if status == 'success':
            # 记录下载成功
            self.record_success(doi)
            return {
                'success': True,
                'doi': doi,
                'filename': result['filename'],
                'file_size': result.get('file_size', 0)
            }

        elif status == 'skipped':
            # 已存在也记录为成功
            self.record_success(doi)
            return {
                'success': True,
                'doi': doi,
                'filename': result['filename'],
                'file_size': result.get('file_size', 0),
                'skipped': True
            }

        else:
            # 记录下载失败
            error_msg = result.get('error', 'download_failed')
            self.record_failed(doi, error_msg)
            return {
                'success': False,
                'doi': doi,
                'error': error_msg
            }

    def run(self, skip_downloaded: bool = True, limit: Optional[int] = None) -> None:
        """运行下载任务

        Args:
            skip_downloaded: 是否跳过已下载的DOI（断点续传）
            limit: 限制下载数量
        """
        logger.info("=== Springer PDF下载器（本地TXT版本）===\n")

        # 加载DOI列表
        all_dois = self.load_dois_from_txt()

        if not all_dois:
            logger.info('DOI列表为空')
            return

        # 如果需要断点续传，过滤已下载的DOI
        dois_to_download = all_dois
        if skip_downloaded:
            downloaded_dois = self.load_downloaded_dois()
            dois_to_download = [doi for doi in all_dois if doi not in downloaded_dois]
            logger.info(f"跳过已下载: {len(all_dois) - len(dois_to_download)} 个")

        # 如果设置了limit，限制数量
        if limit and limit < len(dois_to_download):
            dois_to_download = dois_to_download[:limit]
            logger.info(f"限制本次下载数量: {limit}")

        if not dois_to_download:
            logger.info('没有待下载的DOI')
            return
        # dois_to_download = dois_to_download[1048:]
        total = len(dois_to_download)
        success = 0
        failed = 0
        skipped = 0

        logger.info(f"\n下载配置:")
        logger.info(f"  总DOI数: {len(all_dois)}")
        logger.info(f"  本次下载: {total}")
        logger.info(f"  并发线程数: {self.max_workers}")
        logger.info(f"  每批次文件数: {self.batch_size}")
        logger.info(f"  输出目录: {os.path.abspath(self.output_dir)}")
        logger.info(f"  成功记录: {self.success_file}")
        logger.info(f"  失败记录: {self.failed_file}\n")

        # 使用线程池并发下载
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_doi = {executor.submit(self.worker, doi): doi for doi in dois_to_download}

            # 使用tqdm显示进度
            for future in tqdm(as_completed(future_to_doi), total=total, desc="下载进度"):
                doi = future_to_doi[future]

                try:
                    result = future.result()

                    if result.get('success'):
                        if result.get('skipped'):
                            skipped += 1
                            logger.debug(f"✓ [{success + failed + skipped}/{total}] {doi} 已存在，跳过")
                        else:
                            success += 1
                            logger.info(f"✓ [{success + failed + skipped}/{total}] {doi} 下载成功")
                    else:
                        failed += 1
                        error = result.get('error', 'unknown error')
                        logger.error(f"✗ [{success + failed + skipped}/{total}] {doi} 下载失败: {error}")

                except Exception as e:
                    failed += 1
                    logger.error(f"✗ 任务异常 {doi}: {e}")

                # 每10个任务输出一次统计
                done = success + failed + skipped
                if done % 10 == 0 or done == total:
                    logger.info(f"进度: {done}/{total} 成功:{success} 失败:{failed} 跳过:{skipped}")

        # 显示最终统计
        logger.info(f"\n=== 下载完成 ===")
        logger.info(f"本次下载成功: {success}")
        logger.info(f"本次下载失败: {failed}")
        logger.info(f"本次跳过: {skipped}")
        logger.info(f"下载目录: {os.path.abspath(self.output_dir)}")
        logger.info(f"成功记录文件: {os.path.abspath(self.success_file)}")
        logger.info(f"失败记录文件: {os.path.abspath(self.failed_file)}")


# def parse_args():
#     """解析命令行参数"""
#     import argparse
#     parser = argparse.ArgumentParser(description='Springer PDF下载器（本地TXT版本）')
#     parser.add_argument('input_file', type=str,
#                         help='输入的DOI列表文件（每行一个DOI）',default="pubmed_50_clean.jsonl")
#     parser.add_argument('--output-dir', '--output_dir', type=str, default='./springer_pdfs',
#                         dest='output_dir',
#                         help='PDF输出目录（默认：./springer_pdfs）')
#     parser.add_argument('--workers', type=int, default=10,
#                         help='并发线程数（默认：10）')
#     parser.add_argument('--batch-size', '--batch_size', type=int, default=10000,
#                         dest='batch_size',
#                         help='每批次文件数量（默认：10000）')
#     parser.add_argument('--limit', type=int, default=None,
#                         help='限制本次下载的DOI数量（默认：全部）')
#     parser.add_argument('--no-skip', '--no_skip', action='store_true',
#                         dest='no_skip',
#                         help='不跳过已下载的DOI（默认会跳过）')
#     return parser.parse_args()


def main():
    """主函数"""

    input_file = "doi.jsonl"
    output_dir = "./springer_pdfs"
    max_workers = 10
    batch_size = 10
    limit = None
    no_skip = True

    downloader = SpringerPDFTxtDownloader(
        input_file=input_file,
        output_dir=output_dir,
        max_workers=max_workers,
        batch_size=batch_size,
    )

    try:
        downloader.run(
            skip_downloaded=not no_skip,
            limit=limit
        )
    except KeyboardInterrupt:
        logger.info('\n用户中断下载')
    except Exception as e:
        logger.error(f'下载过程中出现错误: {e}', exc_info=True)


if __name__ == '__main__':
    main()
