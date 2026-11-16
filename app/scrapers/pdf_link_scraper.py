import requests
import csv
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def classify_pdf(title, url):

    text = (title + " " + url).lower()

    if "brsr core" in text:
        return "BRSR Core"

    elif "brsr" in text:
        return "BRSR Report"

    elif "business responsibility report" in text or "brr" in text:
        return "Business Responsibility Report"

    elif "sustainability" in text:
        return "Sustainability Report"

    elif "annual report" in text:
        return "Annual Report"

    else:
        return "Other"


def extract_pdf_links():

    url = "https://www.ril.com/investors/financial-reporting"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        links = soup.find_all("a", href=True)

        pdf_data = []

        for link in links:

            href = link["href"]
            title = link.get_text(" ", strip=True)

            if ".pdf" in href.lower():

                full_url = urljoin(url, href)

                category = classify_pdf(title, full_url)

                pdf_data.append({
                    "title": title,
                    "url": full_url,
                    "category": category
                })

        # Display categorized results
        print("\nPDF CLASSIFICATION RESULTS")
        print("-" * 40)

        for pdf in pdf_data:
            print("\nTitle:", pdf["title"])
            print("Category:", pdf["category"])
            print("URL:", pdf["url"])

        # Save results in CSV
        with open(
            "data/processed/pdf_classification.csv",
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=["title", "url", "category"]
            )

            writer.writeheader()
            writer.writerows(pdf_data)

        print("\nTotal PDFs found:", len(pdf_data))
        print("CSV file saved successfully!")

    except requests.RequestException as error:
        print("Website scraping failed:", error)


if __name__ == "__main__":
    extract_pdf_links()