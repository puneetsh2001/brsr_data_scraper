"""
Download and extract the official SEBI BRSR XBRL taxonomy (NSE archive).

The NSE archive CDN is intermittent (HTTP 503 / 520-530 have been
observed). The shared HTTP client retries with exponential backoff; if the
download still fails, the previously downloaded local ZIP is used and the
fallback is recorded - nothing is fabricated.

Every successful download writes a metadata file next to the ZIP
(source URL, timestamp, size, SHA-256) for the taxonomy registry.

Run:
    python -m app.downloaders.sebi_brsr_taxonomy_downloader
    python -m app.downloaders.sebi_brsr_taxonomy_downloader --output-dir <dir>
"""

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.http_client import nse_archive_client
from app.core.logging_setup import get_logger


log = get_logger(__name__, run_name="taxonomy")

DEFAULT_OUTPUT_DIR = settings.raw_dir / "sebi_brsr_taxonomy"
TAXONOMY_URL = settings.brsr_taxonomy_url


def validate_zip(content):
    if not content.startswith(b"PK"):
        raise ValueError("downloaded response is not a ZIP file")


def safe_extract(zip_path, extract_dir):
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            destination = (extract_dir / member.filename).resolve()
            if not destination.is_relative_to(extract_dir.resolve()):
                raise ValueError("Unsafe archive path: " + member.filename)
        archive.extractall(extract_dir)
        return archive.namelist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--url", default=TAXONOMY_URL)
    parser.add_argument(
        "--refresh", action="store_true",
        help="download again even if a local ZIP exists (the previous ZIP "
             "is kept as a timestamped backup)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    zip_file = output_dir / "Taxonomy_BRSR.zip"
    meta_file = output_dir / "Taxonomy_BRSR.download.json"
    extract_dir = output_dir / "extracted"
    output_dir.mkdir(parents=True, exist_ok=True)

    source = "local_cache"
    if args.refresh or not zip_file.exists():
        try:
            if zip_file.exists():
                backup = zip_file.with_name(
                    f"Taxonomy_BRSR_{datetime.now():%Y%m%d_%H%M%S}.zip")
                zip_file.replace(backup)
                log.info("previous taxonomy ZIP kept", extra={"fields": {
                    "backup": str(backup)}})
            result = nse_archive_client().download(
                args.url, zip_file, validate=validate_zip)
            source = "download"
            meta_file.write_text(json.dumps({
                "source_url": args.url,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "bytes": result["bytes"],
                "sha256": result["sha256"],
            }, indent=2), encoding="utf-8")
            log.info("taxonomy downloaded", extra={"fields": result})
        except Exception as error:
            log.error("taxonomy download failed", extra={"fields": {
                "url": args.url, "error": str(error)}})
            if not zip_file.exists():
                backups = sorted(output_dir.glob("Taxonomy_BRSR_*.zip"))
                if backups:
                    backups[-1].replace(zip_file)
            if not zip_file.exists():
                raise SystemExit("No local taxonomy ZIP available - cannot "
                                 "continue without the source file.")
            source = "local_cache_after_download_failure"

    sha256 = hashlib.sha256(zip_file.read_bytes()).hexdigest()
    members = safe_extract(zip_file, extract_dir)
    xsd = list(extract_dir.rglob("*.xsd"))
    xml = list(extract_dir.rglob("*.xml"))

    log.info("taxonomy extracted", extra={"fields": {
        "source": source, "zip": str(zip_file), "sha256": sha256,
        "members": len(members), "xsd_files": len(xsd),
        "xml_files": len(xml), "extract_dir": str(extract_dir)}})

    print(f"Taxonomy source : {source}")
    print(f"ZIP             : {zip_file}")
    print(f"SHA-256         : {sha256}")
    print(f"Archive members : {len(members)}  (XSD {len(xsd)}, XML {len(xml)})")
    print(f"Extracted to    : {extract_dir}")


if __name__ == "__main__":
    main()
