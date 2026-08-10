# Example Projects

The `examples/` directory contains 9 spider examples curated from real-world projects, showcasing how the jimmySpider framework is used in different scenarios.

## Example List

### 1. eastmoney_report — East Money Research Reports

**Files**: `spider.py`  
**Features demonstrated**:
- `SingleRequestHandler` GET/POST requests
- Multi-category pagination
- Redis checkpoint/resume (page cache + error page set)
- Report file downloads

**Best for**: financial data, research report collection, paginated APIs

### 2. state_council_policy — State Council Policies

**Files**: `spider.py`  
**Features demonstrated**:
- Two-stage list+detail crawl pattern
- ThreadPoolExecutor concurrent detail-page fetching
- BeautifulSoup HTML field parsing
- `HandleDatetime` date normalization

**Best for**: government sites, policies and regulations, news lists

### 3. moj_regulations — Ministry of Justice Regulations

**Files**: `spider.py`  
**Features demonstrated**:
- JSON POST requests (with signed headers)
- `safe_extract_json()` safe extraction of nested JSON
- `rename_keys_inplace()` field mapping
- Batched DB inserts + checkpoint page numbers

**Best for**: API collection, JSON data parsing

### 4. medlive_guide — Medical Guidelines

**Files**: `spider.py`, `spider_type.py`  
**Features demonstrated**:
- Multi-level category tree traversal
- Serialized category checkpoint cache (JSON)
- Two modes: full crawl of a single category vs. traversal of all categories
- Anti-bot on medical sites (dynamic cookies)

**Best for**: sites with category tree structures, medical/academic content

### 5. cicc_report — Broker Research Reports

**Files**: `spider.py`  
**Features demonstrated**:
- `CurlRequestHandler` TLS fingerprint impersonation (chrome120)
- Jiasule CDN cookie challenge solving
- JS crypto context integration (`_load_cookie_js_ctx`)
- Report PDF download + JSON metadata extraction

**Best for**: Cloudflare/Jiasule-protected sites, scenarios needing TLS fingerprint impersonation

### 6. unicamp_br — Brazilian University Repository

**Files**: `spider_list_by_area.py`, `spider_list_by_year.py`, `spider_supplement_by_area.py`  
**Features demonstrated**:
- Multi-strategy parallel collection (by subject / by year / by supplementary subject)
- Hierarchical JSON data loading
- University repository paper metadata collection
- Progress persistence + error retry

**Best for**: university repositories, academic papers, multi-dimensional collection

### 7. cuni_cz — Charles University

**Files**: `spider_list.py`, `spider_detail.py`  
**Features demonstrated**:
- Complete list+detail pattern
- `ThreadRequestHandler` multithreaded requests
- Faculty list driven by JSON data
- MongoDB batch upsert + deduplication

**Best for**: university faculty/paper databases, cross-faculty collection

### 8. escholarship_org — Academic Paper Repository

**Files**: `spider_list.py`, `spider_detail.py`  
**Features demonstrated**:
- `CurlCffiAsyncRequestHandler` curl_cffi async
- AWS WAF token handling
- Paper metadata + attachment downloads
- DOI deduplication + checkpoint recovery

**Best for**: AWS WAF-protected sites, high-concurrency academic data collection

### 9. pubmed_ncbi — PubMed

**Files**: `spider_list.py`, `spider_detail.py`, `spider_50_to_jsonl.py`, `springer_down_txt_xm.py`  
**Features demonstrated**:
- Date-range splitting strategy (monthly shards to bypass API limits)
- ThreadPoolExecutor multithreading (10 workers)
- Multi-stage pipeline (list → detail → JSONL → PDF download)
- PubMed API + Springer full-text downloads

**Best for**: large academic databases, API sharding strategies, multi-stage data pipelines

## Running the Examples

```bash
# Install the framework
pip install jimmyspider

# Make sure MongoDB and Redis are running locally

# Run an example (state_council_policy in this case)
cd examples/state_council_policy
python spider.py
```

## Picking an Example by Scenario

| What you want to do | Which example to look at |
|---------------------|--------------------------|
| Simple site, quick start | `eastmoney_report` |
| Government site, paginated list | `state_council_policy` |
| API JSON collection | `moj_regulations` |
| Site with category tree structure | `medlive_guide` |
| Cloudflare / TLS detection | `cicc_report` |
| University papers, multi-dimensional | `unicamp_br` |
| University data, list+detail | `cuni_cz` |
| AWS WAF / high concurrency | `escholarship_org` |
| Large database, sharding strategy | `pubmed_ncbi` |

## Picking an Example by Request Handler

| Handler | Examples |
|---------|----------|
| `SingleRequestHandler` | eastmoney_report, state_council_policy, moj_regulations |
| `AsyncRequestHandler` | — |
| `ThreadRequestHandler` | cuni_cz, pubmed_ncbi |
| `CurlRequestHandler` | cicc_report |
| `CurlCffiThreadRequestHandler` | — |
| `CurlCffiAsyncRequestHandler` | escholarship_org |

## Writing Your Own Spider

1. Start from the example closest to your scenario
2. Copy the example into a new directory
3. Modify the URLs, parsing logic, and field mappings
4. Run and test
