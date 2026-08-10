import re
import urllib
import urllib.parse
from copy import deepcopy

import bs4


class extractSoup:
    """处理BeautifulSoup对象的工具类"""

    def __init__(self):
        self.description = "处理BeautifulSoup对象的工具类，提供各种提取方法"
        self.name = "extractSoup"


    @staticmethod
    def extract_text(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str):
        """获取文本内容"""
        element = soup.select_one(selector)
        return element.get_text(separator=" ", strip=True) if element else ""

    @staticmethod
    def extract_texts(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str):
        """提取指定选择器的所有文本内容"""
        elements = soup.select(selector)
        return [el.get_text(separator=" ", strip=True) for el in elements if el.get_text(separator=" ", strip=True)]

    @staticmethod
    def extract_href(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str):
        """获取链接"""
        element = soup.select_one(selector)
        if not element:
            return ""
        if "href" in element.attrs:
            return element["href"]
        elif element.has_attr('data-src'):
            return element['data-src']
        elif "src" in element.attrs:
            return element["src"]
        else:
            return ""

    @staticmethod
    def extract_dict(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str):
        """获取字典"""
        elements = soup.select(selector)
        return {el.get_text(separator=" ", strip=True): el["href"] for el in elements if
                el.get_text(separator=" ", strip=True) and "href" in el.attrs}

    @staticmethod
    def extract_list_url(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str):
        """获取列表中的链接"""
        elements = soup.select(selector)
        return [el["href"] for el in elements if "href" in el.attrs]

    ## 提取视频 音频的url 生成list,注意潜在报错的的处理
    @staticmethod
    def extract_media_urls(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str, attr):
        """提取媒体元素的URL"""
        if not soup:
            return []
        elements = soup.select(selector)
        urls = []
        for el in elements:
            if attr in el.attrs:
                urls.append(el[attr])
            elif "href" in el.attrs:
                urls.append(el["href"])
            elif "src" in el.attrs:
                urls.append(el["src"])
            else:
                continue
        return urls

    ## 提取视频 音频的url 生成list,注意潜在报错的的处理
    @staticmethod
    def extract_pic_urls(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str, ) -> list[str]:
        """提取图片元素的URL ,在src 或者href属性 href优先，需要做图片合法性检测"""
        elements = soup.select(selector)
        urls = []
        for el in elements:
            if "href" in el.attrs:
                urls.append(el["href"])
            elif el.has_attr('data-src'):
                urls.append(el['data-src'])
            elif "src" in el.attrs:
                urls.append(el["src"])
            else:
                continue
        # 检查链接合法性
        corrected_urls = []
        for url in urls:
            if re.search(r'\.(png|jpg|jpeg|gif|bmp)(\?|$)', url, re.IGNORECASE):
                if url.startswith('//'):
                    corrected_urls.append('https:' + url)  # 补全协议头
                elif not url.startswith(('http://', 'https://')):
                    print(f"疑似相对路径: {url},可调用extract_pic_urls_relativeURL方法处理")
            else:  # 如果不是图片链接，跳过
                continue
        return corrected_urls

    @staticmethod
    def extract_degree_text(p_element):
        """
        从 <p class="degree"> 元素中提取直接文本节点内容，去除末尾的 ', '
        """
        if not p_element:
            return ""
        text_parts = []
        for child in p_element.contents:
            if isinstance(child, str):  # 文本节点
                text_parts.append(child)
        combined = ''.join(text_parts)
        # 去除末尾的逗号和空格
        if combined.endswith(', '):
            combined = combined[:-2]
        return combined

    @staticmethod
    def extract_urls_relativeURL(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str, relative_url: str) -> list[
        str]:
        """
                从给定的soup对象中提取、清理并补全多种文件类型的URL。

                这个函数会：
                1. 根据CSS选择器找到所有元素。
                2. 优先从 'href' 属性提取URL，其次是 'src'。
                3. 验证URL是否以支持的文件后缀结尾。
                4. 移除URL中的所有查询参数 (即 '?' 之后的内容)。
                5. 自动补全协议头 (如 'https://') 和相对路径。
                6. 处理一些特殊的URL结尾 (如 .jpg.1)。

                :param soup: BeautifulSoup的soup或Tag对象。
                :param selector: 用于查找元素的CSS选择器。
                :param base_url: 页面的基础URL，用于补全相对路径。
                :return: 一个包含所有清理和补全后的有效文件URL的列表。
                """
        SUPPORTED_FILE_EXTENSIONS = [
            # Images
            'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp',
            # Audio
            'mp3', 'wav', 'ogg', 'flac',
            # Video
            'mp4', 'webm', 'mov', 'avi',
            # Documents
            'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
            # Archives
            'zip', 'rar', '7z', 'tar', 'gz'
        ]
        if not soup:
            return []
        elements = soup.select(selector)
        raw_urls = []

        # 1. 提取原始URL，优先使用href
        for el in elements:
            if el.has_attr('href'):
                raw_urls.append(el['href'])
            elif el.has_attr('data-src'):
                raw_urls.append(el['data-src'])
            elif el.has_attr('src'):
                raw_urls.append(el['src'])


        # 动态构建正则表达式，用于匹配所有支持的文件后缀
        # 例如: \.(png|jpg|...|zip)(\.\d+)?$
        extensions_pattern = '|'.join(SUPPORTED_FILE_EXTENSIONS)
        file_pattern = re.compile(rf'\.({extensions_pattern})(\.\d+)?$', re.IGNORECASE)

        cleaned_urls = []
        for url in raw_urls:
            if not url or not isinstance(url, str):
                continue

            # 2. 清理URL：移除查询参数
            # 使用urlparse可以更健壮地处理URL
            parsed_url = urllib.parse.urlparse(url)
            # 只保留 scheme, netloc, path, params, fragment
            # 清空 query 部分
            url_without_params = urllib.parse.urlunparse(
                (parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, '', '')
            )

            # # 3. 验证URL是否是支持的文件类型
            # if not file_pattern.search(url_without_params):
            #     continue  # 如果不是我们想要的文件类型，则跳过
            #
            # # 4. 处理特殊的URL结尾，例如 .jpg.1
            # # 使用正则表达式替换掉结尾的 ".数字"
            # match = file_pattern.search(url_without_params)
            # if match and match.group(2):  # group(2) 对应 (\.\d+)?
            #     url_without_params = url_without_params.removesuffix(match.group(2))

            # 5. 补全URL
            final_url = ''
            if url_without_params.startswith('//'):
                # 补全协议头 (e.g., //example.com/a.jpg -> https://example.com/a.jpg)
                base_scheme = urllib.parse.urlparse(relative_url).scheme
                final_url = f"{base_scheme}:{url_without_params}"
            elif not url_without_params.startswith(('http://', 'https://')):
                # 补全相对路径 (e.g., /img/a.jpg -> https://example.com/img/a.jpg)
                final_url = urllib.parse.urljoin(relative_url, url_without_params)
            else:
                final_url = url_without_params
            # 甘肃日报 删去最后的数字，不可访问，没有办法呀
            # pattern = r'\.\d+$'
            # final_url = re.sub(pattern, '', final_url)
            cleaned_urls.append(final_url)

        return cleaned_urls


    @staticmethod
    def extract_tag_attr(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str, attr):
        """提取指定选择器的文本内容列表"""
        element = soup.select_one(selector)
        return element[attr] if element and attr in element.attrs else ""


    @staticmethod
    def extract_tag_attrs(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str, attr):
        if not soup:
            return []
        """提取指定选择器的文本内容列表"""
        elements = soup.select(selector)
        return [element[attr] for element in elements if attr in element.attrs]

    @staticmethod
    def insert_dict(Dict: dict[str, str]) -> str:
        """将字典转换为JSON字符串"""
        for k, v in Dict.items():
            if isinstance(v, list):
                Dict[k] = ",".join([str(item) for item in v])  # 转义列表中的每个字符串
        return ",".join([f'"{k}","{v}"' for k, v in Dict.items()])
    @staticmethod
    # 返回字典 标签a的 文本 与url  eg:｛text:url｝ 作者信息常用
    def extract_text_url(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str) -> dict[str, str]:
        """提取标签a的文本和URL"""
        element = soup.select_one(selector)
        return {element.get_text(separator=" ", strip=True): element["href"] if element.get_text(separator=" ",
                                                                                                 strip=True) and "href" in element.attrs else ""}

    @staticmethod
    # 返回字典 标签a的 文本 与url  eg:｛text:url｝ 标签常用分类
    def extract_text_urls(soup: bs4.BeautifulSoup | bs4.element.Tag, selector: str) -> dict[str, str]:
        """提取标签a的文本和URL"""
        elements = soup.select(selector)
        return {el.get_text(separator=" ", strip=True): el["href"] for el in elements if
                el.get_text(separator=" ", strip=True) and "href" in el.attrs}

    # 提取正文，主要是提取 p li tr 等会换行的元素的text,进行拼接
    @staticmethod
    def extract_content(soup: bs4.BeautifulSoup | bs4.element.Tag) -> str:
        if not soup:
            print("extract_content 方法，参数错误 soup is empty")
            return ""
        soup_copy = deepcopy(soup)
        for br_tag in soup_copy.find_all('br'):
            # .replace_with() 是一个强大的方法，可以用一个字符串或另一个标签
            # 来替换掉当前标签。
            br_tag.replace_with('\n')
        """提取正文内容 加上div 子标签之后，要足够细"""
        all_my_tags = ["p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "button","div"]
        elements = soup_copy.find_all(all_my_tags)  # 选择段落、列表项和表格行
        content = []
        if len(elements) == 0:
            txt = soup_copy.get_text()
        else:
            for el in elements:
                if el.name == "div":
                    if len(el.find_all(all_my_tags)) <= 1: #不含子标签的div
                        content.append(el.get_text() + "\n")  #get_text(strip=True) 内部所有的、独立的“文本块”。对每一个“文本块”进行处理。
                        el.decompose()
                    else: # div 下面标签很多，不处理div，处理下面的标签
                        pass
                else:
                    content.append(el.get_text(strip = False) + "\n")
                    el.decompose()  # 用完就删掉，这样就不怕嵌套了子节点问题，避免内容重复 li 下面有p两个都会被拿到
            txt = '\n'.join(content)
        txt = re.sub(r"[^\S\n]", "  ", txt)
        # 多个\n 连续，合并为一个
        # 使用 ' +' 模式，只匹配空格
        txt = re.sub(' +', ' ', txt)
        txt = re.sub(r"\n+", "\n", txt).strip()
        return txt

    # 将标签列表定义为常量，使用集合(set)可以稍微提高查找效率

    def _recursive_extract(self,element: bs4.element.Tag) -> str:
        """
        递归辅助函数，用于从一个元素中提取文本。
        """
        significant_tags = {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "button", "div"}
        # 检查当前元素内部是否包含任何其他“重要”标签。
        # element.find(significant_tags) 会查找所有后代节点。
        # 我们需要确保找到的不是元素自身。
        # 一个更稳健的检查是看它的子节点中有没有重要标签。
        has_significant_children = False
        for child in element.children:
            if isinstance(child, bs4.element.Tag) and child.find(significant_tags):
                has_significant_children = True
                break
        # 上面的检查有点复杂，我们可以简化逻辑：
        # 如果一个元素是重要标签，并且它内部不再包含其他重要标签，我们就提取它的文本。
        # element.find_all(significant_tags) 会把自己也算进去，所以列表长度<=1就意味着没有其他重要子标签了。

        # 基础情况（Base Case）: 当前元素是一个“叶子节点”
        # 即它本身是一个重要标签，但它内部不再包含其他重要标签。
        if element.name in significant_tags and len(element.find_all(list(significant_tags))) <= 1:
            # 使用 get_text(strip=True) 可以更好地清理文本块内部的空白
            return element.get_text(strip=True) + "\n"

        # 递归步骤（Recursive Step）: 当前元素是一个“容器”
        # 遍历它的子元素，并对它们进行递归调用
        else:
            content_parts = []
            for child in element.children:
                if isinstance(child, bs4.element.Tag):
                    content_parts.append(self._recursive_extract(child))
            return "".join(content_parts)

    def extract_content_recursively(self,soup: bs4.BeautifulSoup | bs4.element.Tag) -> str:
        """
        重构后的主函数，使用递归方式提取内容。
        """
        if not soup:
            print("extract_content 方法，参数错误 soup is empty")
            return ""
        soup_copy = deepcopy(soup)
        # 预处理 <br> 标签
        for br_tag in soup_copy.find_all('br'):
            br_tag.replace_with('\n')
        # 从根节点开始递归
        txt = self._recursive_extract(soup_copy)
        # 后处理，清理空白和多余的换行符 (与您原来的代码相同)
        txt = re.sub(r"[^\S\n]", " ", txt)  # 将非换行符的空白（如多个空格）替换为单个空格
        txt = re.sub(r"\n+", "\n", txt).strip()
        return txt