import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime, timezone

SOURCES = {
    "Bihar Board": [
        "https://www.biharboardonline.org/home",
        "https://exam.biharboardonline.org/"
    ],
    "CBSE Board": [
        "https://www.cbse.gov.in/cbsenew/examination_circular.html",
        "https://cbseacademic.nic.in/circulars.html"
    ]
}

KEYWORDS = [
    "notice", "circular", "result", "admit",
    "exam", "syllabus", "date sheet", "model paper",
    "notification", "registration", "परीक्षा", "सूचना"
]

def get_updates():
    updates = []

    for board, urls in SOURCES.items():
        for url in urls:
            try:
                response = requests.get(
                    url,
                    timeout=25,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")

                for link in soup.find_all("a", href=True):
                    title = link.get_text(" ", strip=True)
                    href = link["href"].strip()

                    if not title or not any(
                        word in title.lower() for word in KEYWORDS
                    ):
                        continue

                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        from urllib.parse import urljoin
                        href = urljoin(url, href)
                    elif not href.startswith("http"):
                        from urllib.parse import urljoin
                        href = urljoin(url, href)

                    updates.append({
                        "board": board,
                        "title": title,
                        "url": href,
                        "source": url
                    })

            except Exception as error:
                print(f"Error: {url} - {error}")

    unique = {
        item["url"]: item for item in updates
    }

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "updates": list(unique.values())
    }

    with open("updates.json", "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(f"Total updates found: {len(unique)}")


if __name__ == "__main__":
    get_updates()
