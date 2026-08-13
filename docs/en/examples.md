# Example Projects

The `examples/` directory contains 28 spider examples curated from real-world projects, showcasing how the jimmySpider framework is used in different scenarios.

## Example List

| # | Example | Site / Data source | DB | Key features |
|---|---------|--------------------|-----|--------------|
| 1 | `hello_world` | Hacker News | MongoDB | Minimal complete spider: `SingleRequestHandler` + `extractSoup` + `save_result` |
| 2 | `hello_mysql` | Hacker News | MySQL | Same logic as hello_world, `db_type="mysql"` switches backend |
| 3 | `hello_postgresql` | Hacker News | PostgreSQL | Same logic as hello_world, `db_type="postgresql"` switches backend |
| 4 | `eastmoney_report` | East Money research reports | MongoDB | GET/POST multi-category pagination, Redis checkpoint, report downloads |
| 5 | `state_council_policy` | State Council policies | MongoDB | Two-stage list+detail, ThreadPoolExecutor concurrency, `HandleDatetime` |
| 6 | `moj_regulations` | Ministry of Justice regulations | MongoDB | JSON POST with signed headers, `safe_extract_json`, field mapping |
| 7 | `medlive_guide` | Medlive medical guidelines | MongoDB | Multi-level category tree, serialized category checkpoints, dynamic cookies |
| 8 | `cicc_report` | CICC research reports | MongoDB | Jiasule CDN cookie challenge via JS reverse (execjs), PDF downloads |
| 9 | `unicamp_br` | University of Campinas repository | MongoDB | Multi-strategy collection (area/year/supplement) |
| 10 | `cuni_cz` | Charles University | MongoDB | list+detail, `ThreadRequestHandler`, batch upsert + dedup |
| 11 | `escholarship_org` | eScholarship papers | MongoDB | `CurlCffiAsyncRequestHandler`, AWS WAF token, DOI dedup |
| 12 | `pubmed_ncbi` | PubMed | MongoDB | Monthly date sharding, multi-stage pipeline (list → detail → JSONL → PDF) |
| 13 | `oatd` | OATD theses | MongoDB | `AsyncRequestHandler` concurrency, Cloudflare Turnstile CDP bypass, cookie refresh |
| 14 | `twse_taiwan` | Taiwan TWSE MOPS | MongoDB | Financial announcements, slicing by ROC year × market, calendar conversion |
| 15 | `chinamoney` | China Money Network | MongoDB | Credit ratings, POST pagination (year × page), PDF link construction |
| 16 | `naver_research` | Naver research reports (Korean) | MongoDB | Korean page parsing, custom parser routing, Korean date normalization |
| 17 | `yaozh_pharma` | Yaozh pharma database | MongoDB | Login-session site, pagination from HTML data attributes, checkpoint |
| 18 | `medsci` | Medsci medical guidelines | MongoDB | Category-API-driven, captcha-aware rate limiting, category JSON checkpoints |
| 19 | `gspublishing` | Goldman Sachs reports | MongoDB | Complex JSON POST search, ms-timestamp formatting, raw_data retention |
| 20 | `boc_fimarkets` | Bank of China financial markets | MongoDB | list+detail, encoding detection (GBK/UTF-8), attachment-per-record |
| 21 | `arxiv_org` | arXiv | PostgreSQL | `CurlRequestHandler` TLS impersonation + `ThreadRequestHandler`, 10-day window slicing, PG upsert |
| 22 | `papercopilot` | Paper Copilot conference papers | PostgreSQL | AJAX batch endpoint reverse-engineering, header-aligned parsing, cross-example reuse |
| 23 | `google_scholar` | Google Scholar | PostgreSQL | 4-file pipeline: title search → author profiles → collaborator expansion |
| 24 | `tech_news_flash` | Tech news flash (citreport.com) | MySQL | GBK pages, article-ID-driven crawl, MySQL + Redis checkpoint |
| 25 | `tiantian_fund` | Tiantian Fund (fund.eastmoney.com) | MySQL | Ranking pagination + NAV curve JS parsing, dual-table MySQL + CSV fallback |
| 26 | `tech_literature` | Frontiers tech literature | MySQL | Journals → search API → detail concurrency, structured parsing, journal JSON cache |
| 27 | `robot_lab` | UC Berkeley BAIR blog | MySQL | Blog pagination + detail concurrency, body/author/keyword extraction |
| 28 | `clash_proxy_pool` | Any site via Clash proxy pool | MongoDB | `ClashManager` health checks, auto node switching on download cap/403, docker-compose |

## Running the Examples

```bash
# Install the framework
pip install -e .

# Make sure MongoDB and Redis are running locally
# (MySQL / PostgreSQL examples also need their database running)

# Run an example (hello_world in this case)
cd examples/hello_world
python spider.py
```

## Picking an Example by Scenario

| What you want to do | Which example to look at |
|---------------------|--------------------------|
| Quick start with the framework | `hello_world` |
| Compare database backends (MySQL / PostgreSQL) | `hello_mysql`, `hello_postgresql` |
| Simple site, paginated list | `eastmoney_report` |
| Government site, paginated list | `state_council_policy` |
| API JSON collection | `moj_regulations` |
| Site with category tree structure | `medlive_guide` |
| Jiasule CDN / JS reverse | `cicc_report` |
| University papers, multi-dimensional | `unicamp_br` |
| University data, list+detail | `cuni_cz` |
| AWS WAF / high concurrency | `escholarship_org` |
| Large database, sharding strategy | `pubmed_ncbi` |
| Cloudflare + dynamic cookies | `oatd` |
| Financial data (announcements/ratings/reports) | `twse_taiwan`, `chinamoney`, `gspublishing`, `boc_fimarkets`, `tiantian_fund` |
| International sites (Korean pages) | `naver_research` |
| Medical/pharma databases | `yaozh_pharma`, `medsci` |
| Academic preprints | `arxiv_org` |
| Scholar networks / cross-example pipeline | `papercopilot`, `google_scholar` |
| News sites | `tech_news_flash` |
| Journal literature | `tech_literature` |
| Lab blogs | `robot_lab` |
| Proxy pool / node switching | `clash_proxy_pool` |

## Picking an Example by Request Handler

| Handler | Examples |
|---------|----------|
| `SingleRequestHandler` | hello_world, hello_mysql, hello_postgresql, eastmoney_report, state_council_policy, moj_regulations, medlive_guide, cicc_report, unicamp_br, twse_taiwan, chinamoney, naver_research, yaozh_pharma, medsci, gspublishing, boc_fimarkets, papercopilot, tech_news_flash, tech_literature, robot_lab, clash_proxy_pool |
| `AsyncRequestHandler` | oatd |
| `ThreadRequestHandler` | cuni_cz, pubmed_ncbi, arxiv_org |
| `CurlRequestHandler` | oatd (cookie_flush scenario), arxiv_org, google_scholar |
| `CurlCffiThreadRequestHandler` | — |
| `CurlCffiAsyncRequestHandler` | escholarship_org |

## Picking an Example by Database Backend

| Database | Examples |
|----------|----------|
| MongoDB (default) | hello_world, eastmoney_report, state_council_policy, moj_regulations, medlive_guide, cicc_report, unicamp_br, cuni_cz, escholarship_org, pubmed_ncbi, oatd, twse_taiwan, chinamoney, naver_research, yaozh_pharma, medsci, gspublishing, boc_fimarkets, clash_proxy_pool |
| MySQL | hello_mysql, tech_news_flash, tiantian_fund, tech_literature, robot_lab |
| PostgreSQL | hello_postgresql, arxiv_org, papercopilot, google_scholar |

> Switch backends via the `db_type` constructor argument or `db_type` in `jimmyspider.yaml` (default: mongodb). Table schemas are created automatically by the corresponding handler.

## Writing Your Own Spider

1. Start from the example closest to your scenario
2. Copy the example into a new directory
3. Modify the URLs, parsing logic, and field mappings
4. Run and test
