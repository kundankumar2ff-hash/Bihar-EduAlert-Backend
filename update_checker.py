import requests
from bs4 import BeautifulSoup
import json
import re
import os
import hashlib
import firebase_admin

from firebase_admin import credentials, firestore
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse


SOURCES = {
    "Bihar Board": [
        "https://www.biharboardonline.org/home",
        "https://exam.biharboardonline.org/",
        "https://intermediate.biharboardonline.com/"
    ],
    "CBSE Board": [
        "https://www.cbse.gov.in/cbsenew/examination_circular.html",
        "https://cbseacademic.nic.in/circulars.html"
    ]
}


KEYWORDS = [
    "notice", "circular", "result", "admit",
    "exam", "syllabus", "date sheet", "model paper",
    "notification", "registration", "विज्ञप्ति",
    "परीक्षा", "सूचना", "पाठ्यक्रम", "मॉडल"
]


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BiharEduAlert/1.0)"
}


def get_title(link):
    title = link.get_text(" ", strip=True)

    if not title:
        title = link.get("title", "")

    if not title:
        img = link.find("img")
        if img:
            title = img.get("alt", "")

    return re.sub(r"\s+", " ", title).strip()


def get_updates():
    updates = []
    source_status = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for board, urls in SOURCES.items():
        for page_url in urls:
            try:
                response = session.get(page_url, timeout=30)
                response.raise_for_status()

                soup = BeautifulSoup(
                    response.content, "html.parser"
                )

                found = 0

                for link in soup.find_all("a", href=True):
                    title = get_title(link)
                    href = urljoin(page_url, link["href"].strip())

                    if not title or len(title) < 4:
                        continue

                    if not any(
                        word.casefold() in title.casefold()
                        for word in KEYWORDS
                    ):
                        continue

                    if urlparse(href).scheme not in ("http", "https"):
                        continue

                    updates.append({
                        "board": board,
                        "title": title,
                        "url": href,
                        "source": page_url,
                        "checked_at": datetime.now(
                            timezone.utc
                        ).isoformat()
                    })

                    found += 1

                source_status.append({
                    "board": board,
                    "source": page_url,
                    "status": "success",
                    "links_found": found
                })

                print(f"Checked: {page_url} | Found: {found}")

            except Exception as error:
                source_status.append({
                    "board": board,
                    "source": page_url,
                    "status": "error",
                    "error": str(error)
                })

                print(f"Error: {page_url} | {error}")

    unique = {}

    for item in updates:
        unique[item["url"]] = item

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_updates": len(unique),
        "source_status": source_status,
        "updates": list(unique.values())
    }

    with open("updates.json", "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print("Total updates:", len(unique))

    return list(unique.values())


def upload_to_firebase(updates):
    secret_value = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

    if not secret_value:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT GitHub Secret nahi mila."
        )

    service_account_info = json.loads(secret_value)

    if not firebase_admin._apps:
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)

    db = firestore.client()

    collection = db.collection("board_updates")

    batch = db.batch()
    count = 0

    for item in updates:
        document_id = hashlib.sha256(
            item["url"].encode("utf-8")
        ).hexdigest()

        document = collection.document(document_id)

        batch.set(document, {
            "board": item["board"],
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "checked_at": item["checked_at"],
            "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

        count += 1

        if count % 400 == 0:
            batch.commit()
            batch = db.batch()

    if count % 400 != 0:
        batch.commit()

    print("Firebase upload successful:", count)


if __name__ == "__main__":
    updates = get_updates()
    upload_to_firebase(updates)
