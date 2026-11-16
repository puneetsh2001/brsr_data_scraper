
import re
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from app.company.company_mapper import get_company_website


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}


def extract_financial_year(text):

    patterns = [
        r"(20\d{2})[-–_/](\d{2,4})",
        r"(20\d{2})(\d{2})"
    ]

    for pattern in patterns:

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            start_year = int(match.group(1))
            end_year = match.group(2)

            return f"FY {start_year}-{end_year[-2:]}"

    return "Unknown"


def discover_brsr_reports(company_name):

    company_info = get_company_website(company_name)

    if not company_info:
        print(f"Company mapping not found: {company_name}")
        return []

    investor_url = company_info["investor_url"]

    print(f"\nSearching BRSR reports for: {company_name}")
    print(f"Investor URL: {investor_url}")

    try:

        response = requests.get(
            investor_url,
            headers=HEADERS,
            timeout=60
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        discovered_reports = []

        for link in soup.find_all("a", href=True):

            href = link["href"]
            link_text = link.get_text(" ", strip=True)

            full_url = urljoin(response.url, href)

            searchable_text = (
                link_text + " " + href + " " + full_url
            ).lower()

            is_brsr = (
                "brsr" in searchable_text
                or "business responsibility and sustainability report"
                in searchable_text
            )

            is_pdf = ".pdf" in full_url.lower()

            if is_brsr and is_pdf:

                financial_year = extract_financial_year(
                    searchable_text
                )

                report = {
                    "company_name": company_name,
                    "financial_year": financial_year,
                    "report_type": "BRSR",
                    "report_url": full_url,
                    "source_url": investor_url
                }

                if not any(
                    existing["report_url"] == full_url
                    for existing in discovered_reports
                ):
                    discovered_reports.append(report)

        print(f"Reports found: {len(discovered_reports)}")

        return discovered_reports

    except requests.RequestException as error:

        print(f"Website request failed: {error}")
        return []

    except Exception as error:

        print(f"Unexpected error: {error}")
        return []


# Diagnostic code runs only when this file is executed directly.
# It will not run when another Python file imports this module.

if __name__ == "__main__":

    company_name = "Reliance Industries Limited"

    reports = discover_brsr_reports(company_name)

    print("\nDISCOVERED BRSR REPORTS:")

    for report in reports:
        print("----------------------------------")
        print("Company:", report["company_name"])
        print("Financial Year:", report["financial_year"])
        print("Report URL:", report["report_url"])
