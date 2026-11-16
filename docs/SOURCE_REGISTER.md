# Source Register

Every external source used by the platform, how it is accessed, and its
access status when last probed. Probe results are also appended to
`data/processed/registry/source_access_log.csv` each time an adapter runs.

Source priority (master requirement 3.5): 1 NSE/BSE BRSR XBRL filing ->
2 official structured filing data -> 3 company annual / BRSR report ->
4 sustainability report -> 5 MCA (identity / governance validation).

## NSE (primary)

| Source | URL / pattern | Access method | Status (2026-11-11) |
|---|---|---|---|
| BRSR filing index API | `https://www.nseindia.com/api/corporate-bussiness-sustainabilitiy?index=equities[&symbol=..][&from_date=DD-MM-YYYY&to_date=..]` | shared HTTP client; session warm-up on the BRSR listing page (cookies), rate-limited 2.5 s | Accessible. Returns companyName, symbol, fyFrom, fyTo, submissionDate, revisionDate, xbrlFile, attachmentFile. No ISIN/CIN. |
| BRSR XBRL files | `https://nsearchives.nseindia.com/corporate/xbrl/<file>.xml` | atomic download, XML validation, SHA-256; invalid control characters sanitised (original kept) | Accessible. 1 broken link (M&M FY23-24, 404) and 1 malformed file (Torrent Pharma FY21-22). |
| BRSR RSS feed | `https://nsearchives.nseindia.com/content/RSS/brsr.xml` | shared client | Accessible; latest ~20 filings only. |
| Symbol / name change files | `https://nsearchives.nseindia.com/content/equities/symbolchange.csv`, `namechange.csv` | shared client, dated snapshot in `data/raw/reference/nse/<date>/` | Accessible. |
| Equity master | `NSE_EQUITY_L.csv` (local copy in `data/raw/reference/`) | file | Local. |
| BRSR taxonomy (current) | `https://nsearchives.nseindia.com/web/mediaattachment/2026-03/Taxonomy_BRSR_20260330194931.zip` | shared client; local ZIP reused unless `--refresh`; fallback to local ZIP on failure | GET accessible; HEAD returns 503 (CDN). SEBI namespace URLs (`https://www.sebi.gov.in/xbrl/...`) are identifiers, not download locations (404). |
| BRSR taxonomy (older versions) | `https://nsearchives.nseindia.com//web/mediaattachment/2026-07/Taxonomy_Archives_20260731164437.zip` (`BRSR.zip` inside) | downloaded once; versions 2021-09-30, 2023-06-30, 2024-04-30, 2025-05-31 | Local copies in `data/raw/sebi_brsr_taxonomy/versions/`. Every local file is checked by SHA-256 against `BRSR.zip` inside the archive (`data/processed/taxonomy/taxonomy_files.csv`). The download time was not recorded, so the registry reports the file modification time and says so. |
| XBRL information page | `https://www.nseindia.com/companies-listing/xbrl-information` | page links | Accessible. |

## BSE (secondary exchange)

| Source | URL / pattern | Access method | Status (2026-11-11) |
|---|---|---|---|
| BRSR list per company ("Get Quote > BRSR") | `https://api.bseindia.com/BseIndiaAPI/api/XbrlBrsrDetails/w?scripcode=<code>` (also `SHP_BRSR_ng/w?scrip_cd=`) | `app/scrapers/bse_scraper.py probe` - one request, stops at the first 403 | **Blocked**: HTTP 403 "Access Denied" (CDN bot protection) for every API endpoint, also after a browser-style session warm-up. Not bypassed. |
| BRSR page (manual) | `https://www.bseindia.com/stock-share-price/x/<symbol>/<scrip_code>/brsr/` | a person downloads the XBRL in a browser into `data/raw/bse_brsr_xml/manual/`; `bse_scraper import-manual` registers it | Manual path implemented and tested (identical / same-period / not-on-NSE cases). |
| Scrip master | `BSE_EQ_SCRIP_<date>.csv` (local copy) | file | Local; BSE code matched for 1,313 companies. |

Requests for manual BSE downloads (company-years missing on NSE):
`data/processed/registry/bse_manual_download_requests.csv`.

## MCA (identity validation)

| Source | URL / pattern | Access method | Status (2026-11-11) |
|---|---|---|---|
| MCA portal master data | `https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html` | `mca_scraper probe` | **Blocked**: HTTP 403 (CDN). Not bypassed; the portal also uses captcha / login for company search. |
| MCA company master via Open Government Data | `https://api.data.gov.in/resource/<resource_id>?api-key=..&format=json&filters[CIN]=..` | `mca_scraper validate` when `DATA_GOV_IN_API_KEY` and `BRSR_DATAGOV_MCA_RESOURCE_IDS` are set | **Requires a free API key** ("Authorization field missing" without one). Resource ids are shown on `https://www.data.gov.in/catalog/company-master-data` (page is rendered by JavaScript). Data is a periodic snapshot. |
| Bulk ROC-wise company master CSVs | data.gov.in downloads placed in `data/raw/reference/mca/` | `mca_scraper validate` (bulk path) | Supported; none placed yet. |
| CIN decoding | the CIN itself | `mca_scraper validate` | Always available: listing flag, NIC code, state / ROC, incorporation year, company type, placeholder detection. |

## Supplementary (company documents)

| Source | Access method | Status |
|---|---|---|
| Company investor-relations pages | `app/scrapers/report_discovery.py`, `pdf_link_scraper.py` | Implemented for PDF discovery; used only as supplementary evidence. |
| Annual / CSR / BRSR PDFs | `app/downloaders/pdf_downloader.py`, `app/extractors/pdf_extractor.py` | Implemented; Reliance documents present in `data/raw/Reliance*`. |
| NSE BRSR PDF attachments | `attachmentFile` URL from the NSE API (recorded in the filing registry as `pdf_url`) | URLs recorded; not downloaded (owner decision). |

## Derived outputs and their sources (Phase 15)

The deliverables in `data/outputs/` are built only from the processed outputs
above. Nothing is downloaded or recalculated. `data/outputs/manifest.json`
records the path and SHA-256 of every source file used for a build. The
grain and source of each file are described in
[DATA_DICTIONARY.md](DATA_DICTIONARY.md).

Limitations carried into the outputs:

- **Supplementary PDF candidates.** These come from the local Reliance
  documents only. They are labelled as unverified and never change XBRL
  values or coverage.
- **NSE BRSR PDFs.** The URLs are recorded (417) but the files are not
  downloaded, per the owner decision.
- **Missing filings.** Two in-scope XBRL filings could not be processed:
  - an NSE 404 for C-INE101A01026, 2023-24;
  - a malformed XML file for C-INE685A01028, 2021-22.

  The coverage report marks both as `EXTRACTION_ERROR`, with the evidence.
