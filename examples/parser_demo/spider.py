"""
parser_demo —— jimmyspider.parser 智能解析演示（完全离线，不联网）

演示三个提取器在 4 种页面结构上的行为：
  - TitleExtractor    中文标题专项提取器（h1 → 语义 class → meta/JSON-LD → DOM → LLM）
  - ContentExtractor  正文提取器（语义选择器 → 文本密度 → 递归 → body 兜底）
  - SelectorCascade   五层级联引擎，本次演示覆盖全部 5 层：
        Level 0  已知定位器（known_selectors，来自缓存/配置文件）→ selector
        Level 1  语义选择器（h1 / .article-title / .price 等）    → semantic
        Level 2  JSON-LD / OpenGraph / Meta                       → meta
        Level 3  DOM 结构分析（文本密度/阅读器算法）               → dom
        Level 4  LLM 语义兜底（此处用 mock_llm_call 占位）         → llm

每个字段输出 ExtractionResult（value / method / confidence / selector_used / latency_ms）。
"""

import sys

# Windows GBK 控制台无法打印中文/emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from jimmyspider.parser import ContentExtractor, SelectorCascade, TitleExtractor

# ======================================================================
# 4 种页面结构样本（mock HTML，离线演示）
# ======================================================================

SAMPLE_NEWS = {
    "name": "A. 新闻文章（日报站）",
    "url": "https://daily.example.com/news/20260815/1",
    "html": """<html><head>
<title>城市更新三年行动方案发布 - 日报网</title>
<meta property="og:title" content="城市更新三年行动方案发布"/>
<meta property="article:published_time" content="2026-08-15T09:30:00+08:00"/>
<script type="application/ld+json">{"@type":"NewsArticle","headline":"城市更新三年行动方案发布","datePublished":"2026-08-15"}</script>
</head><body>
<header><nav>首页 时政 经济 国际 评论</nav></header>
<article class="article-content">
  <h1>城市更新三年行动方案发布</h1>
  <div class="article-meta"><span class="author">记者 王小江</span><span class="date">2026-08-15</span></div>
  <p>本报记者今日从市住建委获悉，《城市更新三年行动方案（2026—2028）》正式发布。
     方案提出，未来三年将完成老旧小区改造 1200 个，涉及居民约 45 万户，
     同步推进城市地下管网更新、片区综合开发与历史街区活化利用等八大行动。</p>
  <p>市住建委相关负责人表示，城市更新将坚持「留改拆」并举，优先补齐市政基础设施短板，
     完善社区养老、托育、停车等公共服务配套，切实提升居民获得感与幸福感。</p>
  <p>据悉，首批 200 个改造项目已进入招投标阶段，计划于今年 10 月陆续开工。</p>
</article>
<footer>版权所有 © 2026 日报网</footer>
</body></html>""",
}

SAMPLE_BLOG = {
    "name": "B. 博客文章（标题只存在于 JSON-LD）",
    "url": "https://blog.example.com/posts/understanding-cascade",
    "html": """<html><head>
<title>理解选择器级联引擎 - 某某博客</title>
<script type="application/ld+json">{"@type":"BlogPosting","headline":"理解选择器级联引擎","author":{"name":"张三"},"datePublished":"2026-07-20"}</script>
</head><body>
<div class="post">
  <div class="post-body">
    <p>选择器级联引擎按照成本从低到高依次尝试多种提取策略，一旦某个策略的置信度
       达到阈值就立即返回，从而在准确率与成本之间取得平衡。本文从工程角度拆解其设计。</p>
    <p>第一层是已知定位器。对于已经验证过的站点，直接复用缓存的 CSS 选择器，
       零 token 成本且置信度为 1.0，这是整个系统最廉价的路径。</p>
    <p>第二层是语义选择器。针对新闻类页面，h1 的命中率高达 99.3%，
       因此标题提取器把 h1 排在策略链的最前面，其次才是各类语义化 class。</p>
    <p>第三层是结构化数据。SEO 完备的页面会提供 JSON-LD、OpenGraph 等元数据，
       解析这些标记通常只需要几毫秒，且准确率稳定在 90% 以上。</p>
    <p>当上述所有非 LLM 手段都失败时，才把页面交给大模型兜底，
       此时每个字段会消耗约 500 个 token，因此应当尽量少用。</p>
  </div>
</div>
</body></html>""",
}

SAMPLE_PRODUCT = {
    "name": "C. 商品详情页（og:title + 语义 class + LLM 兜底）",
    "url": "https://shop.example.com/product/x1-pro",
    "html": """<html><head>
<meta property="og:title" content="智能扫地机器人 X1 Pro"/>
<meta property="og:description" content="旗舰款扫地机器人，激光导航，自动集尘"/>
</head><body>
<div class="product-wrap">
  <div class="product-name">智能扫地机器人 X1 Pro</div>
  <div class="gallery"><img src="/img/x1-pro.jpg" alt="X1 Pro 主图"/></div>
  <div class="info">
    <span class="price">¥2999.00</span>
    <span class="item-no">SKU-X1-PRO-2026</span>
  </div>
  <div class="detail-desc">
    <p>X1 Pro 采用激光雷达导航，建图速度提升 40%；吸力 6000Pa，
       支持地毯增压与自动集尘，尘袋容量 3.2L 可 75 天免维护。</p>
    <p>机身厚度仅 9.2cm，可进入大部分家具底部；支持 App 远程控制、
       区域清扫与虚拟墙设置，并兼容主流智能音箱语音控制。</p>
  </div>
</div>
</body></html>""",
}

SAMPLE_BARE = {
    "name": "D. 裸结构页面（无 class、无 meta —— 考验 DOM 分析与 LLM 兜底）",
    "url": "https://legacy.example.com/page/8848",
    "html": """<html><body>
<div>
  <p>这是一篇完全没有 class 与 id 标注的旧版页面，也没有任何 meta 标签。
     此类页面在信息系统中大量存在，是阅读器类算法的主要应用场景。</p>
  <p>文本密度算法的核心假设是：正文所在的容器通常文本量大、链接占比低，
     而导航、广告区域则恰恰相反。通过对每个容器计算文本密度得分，
     可以可靠地定位正文主体，即使页面没有任何语义标注。</p>
  <p>经过脚本与样式剥离、噪音容器过滤、语义词加权三步处理后，
     得分最高的容器即被视为正文，该方法对中文与英文页面均有较好效果。</p>
</div>
</body></html>""",
}

SAMPLES = [SAMPLE_NEWS, SAMPLE_BLOG, SAMPLE_PRODUCT, SAMPLE_BARE]

# ======================================================================
# mock LLM：真实项目接入大模型（如 OpenAI/DeepSeek），此处返回占位值
# ======================================================================

def mock_llm_call(html: str, field_name: str, field_def: dict) -> str:
    """模拟 LLM 兜底调用：返回确定性占位值（不真正消耗 token）"""
    placeholders = {
        "title": "《未知名文章：正文首句提炼的标题》",
        "sku": "SKU-X1-PRO-2026-旗舰款",
    }
    return placeholders.get(
        field_name,
        f"【LLM 占位】{field_name} = 未在页面中定位到对应内容",
    )

# ======================================================================
# 打印辅助
# ======================================================================

def print_result(r, indent="    "):
    flag = "OK" if r.is_valid else "--"
    print(f"{indent}[{flag}] {r.field_name:<12} value={str(r.value)[:42]!r}")
    print(f"{indent}     method={r.method:<9} confidence={r.confidence:.2f} "
          f"selector={r.selector_used!r} latency={r.latency_ms:.1f}ms")

# ======================================================================
# 演示 1：TitleExtractor + ContentExtractor 专项提取
# ======================================================================

def demo_extractors():
    print("=" * 70)
    print("演示 1：TitleExtractor（中文标题） + ContentExtractor（正文）")
    print("=" * 70)
    title_extractor = TitleExtractor()
    content_extractor = ContentExtractor()

    for s in SAMPLES:
        print(f"\n--- {s['name']}  ({s['url']}) ---")
        t = title_extractor.extract(s["html"], url=s["url"])
        print_result(t)
        c = content_extractor.extract(s["html"], url=s["url"])
        print_result(c)

# ======================================================================
# 演示 2：SelectorCascade 五层级联
# ======================================================================

def demo_cascade():
    print("\n" + "=" * 70)
    print("演示 2：SelectorCascade 五层级联（含 mock LLM 兜底）")
    print("=" * 70)

    # 常见字段 schema
    # 注意字段顺序：content 的 DOM 密度分析会就地剥离 script 等标签，
    # 因此依赖 JSON-LD 的字段（publish_date 等）要排在 content 之前。
    schema_common = {"fields": [
        {"name": "title", "type": "text"},
        {"name": "publish_date", "type": "text"},
        {"name": "content", "type": "text"},
    ]}
    schema_product = {"fields": [
        {"name": "title", "type": "text"},
        {"name": "price", "type": "text"},
        {"name": "sku", "type": "text"},
        {"name": "content", "type": "text"},
    ]}

    runs = [
        # ---- Level 0：已知定位器（首次抓取后缓存复用） ----
        ("Level 0 演示：传入 known_selectors，直接命中缓存定位器",
         SAMPLE_NEWS, schema_common,
         SelectorCascade(known_selectors={"title": "h1", "content": ".article-content"})),
        # ---- Level 1：语义选择器 ----
        ("Level 1 演示：无已知定位器，语义选择器命中 h1 / .article-content",
         SAMPLE_NEWS, schema_common, SelectorCascade()),
        # ---- Level 2：JSON-LD / Meta ----
        ("Level 2 演示：标题只在 JSON-LD 中（无 h1 无标题 class）",
         SAMPLE_BLOG, schema_common, SelectorCascade()),
        # ---- 商品页：title→meta(L2) / price→semantic(L1) / sku→llm(L4) ----
        ("Level 4 演示：商品页 —— sku 字段所有非 LLM 手段均失败，LLM 兜底",
         SAMPLE_PRODUCT, schema_product, SelectorCascade()),
        # ---- Level 3：DOM 密度分析 ----
        ("Level 3 演示：裸结构页 —— 正文由文本密度算法定位",
         SAMPLE_BARE, schema_common, SelectorCascade()),
    ]

    for title, s, schema, cascade in runs:
        print(f"\n--- {title} ---")
        result = cascade.extract(s["html"], url=s["url"], schema=schema,
                                 llm_call=mock_llm_call)
        print(result.summary())
        print(f"    整页耗时={result.total_latency_ms:.1f}ms | "
              f"提取器={result.extractors_used} | 预估tokens={result.tokens_used}")

    # ---- 可复用的定位器输出 ----
    print("\n--- 输出可复用定位器（下次抓取直接作为 known_selectors） ---")
    cascade = SelectorCascade()
    result = cascade.extract(SAMPLE_NEWS["html"], url=SAMPLE_NEWS["url"],
                             schema=schema_common, llm_call=mock_llm_call)
    print("    selectors =", result.selectors)

# ======================================================================
# 演示 3：成本对比（LLM vs 级联）
# ======================================================================

def demo_cost():
    print("\n" + "=" * 70)
    print("演示 3：成本估算 —— 级联引擎 vs 全量 LLM 提取")
    print("=" * 70)

    # 级联：对 4 个样本各跑一次，统计总 token 消耗
    cascade = SelectorCascade()
    schemas = {
        SAMPLE_NEWS["url"]: {"fields": [{"name": "title", "type": "text"},
                                        {"name": "content", "type": "text"}]},
        SAMPLE_BLOG["url"]: {"fields": [{"name": "title", "type": "text"},
                                        {"name": "content", "type": "text"}]},
        SAMPLE_PRODUCT["url"]: {"fields": [{"name": "title", "type": "text"},
                                           {"name": "sku", "type": "text"},
                                           {"name": "content", "type": "text"}]},
        SAMPLE_BARE["url"]: {"fields": [{"name": "title", "type": "text"},
                                        {"name": "content", "type": "text"}]},
    }
    total_tokens = 0
    llm_fields = 0
    for s in SAMPLES:
        r = cascade.extract(s["html"], url=s["url"], schema=schemas[s["url"]],
                            llm_call=mock_llm_call)
        total_tokens += r.tokens_used
        llm_fields += sum(1 for f in r.fields.values() if f.method == "llm")

    # 全量 LLM：每个字段 ~500 tokens（估算，OpenAI 级别定价）
    field_count = sum(len(s["fields"]) for s in schemas.values())
    llm_tokens = field_count * 500

    print(f"    级联引擎: {field_count} 个字段 × 4 个页面 → 仅 {total_tokens} tokens "
          f"（{llm_fields} 个字段落到 Level 4 LLM 兜底）")
    print(f"    全量 LLM : {field_count} 个字段 × 4 个页面 → 约 {llm_tokens} tokens")
    print(f"    成本差距 : {(llm_tokens / max(total_tokens, 1)):.0f} 倍"
          f"（级联在绝大多数页面上零 token 成本）")

def main():
    demo_extractors()
    demo_cascade()
    demo_cost()


if __name__ == "__main__":
    main()
