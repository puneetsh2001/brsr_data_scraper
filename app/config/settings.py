"""
Central configuration for the BRSR data platform.

Every value has a safe default and can be overridden with an environment
variable (or a line in .env). Overrides use the prefix BRSR_, e.g.
BRSR_HTTP_TIMEOUT=60. Database credentials keep their existing names
(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD) and are never logged.

Usage:
    from app.config import settings
    settings.http_timeout
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:          # python-dotenv is in requirements; stay usable
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


def _env(name, default, cast=str):
    """
    Read BRSR_<name>. An invalid value never crashes the platform: the
    default is used and a warning is printed (logging is not configured
    yet at settings import time).
    """
    raw = os.getenv(f"BRSR_{name}")
    if raw is None or raw.strip() == "":
        return default
    raw = raw.strip()
    if cast is bool:
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        _warn(name, raw, default)
        return default
    if cast is tuple:
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        _warn(name, raw, default)
        return default
    if cast in (int, float) and value < 0:
        _warn(name, raw, default)
        return default
    return value


def _warn(name, raw, default):
    import sys
    print(f"WARNING: invalid BRSR_{name}={raw!r}; using default "
          f"{default!r}", file=sys.stderr)


def _path(name, default):
    value = _env(name, None)
    return Path(value) if value else default


@dataclass(frozen=True)
class Settings:
    # ---- paths -----------------------------------------------------------
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: _path(
        "DATA_DIR", PROJECT_ROOT / "data"))
    log_dir: Path = field(default_factory=lambda: _path(
        "LOG_DIR", PROJECT_ROOT / "logs"))
    cache_dir: Path = field(default_factory=lambda: _path(
        "CACHE_DIR", PROJECT_ROOT / "data" / "cache" / "http"))

    # ---- NSE sources -------------------------------------------------------
    nse_base_url: str = field(default_factory=lambda: _env(
        "NSE_BASE_URL", "https://www.nseindia.com"))
    nse_brsr_listing_page: str = field(default_factory=lambda: _env(
        "NSE_BRSR_LISTING_PAGE",
        "https://www.nseindia.com/companies-listing/"
        "corporate-filings-bussiness-sustainabilitiy-reports"))
    nse_brsr_api: str = field(default_factory=lambda: _env(
        "NSE_BRSR_API",
        "https://www.nseindia.com/api/corporate-bussiness-sustainabilitiy"))
    nse_rss_url: str = field(default_factory=lambda: _env(
        "NSE_RSS_URL", "https://nsearchives.nseindia.com/content/RSS/brsr.xml"))
    nse_symbol_change_url: str = field(default_factory=lambda: _env(
        "NSE_SYMBOL_CHANGE_URL",
        "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"))
    nse_name_change_url: str = field(default_factory=lambda: _env(
        "NSE_NAME_CHANGE_URL",
        "https://nsearchives.nseindia.com/content/equities/namechange.csv"))
    nse_xbrl_info_page: str = field(default_factory=lambda: _env(
        "NSE_XBRL_INFO_PAGE",
        "https://www.nseindia.com/companies-listing/xbrl-information"))
    brsr_taxonomy_url: str = field(default_factory=lambda: _env(
        "TAXONOMY_URL",
        "https://nsearchives.nseindia.com/web/mediaattachment/2026-03/"
        "Taxonomy_BRSR_20260330194931.zip"))
    # NSE archive of superseded taxonomies (BRSR.zip inside)
    brsr_taxonomy_archive_url: str = field(default_factory=lambda: _env(
        "TAXONOMY_ARCHIVE_URL",
        "https://nsearchives.nseindia.com//web/mediaattachment/2026-07/"
        "Taxonomy_Archives_20260731164437.zip"))
    taxonomy_dir: Path = field(default_factory=lambda: _path(
        "TAXONOMY_DIR", PROJECT_ROOT / "data" / "raw" / "sebi_brsr_taxonomy"))

    # ---- BSE sources -------------------------------------------------------
    bse_api_base: str = field(default_factory=lambda: _env(
        "BSE_API_BASE", "https://api.bseindia.com/BseIndiaAPI/api"))
    # per-company BRSR list used by the BSE "Get Quote > BRSR" tab
    bse_brsr_detail_path: str = field(default_factory=lambda: _env(
        "BSE_BRSR_DETAIL_PATH", "/XbrlBrsrDetails/w"))
    bse_brsr_page: str = field(default_factory=lambda: _env(
        "BSE_BRSR_PAGE",
        "https://www.bseindia.com/stock-share-price/x/{symbol}/{code}/brsr/"))
    bse_manual_dir: Path = field(default_factory=lambda: _path(
        "BSE_MANUAL_DIR",
        PROJECT_ROOT / "data" / "raw" / "bse_brsr_xml" / "manual"))

    # ---- MCA / data.gov.in -----------------------------------------------
    datagov_api_base: str = field(default_factory=lambda: _env(
        "DATAGOV_API_BASE", "https://api.data.gov.in/resource"))
    # resource id(s) of the "Company Master Data" datasets (comma list);
    # visible on https://www.data.gov.in/catalog/company-master-data
    datagov_mca_resource_ids: tuple = field(default_factory=lambda: _env(
        "DATAGOV_MCA_RESOURCE_IDS", (), tuple))
    datagov_cin_field: str = field(default_factory=lambda: _env(
        "DATAGOV_CIN_FIELD", "CIN"))
    mca_bulk_dir: Path = field(default_factory=lambda: _path(
        "MCA_BULK_DIR",
        PROJECT_ROOT / "data" / "raw" / "reference" / "mca"))

    @property
    def datagov_api_key(self):
        """Secret: read from DATA_GOV_IN_API_KEY, never logged."""
        return os.getenv("DATA_GOV_IN_API_KEY", "")

    # ---- HTTP behaviour ----------------------------------------------------
    http_user_agent: str = field(default_factory=lambda: _env(
        "HTTP_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"))
    http_timeout: float = field(default_factory=lambda: _env(
        "HTTP_TIMEOUT", 90.0, float))
    http_max_retries: int = field(default_factory=lambda: _env(
        "HTTP_MAX_RETRIES", 4, int))
    http_backoff_base: float = field(default_factory=lambda: _env(
        "HTTP_BACKOFF_BASE", 2.0, float))
    http_backoff_max: float = field(default_factory=lambda: _env(
        "HTTP_BACKOFF_MAX", 60.0, float))
    # minimum seconds between requests to the same host
    http_min_interval_default: float = field(default_factory=lambda: _env(
        "HTTP_MIN_INTERVAL_DEFAULT", 1.0, float))
    http_min_interval_nse_api: float = field(default_factory=lambda: _env(
        "HTTP_MIN_INTERVAL_NSE_API", 2.5, float))
    http_min_interval_nse_archive: float = field(default_factory=lambda: _env(
        "HTTP_MIN_INTERVAL_NSE_ARCHIVE", 1.0, float))
    http_cache_enabled: bool = field(default_factory=lambda: _env(
        "HTTP_CACHE_ENABLED", True, bool))
    http_cache_ttl_hours: float = field(default_factory=lambda: _env(
        "HTTP_CACHE_TTL_HOURS", 24.0, float))

    # ---- processing scope --------------------------------------------------
    company_universe_file: Path = field(default_factory=lambda: _path(
        "COMPANY_UNIVERSE_FILE",
        PROJECT_ROOT / "data" / "raw" / "nse_nifty100_constituents.csv"))
    company_filter: tuple = field(default_factory=lambda: _env(
        "COMPANY_FILTER", (), tuple))          # NSE symbols; empty = all
    first_fy_slot: int = field(default_factory=lambda: _env(
        "FIRST_FY_SLOT", 2021, int))           # FY2021-22 (voluntary year)
    discovery_years: int = field(default_factory=lambda: _env(
        "DISCOVERY_YEARS", 5, int))

    # ---- logging -----------------------------------------------------------
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # ---- analytics API (read-only) -----------------------------------------
    # database the analytics API reads (BRSR_ANALYTICS_DB_NAME); the
    # production database is refused until a production import is approved
    analytics_db_name: str = field(default_factory=lambda: _env(
        "ANALYTICS_DB_NAME", "brsr_phase14_test"))

    # ---- database (credentials from DB_* variables) ------------------------
    @property
    def db_configured(self):
        return all(os.getenv(k) for k in ("DB_HOST", "DB_PORT", "DB_NAME",
                                          "DB_USER"))

    @property
    def db_description(self):
        """Safe description for logs - never includes the password."""
        return (f"postgresql://{os.getenv('DB_USER', '?')}@"
                f"{os.getenv('DB_HOST', '?')}:{os.getenv('DB_PORT', '?')}/"
                f"{os.getenv('DB_NAME', '?')}")

    # ---- derived paths -----------------------------------------------------
    @property
    def raw_dir(self):
        return self.data_dir / "raw"

    @property
    def processed_dir(self):
        return self.data_dir / "processed"

    @property
    def reference_dir(self):
        return self.data_dir / "reference"

    def min_interval_for(self, host):
        host = (host or "").lower()
        if host == "www.nseindia.com":
            return self.http_min_interval_nse_api
        if host.endswith("nsearchives.nseindia.com"):
            return self.http_min_interval_nse_archive
        if host.endswith("bseindia.com"):
            return max(self.http_min_interval_default, 2.0)
        return self.http_min_interval_default

    def as_dict(self):
        """All settings (safe to log: no credentials)."""
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["db"] = self.db_description
        return {k: str(v) if isinstance(v, Path) else v
                for k, v in out.items()}


settings = Settings()
