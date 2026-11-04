import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import firebase_admin
from firebase_admin import credentials, firestore


# =====================================================
# BIHAR EDU ALERT - AUTOMATIC UPDATE CHECKER
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

USER_AGENT = "BiharEduAlert/1.0"
TIMEOUT = 25
MAX_PAGE_SIZE = 5 * 1024 * 1024

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT
})


# =====================================================
# OFFICIAL WEBSITES
# =====================================================

SOURCES = [
    {
        "name": "Bihar Public Service Commission",
        "category": "BPSC",
        "url": "https://bpsc.bihar.gov.in/"
    },
    {
        "name": "Bihar Staff Selection Commission",
        "category": "BSSC",
        "url": "https://bssc.bihar.gov.in/"
    },
    {
        "name": "Bihar Police CSBC",
        "category": "Police",
        "url": "https://csbc.bihar.gov.in/"
    },
    {
        "name": "Bihar Police BPSSC",
        "category": "Police",
        "url": "https://bpssc.bihar.gov.in/"
    },
    {
        "name": "Bihar Board",
        "category": "Bihar Board",
        "url": "https://biharboardonline.bihar.gov.in/"
    },
    {
        "name": "CBSE",
        "category": "CBSE Board",
        "url": "https://www.cbse.gov.in/"
    },
    {
        "name": "CBSE Academic",
        "category": "CBSE Board",
        "url": "https://cbseacademic.nic.in/"
    },
    {
        "name": "Staff Selection Commission",
        "category": "SSC",
        "url": "https://ssc.gov.in/"
    },
    {
        "name": "Indian Railways Recruitment",
        "category": "Railway",
        "url": "https://www.rrbapply.gov.in/"
    },
    {
        "name": "Indian Army",
        "category": "Army",
        "url": "https://joinindianarmy.nic.in/"
    },
    {
        "name": "National Career Service",
        "category": "Government Jobs",
        "url": "https://www.ncs.gov.in/"
    }
]


# =====================================================
# KEYWORDS
# =====================================================

KEYWORDS = [
    "recruitment",
    "vacancy",
    "notification",
    "advertisement",
    "apply online",
    "application",
    "admit card",
    "hall ticket",
    "result",
    "merit list",
    "answer key",
    "exam date",
    "examination",
    "syllabus",
    "scholarship",
    "scheme",
    "government job",
    "भर्ती",
    "नियुक्ति",
    "विज्ञापन",
    "आवेदन",
    "प्रवेश पत्र",
    "एडमिट कार्ड",
    "परिणाम",
    "रिजल्ट",
    "उत्तर कुंजी",
    "परीक्षा",
    "छात्रवृत्ति",
    "योजना"
]


# =====================================================
# FIREBASE CONNECTION
# =====================================================

def initialize_firebase():

    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

    if not raw:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT secret is missing"
        )

    service_account = json.loads(raw)

    if not firebase_admin._apps:

        certificate = credentials.Certificate(
            service_account
        )

        firebase_admin.initialize_app(certificate)

    return firestore.client()


# =====================================================
# CREATE UPDATE ID
# =====================================================

def make_update_id(url, title):

    raw = (url + "|" + title).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


# =====================================================
# IDENTIFY UPDATE CATEGORY
# =====================================================

def identify_type(text):

    text = text.casefold()

    if any(word in text for word in [
        "admit card", "hall ticket", "प्रवेश पत्र",
        "एडमिट कार्ड"
    ]):
        return "Admit Card"

    if any(word in text for word in [
        "result", "merit list", "परिणाम", "रिजल्ट"
    ]):
        return "Result"

    if any(word in text for word in [
        "answer key", "उत्तर कुंजी"
    ]):
        return "Answer Key"

    if any(word in text for word in [
        "recruitment", "vacancy", "भर्ती",
        "नियुक्ति", "advertisement"
    ]):
        return "Recruitment"

    if any(word in text for word in [
        "scholarship", "छात्रवृत्ति"
    ]):
        return "Scholarship"

    if any(word in text for word in [
        "scheme", "योजना"
    ]):
        return "Government Scheme"

    if any(word in text for word in [
        "exam date", "examination", "परीक्षा"
    ]):
        return "Exam Update"

    return "Official Notice"


# =====================================================
# CHECK WEBSITE
# =====================================================

def fetch_website(source):

    url = source["url"]

    try:

        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()

        if len(response.content) > MAX_PAGE_SIZE:
            logging.warning("Page too large: %s", url)
            return []

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        if "html" not in content_type:
            logging.warning("Not an HTML page: %s", url)
            return []

        soup = BeautifulSoup(
            response.content,
            "html.parser"
        )

        updates = []
        seen = set()

        for tag in soup.find_all("a", href=True):

            title = tag.get_text(
                " ",
                strip=True
            )

            link = urljoin(
                response.url,
                tag["href"]
            )

            if not title or len(title) < 5:
                continue

            if len(title) > 300:
                continue

            parsed = urlparse(link)

            if parsed.scheme not in ("http", "https"):
                continue

            if link in seen:
                continue

            combined = title.casefold()

            if not any(
                keyword in combined
                for keyword in KEYWORDS
            ):
                continue

            seen.add(link)

            updates.append({
                "title": title,
                "url": link,
                "type": identify_type(title),
                "source": source["name"],
                "category": source["category"]
            })

        return updates

    except requests.RequestException as error:

        logging.warning(
            "Website check failed: %s | %s",
            url,
            error
        )

        return []


# =====================================================
# SAVE TO FIRESTORE
# =====================================================

def save_update(db, update):

    document_id = make_update_id(
        update["url"],
        update["title"]
    )

    reference = (
        db.collection("announcements")
        .document(document_id)
    )

    try:

        if reference.get().exists:
            logging.info(
                "Already exists: %s",
                update["title"]
            )
            return False

        document = {
            "title": update["title"],
            "url": update["url"],
            "type": update["type"],
            "source": update["source"],
            "category": update["category"],
            "active": True,
            "verified": False,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }

        reference.set(document)

        logging.info(
            "Saved: %s",
            update["title"]
        )

        return True

    except Exception as error:

        logging.error(
            "Firestore error: %s",
            error
        )

        return False


# =====================================================
# SAVE LOCAL JSON REPORT
# =====================================================

def save_json(updates):

    output = {
        "project": "Bihar EduAlert",
        "generatedAt": datetime.now(
            timezone.utc
        ).isoformat(),
        "totalUpdates": len(updates),
        "updates": updates
    }

    with open(
        "updates.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )


# =====================================================
# MAIN
# =====================================================

def main():

    logging.info(
        "========== BIHAR EDU ALERT UPDATE CHECKER =========="
    )

    db = initialize_firebase()

    all_updates = []
    total_saved = 0

    for source in SOURCES:

        logging.info(
            "Checking: %s",
            source["name"]
        )

        updates = fetch_website(source)

        logging.info(
            "Found %s possible updates",
            len(updates)
        )

        for update in updates:

            all_updates.append(update)

            if save_update(db, update):
                total_saved += 1

    save_json(all_updates)

    logging.info(
        "Total possible updates: %s",
        len(all_updates)
    )

    logging.info(
        "New updates saved to Firebase: %s",
        total_saved
    )

    logging.info(
        "========== UPDATE CHECK COMPLETE =========="
    )


if __name__ == "__main__":

    try:
        main()

    except Exception:

        logging.exception(
            "Update checker failed"
        )

        raise
