
import re
import requests

from pathlib import Path
from urllib.parse import urlparse, unquote


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def download_brsr_reports(reports):
    """
    Download discovered BRSR PDF reports.

    Each company's reports are stored in a separate folder.
    """

    downloaded_reports = []

    if not reports:
        print("No reports available for downloading.")
        return downloaded_reports

    for report in reports:

        company_name = report["company_name"]
        financial_year = report["financial_year"]
        pdf_url = report["report_url"]

        # Create a safe folder name
        safe_company_name = re.sub(
            r'[<>:"/\\|?*]',
            "_",
            company_name
        )

        download_folder = (
            Path("data/raw") / safe_company_name
        )

        download_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        # Extract filename from PDF URL
        filename = Path(
            unquote(urlparse(pdf_url).path)
        ).name

        if not filename.lower().endswith(".pdf"):
            filename = f"BRSR_{financial_year.replace(' ', '_')}.pdf"

        file_path = download_folder / filename

        print("\n" + "=" * 50)
        print("Company:", company_name)
        print("Financial Year:", financial_year)
        print("Downloading:", filename)

        # Avoid downloading an existing file again
        if file_path.exists():
            print("Already downloaded:", filename)

            downloaded_reports.append({
                **report,
                "file_path": str(file_path)
            })

            continue

        try:

            response = requests.get(
                pdf_url,
                headers=HEADERS,
                timeout=60
            )

            response.raise_for_status()

            # Verify that the response is actually a PDF
            if not response.content.startswith(b"%PDF-"):
                print("Invalid PDF response. Skipping.")
                continue

            file_path.write_bytes(response.content)

            print("Download successful!")
            print("Saved at:", file_path)
            print("File size:", len(response.content), "bytes")

            downloaded_reports.append({
                **report,
                "file_path": str(file_path)
            })

        except requests.RequestException as error:

            print("Download failed:", pdf_url)
            print("Reason:", error)

    print("\n" + "=" * 50)
    print("DOWNLOAD SUMMARY")
    print("=" * 50)

    print("Total reports:", len(reports))
    print("Successfully available:", len(downloaded_reports))

    return downloaded_reports
