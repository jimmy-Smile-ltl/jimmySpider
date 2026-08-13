# jimmySpider Examples

This directory contains 27 real-world spider projects that demonstrate the jimmySpider framework in action.

## Quick Navigation

| # | Example | What it demonstrates | Key feature |
|---|---------|---------------------|-------------|
| 1 | [eastmoney_report/](eastmoney_report/) | Financial report scraping | `SingleRequestHandler`, Redis checkpoint |
| 2 | [state_council_policy/](state_council_policy/) | Government policy portal | list+detail pagination, HTML parsing |
| 3 | [moj_regulations/](moj_regulations/) | Legal regulations API | JSON POST, field mapping |
| 4 | [medlive_guide/](medlive_guide/) | Medical guidelines | Category tree traversal |
| 5 | [cicc_report/](cicc_report/) | Broker research reports | `CurlRequestHandler`, TLS fingerprinting |
| 6 | [unicamp_br/](unicamp_br/) | University repository | Multi-strategy (area/year/supplement) |
| 7 | [cuni_cz/](cuni_cz/) | Charles University | `ThreadRequestHandler`, list+detail |
| 8 | [escholarship_org/](escholarship_org/) | Academic papers | `CurlCffiAsync`, AWS WAF |
| 9 | [pubmed_ncbi/](pubmed_ncbi/) | PubMed database | Date-range splitting, multi-threading |
| 10 | [oatd/](oatd/) | OATD theses (Cloudflare-protected) | `AsyncRequestHandler`, cookie refresh |
| 11 | [naver_research/](naver_research/) | Naver Finance research reports (Korean) | Custom parser module, category resume |
| 12 | [yaozh_pharma/](yaozh_pharma/) | Chinese pharma database (yaozh) | Logged-in session, pagination from HTML data attributes |
| 13 | [medsci/](medsci/) | Medical science guidelines | Category API + per-category pagination |
| 14 | [gspublishing/](gspublishing/) | Goldman Sachs research reports | POST JSON search API, timestamp formatting |
| 15 | [boc_fimarkets/](boc_fimarkets/) | Bank of China financial markets | list+detail, attachment-per-record, encoding detection |
| 16 | [hello_world/](hello_world/) | Hacker News | Minimal complete spider, `extractSoup` HTML parsing |
| 17 | [twse_taiwan/](twse_taiwan/) | Taiwan TWSE MOPS | Financial announcements, JSON POST slicing |
| 18 | [chinamoney/](chinamoney/) | China Money Network | Credit ratings, POST pagination, checkpoint |
| 19 | [arxiv_org/](arxiv_org/) | arXiv preprints | PostgreSQL bulk upsert, curl_cffi, 10-day window slicing |
| 20 | [papercopilot/](papercopilot/) | Conference papers | AJAX batch endpoint reverse-engineering, header-aligned parsing |
| 21 | [google_scholar/](google_scholar/) | Google Scholar authors | Search parsing, author profiles, cross-example pipeline |

## How to Use These Examples

### 1. Pick the closest match

Find the example that most closely matches your target website type:

- **Financial/API data** → eastmoney_report, moj_regulations, gspublishing, boc_fimarkets, twse_taiwan, chinamoney
- **Government/news sites** → state_council_policy, boc_fimarkets
- **Academic/university** → unicamp_br, cuni_cz, escholarship_org, pubmed_ncbi, naver_research, arxiv_org, papercopilot, google_scholar
- **Medical/health** → medlive_guide, medsci, yaozh_pharma
- **International/non-Chinese sites** → naver_research, gspublishing, unicamp_br, cuni_cz, escholarship_org
- **Cloudflare-protected** → cicc_report, escholarship_org

### 2. Read the module docstring

Every `.py` file starts with a docstring explaining what the example demonstrates and what framework features it uses.

### 3. Run it (optional)

```bash
cd examples/state_council_policy
python spider.py
```

> Note: Some examples require live cookies or API keys to work. Their docstrings explain what to set up.

### 4. Adapt to your needs

Copy the example, modify the URL parsing logic and field mapping to match your target site.

## Pattern Reference

### All examples share this pattern:

```python
from pathlib import Path
from jimmyspider import JimmySpider, Cache, generate_string_id

class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set up Redis caches for checkpoint/resume

    def run(self):
        # Check if already finished
        # Resume from last checkpoint
        # Fetch and parse data
        # self.save_result(data)

if __name__ == "__main__":
    Spider(pro_path=Path(__file__).parent).run()
```

### list+detail pattern:

```
spider_list.py    →  collects list page URLs
spider_detail.py  →  fetches and parses each detail page
```

### Multi-strategy pattern:

```
spider_by_area.py    →  scrape by subject area
spider_by_year.py    →  scrape by year range
spider_supplement.py →  fill in missing items
```

## Seeing Framework Features in Action

| If you want to learn about... | Look at... |
|------------------------------|------------|
| `SingleRequestHandler` usage | eastmoney_report, state_council_policy, moj_regulations, naver_research, yaozh_pharma, medsci, gspublishing, boc_fimarkets |
| `ThreadRequestHandler` usage | cuni_cz, pubmed_ncbi |
| `CurlRequestHandler` usage | cicc_report, arxiv_org, google_scholar |
| `PostgreSQLHandler` batch upsert | arxiv_org, papercopilot, google_scholar |
| AJAX batch endpoint reverse-engineering | papercopilot |
| Cross-example pipeline (sys.path reuse) | papercopilot, google_scholar |
| `CurlCffiAsyncRequestHandler` usage | escholarship_org |
| `AsyncRequestHandler` usage | oatd |
| Cookie refresh via headless browser (Cloudflare) | oatd |
| Redis checkpoint/resume | eastmoney_report, state_council_policy |
| `Cache` for error retry sets | eastmoney_report, unicamp_br |
| `safe_extract_json` nested access | moj_regulations |
| `convert_date_robust` date parsing | state_council_policy, boc_fimarkets |
| `extractSoup` HTML parsing | hello_world, boc_fimarkets |
| Custom parser module (per-site layout) | naver_research |
| `rename_keys_inplace` field mapping | moj_regulations |
| `FileDownloader` file download | eastmoney_report, cicc_report |
| `generate_string_id` / `generate_doi_id` | pubmed_ncbi, escholarship_org |
| Date-range splitting strategy | pubmed_ncbi |
| Multi-thread detail concurrency | state_council_policy, pubmed_ncbi |

## Need Help?

- Read the [main documentation](../docs/)
- Check the [API Reference](../docs/api.md)
- Open an issue on GitHub
