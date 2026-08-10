# jimmySpider Examples

This directory contains 10 real-world spider projects that demonstrate the jimmySpider framework in action.

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

## How to Use These Examples

### 1. Pick the closest match

Find the example that most closely matches your target website type:

- **Financial/API data** → eastmoney_report, moj_regulations
- **Government/news sites** → state_council_policy
- **Academic/university** → unicamp_br, cuni_cz, escholarship_org, pubmed_ncbi
- **Medical/health** → medlive_guide
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
| `SingleRequestHandler` usage | eastmoney_report, state_council_policy, moj_regulations |
| `ThreadRequestHandler` usage | cuni_cz, pubmed_ncbi |
| `CurlRequestHandler` usage | cicc_report |
| `CurlCffiAsyncRequestHandler` usage | escholarship_org |
| `AsyncRequestHandler` usage | oatd |
| Cookie refresh via headless browser (Cloudflare) | oatd |
| Redis checkpoint/resume | eastmoney_report, state_council_policy |
| `Cache` for error retry sets | eastmoney_report, unicamp_br |
| `safe_extract_json` nested access | moj_regulations |
| `convert_date_robust` date parsing | state_council_policy |
| `rename_keys_inplace` field mapping | moj_regulations |
| `FileDownloader` file download | eastmoney_report, cicc_report |
| `generate_string_id` / `generate_doi_id` | pubmed_ncbi, escholarship_org |
| Date-range splitting strategy | pubmed_ncbi |
| Multi-thread detail concurrency | state_council_policy, pubmed_ncbi |

## Need Help?

- Read the [main documentation](../docs/)
- Check the [API Reference](../docs/api.md)
- Open an issue on GitHub
