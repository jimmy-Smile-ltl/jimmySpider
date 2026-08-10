import hashlib
from jimmyspider.config import get_config




def normalize_doi(doi):
    """标准化DOI格式"""
    if not doi:
        return None
    doi = str(doi).strip()
    # 移除常见的DOI前缀格式
    if doi.startswith('http://dx.doi.org/'):
        doi = doi.replace('http://dx.doi.org/', '')
    elif doi.startswith('https://dx.doi.org/'):
        doi = doi.replace('https://dx.doi.org/', '')
    elif doi.startswith('http://doi.org/'):
        doi = doi.replace('http://doi.org/', '')
    elif doi.startswith('https://doi.org/'):
        doi = doi.replace('https://doi.org/', '')
    return doi.lower() if doi else None

def generate_doi_id(doi):
    """使用 DOI 生成 md5 _id"""
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return None
    # 生成 MD5
    md5_hash = hashlib.md5(normalized_doi.encode('utf-8')).hexdigest()
    return md5_hash

def generate_title_id(title:str):
    md5_hash = hashlib.md5(title.encode('utf-8')).hexdigest()
    return md5_hash

def generate_string_id(title:str):
    md5_hash = hashlib.md5(title.encode('utf-8')).hexdigest()
    return md5_hash

from DrissionPage import ChromiumPage, ChromiumOptions
import time
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

def build_url_with_params(url, params):
    if not params:
        return url
    parts = urlparse(url)
    # 合并已有 query 和新 params，params 优先生效
    query = dict(parse_qsl(parts.query))
    query.update(params)
    new_query = urlencode(query, doseq=True)
    return urlunparse(parts._replace(query=new_query))
import os
def get_cookies_by_url(url, params={} ,click_checkbox=True, wait_sec=2, headless=True):
    """访问 URL，必要时点击 checkbox，返回可直接给 requests 使用的 cookies dict。"""

    print("正在配置浏览器参数...")
    options = ChromiumOptions()
    #
    # # 1. 启用无头模式（Linux 服务器环境必须）
    options.headless(False)

    # 2. 基础稳定性参数
    options.set_argument('--no-sandbox')  # 绕过操作系统安全沙箱（Linux 必备）
    options.set_argument('--disable-gpu')  # 禁用 GPU 加速（无头模式推荐）
    options.set_argument('--disable-dev-shm-usage')  # 解决 Linux 内存共享不足导致的崩溃
    options.set_argument('--window-size=1920,1080')  # 显式设置窗口大小，防止布局错乱

    # 3. 伪装参数
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_local_port(9223)
    page = ChromiumPage(addr_or_opts=options)
    try:
        if not params:
            page.get(url)
        else:
            full_url = build_url_with_params(url, params)
            page.get(full_url)
        time.sleep(wait_sec)

        if click_checkbox:
            checkbox = page.ele('tag:input@type=checkbox', timeout=10)
            if checkbox:
                for _ in range(10):
                    try:
                        checkbox.click(by_js=True)
                        break
                    except Exception:
                        time.sleep(1)
                page.wait.load_start()

        time.sleep(wait_sec)

        # DrissionPage 返回 cookies 列表，这里转成 dict
        cookies_list = page.cookies()  # list of dicts
        cookies_dict = {c.get('name'): c.get('value') for c in cookies_list if c.get('name')}
        return cookies_dict
    finally:
        page.close()

def safe_extract_json(data, path, default=""):
    """
    安全地从嵌套的字典和列表中提取数据。

    :param data: 原始的字典或列表。
    :param path: 一个包含键名和索引的访问路径列表，例如 ['key1', 0, 'key2']。
    :param default: 如果路径中任何一步失败，返回的默认值。
    :return: 提取到的值或默认值。
    """
    current_data = data
    for key in path:
        # 检查当前数据是否可以进行下一步提取
        if isinstance(current_data, dict):
            current_data = current_data.get(key)
        elif isinstance(current_data, list):
            # 只有在索引是整数且列表不为空的情况下才尝试访问
            if isinstance(key, int) and -len(current_data) <= key < len(current_data):
                current_data = current_data[key]
            else:
                return default  # 索引无效或类型不匹配
        else:
            return default  # 无法继续深入

        # 如果任何一步返回None，则提前中止并返回默认值
        if current_data is None:
            return default

    return current_data if current_data is not None else default

def rename_dict_key_safe(d, old_key, new_key, overwrite=False):
    if old_key not in d:
        return d  # 或者 raise KeyError(f"Key '{old_key}' not found")
    if new_key in d and not overwrite:
        print(f"Key '{new_key}' already exists and overwrite=False")
    d[new_key] = d.pop(old_key)
    return d

def rename_keys_by_mapping(original_dict, key_mapping):
    """
    根据 key_mapping 字典批量重命名 original_dict 的键。
    key_mapping 格式: {旧键名: 新键名}
    返回新的字典（不修改原字典）
    """
    new_dict = {}
    for k, v in original_dict.items():
        if k in key_mapping:
            new_key = key_mapping[k]
            new_dict[new_key] = v
        else:
            new_dict[k] = v
    return new_dict

# 或者直接在原字典上修改（更节省内存）
def rename_keys_inplace(original_dict, key_mapping):
    for old, new in key_mapping.items():
        if old in original_dict:
            original_dict[new] = original_dict.pop(old)
    return original_dict


def mark_downloaded_by_doi(
    doi_column,
    source_collection,
):
    from jimmyspider.mark_downloaded import DownloadMarker
    check_mongo_uri = get_config().MONGO_URI
    check_db = "all_journals"  # 可配置项：查询下载状态库名，按需修改
    check_collection = "all_articles_by_doi"

    marker = DownloadMarker(
        check_mongo_uri=check_mongo_uri,
        check_db_name=check_db,
        check_collection=check_collection
    )
    # 创建标记器
    batch_size = 1000
    source_mongo_uri = get_config().MONGO_URI
    source_db = get_config().MONGO_DB
    status_column = "downloaded"
    marker.mark_mongodb_collection(
        source_mongo_uri=source_mongo_uri,
        source_db_name=source_db,
        source_collection_name=source_collection,
        doi_field=doi_column,
        status_field=status_column,
        batch_size=batch_size
    )
    marker.close()

