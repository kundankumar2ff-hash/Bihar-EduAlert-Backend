
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


# --------------------------------------------------
# 1. OFFICIAL WEBSITE SOURCES
# --------------------------------------------------

BOARD_SOURCES = {
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


JOB_SOURCES = {
    "Police": [
        "https://csbc.bihar.gov.in/",
        "https://bpssc.bihar.gov.in/"
    ],
    "Army": [
        "https://joinindianarmy.nic.in/"
    ],
    "Railway": [
        "https://www.rrbapply.gov.in/"
    ],
    "SSC": [
        "https://ssc.gov.in/"
    ],
    "Bank": [
        "https://www.ibps.in/"
    ],
    "BPSC": [
        "https://bpsc.bihar.gov.in/"
    ],
    "Teacher": [
        "https://bssc.bihar.gov.in/",
        "https://btsc.bihar.gov.in/"
    ],
    "Defence": [
        "https://joinindianarmy.nic.in/"
    ]
}


NEWS_SOURCES = {
    "Education": [
        "https://state.bihar.gov.in/educationbihar/"
    ],
    "General": [
        "https://state.bihar.gov.in/prdbihar/",
        "https://state.bihar.gov.in/main/CitizenHome.html"
    ]
}


# --------------------------------------------------
# 2. KEYWORDS
# --------------------------------------------------

BOARD_KEYWORDS = [
    "notice", "circular", "result", "admit",
    "exam", "syllabus", "date sheet", "model paper",
    "notification", "registration", "विज्ञप्ति",
    "परीक्षा", "सूचना", "पाठ्यक्रम", "मॉडल"
]

JOB_KEYWORDS = [
    "recruitment", "vacancy", "recruit",
    "application", "apply online", "advertisement",
    "notification", "post", "constable", "sub inspector",
    "agniveer", "group d", "ntpc", "technician",
    "assistant", "teacher", "lecturer", "officer",
    "clerk", "engineer", "bharti", "रिक्ति",
    "भर्ती", "नियुक्ति", "आवेदन", "पद"
]

EXAM_KEYWORDS = [
    "admit card", "exam date", "examination",
    "answer key", "result", "merit list",
    "cut off", "cutoff", "exam notice",
    "प्रवेश पत्र", "परीक्षा तिथि", "उत्तर कुंजी",
    "परिणाम", "रिजल्ट"
]

NEWS_KEYWORDS = [
    "news", "notice", "announcement", "scheme",
    "scholarship", "education", "student",
    "yojana", "छात्रवृत्ति", "योजना",
    "शिक्षा", "विद्यार्थी", "सूचना"
]


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BiharEduAlert/2.0)"
}

TIMEOUT = 30


# --------------------------------------------------
# 3. COMMON FUNCTIONS
# --------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def get_title(link):
    title = link.get_text(" ", strip=True)

    if not title:
        title = link.get("title", "")

    if not title:
        img = link.find("img")
        if img:
            title = img.get("alt", "")

    return clean_text(title)


def matches_keyword(title, keywords):
    text = title.casefold()

    return any(
        keyword.casefold() in text
        for keyword in keywords
    )


def valid_url(url):
    parsed = urlparse(url)

    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
    )


def make_item(category, title, url, source):
    return {
        "category": category,
        "title": title,
        "url": url,
        "source": source,
        "description": title,
        "checked_at": now_utc()
    }


# --------------------------------------------------
# 4. SCRAPE WEBSITE LINKS
# --------------------------------------------------

def scrape_page(session, page_url):

    response = session.get(
        page_url,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.content,
        "html.parser"
    )

    found_links = []

    for link in soup.find_all("a", href=True):

        title = get_title(link)

        if not title or len(title) < 4:
            continue

        href = link.get("href", "").strip()

        if not href:
            continue

        full_url = urljoin(page_url, href)

        if not valid_url(full_url):
            continue

        found_links.append({
            "title": title,
            "url": full_url,
            "source": page_url
        })

    return found_links


# --------------------------------------------------
# 5. FETCH BOARD UPDATES
# --------------------------------------------------

def get_board_updates(session, source_status):

    updates = []

    for board, urls in BOARD_SOURCES.items():

        for page_url in urls:

            try:
                links = scrape_page(session, page_url)
                found = 0

                for item in links:

                    if not matches_keyword(
                        item["title"],
                        BOARD_KEYWORDS
                    ):
                        continue

                    updates.append({
                        "board": board,
                        "title": item["title"],
                        "url": item["url"],
                        "source": page_url,
                        "checked_at": now_utc()
                    })

                    found += 1

                source_status.append({
                    "type": "board",
                    "category": board,
                    "source": page_url,
                    "status": "success",
                    "links_found": found
                })

                print(
                    f"BOARD: {board} | "
                    f"{page_url} | Found: {found}"
                )

            except Exception as error:

                source_status.append({
                    "type": "board",
                    "category": board,
                    "source": page_url,
                    "status": "error",
                    "error": str(error)
                })

                print("BOARD ERROR:", page_url, error)

    return unique_by_url(updates)


# --------------------------------------------------
# 6. FETCH JOB AND EXAM UPDATES
# --------------------------------------------------

def get_job_and_exam_updates(session, source_status):

    job_updates = []
    exam_updates = []

    for category, urls in JOB_SOURCES.items():

        for page_url in urls:

            try:
                links = scrape_page(session, page_url)

                job_found = 0
                exam_found = 0

                for item in links:

                    title = item["title"]

                    is_job = matches_keyword(
                        title,
                        JOB_KEYWORDS
                    )

                    is_exam = matches_keyword(
                        title,
                        EXAM_KEYWORDS
                    )

                    if is_job:

                        job_updates.append(
                            make_item(
                                category,
                                title,
                                item["url"],
                                page_url
                            )
                        )

                        job_found += 1

                    if is_exam:

                        exam_updates.append(
                            make_item(
                                category,
                                title,
                                item["url"],
                                page_url
                            )
                        )

                        exam_found += 1

                source_status.append({
                    "type": "job",
                    "category": category,
                    "source": page_url,
                    "status": "success",
                    "job_links_found": job_found,
                    "exam_links_found": exam_found
                })

                print(
                    f"JOB: {category} | "
                    f"Jobs: {job_found} | "
                    f"Exam: {exam_found}"
                )

            except Exception as error:

                source_status.append({
                    "type": "job",
                    "category": category,
                    "source": page_url,
                    "status": "error",
                    "error": str(error)
                })

                print("JOB ERROR:", page_url, error)

    return (
        unique_by_url(job_updates),
        unique_by_url(exam_updates)
    )


# --------------------------------------------------
# 7. FETCH BIHAR NEWS
# --------------------------------------------------

def get_news_updates(session, source_status):

    updates = []

    for category, urls in NEWS_SOURCES.items():

        for page_url in urls:

            try:
                links = scrape_page(session, page_url)
                found = 0

                for item in links:

                    if not matches_keyword(
                        item["title"],
                        NEWS_KEYWORDS
                    ):
                        continue

                    updates.append(
                        make_item(
                            category,
                            item["title"],
                            item["url"],
                            page_url
                        )
                    )

                    found += 1

                source_status.append({
                    "type": "news",
                    "category": category,
                    "source": page_url,
                    "status": "success",
                    "links_found": found
                })

                print(
                    f"NEWS: {category} | Found: {found}"
                )

            except Exception as error:

                source_status.append({
                    "type": "news",
                    "category": category,
                    "source": page_url,
                    "status": "error",
                    "error": str(error)
                })

                print("NEWS ERROR:", page_url, error)

    return unique_by_url(updates)


# --------------------------------------------------
# 8. REMOVE DUPLICATES
# --------------------------------------------------

def unique_by_url(items):

    unique = {}

    for item in items:
        unique[item["url"]] = item

    return list(unique.values())


# --------------------------------------------------
# 9. FETCH ALL UPDATES AND CREATE JSON
# --------------------------------------------------

def get_updates():

    session = requests.Session()
    session.headers.update(HEADERS)

    source_status = []

    board_updates = get_board_updates(
        session,
        source_status
    )

    job_updates, exam_updates = get_job_and_exam_updates(
        session,
        source_status
    )

    news_updates = get_news_updates(
        session,
        source_status
    )

    result = {
        "checked_at": now_utc(),

        "total_updates": (
            len(board_updates)
            + len(job_updates)
            + len(exam_updates)
            + len(news_updates)
        ),

        "source_status": source_status,

        # Old key retained for compatibility
        "updates": board_updates,

        "board_updates": board_updates,
        "job_updates": job_updates,
        "exam_updates": exam_updates,
        "news_updates": news_updates
    }

    with open(
        "updates.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("--------------------------------")
    print("BOARD UPDATES:", len(board_updates))
    print("JOB UPDATES:", len(job_updates))
    print("EXAM UPDATES:", len(exam_updates))
    print("NEWS UPDATES:", len(news_updates))
    print("TOTAL:", result["total_updates"])
    print("--------------------------------")

    return result


# --------------------------------------------------
# 10. FIREBASE CONNECTION
# --------------------------------------------------

def connect_firebase():

    secret_value = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT"
    )

    if not secret_value:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT GitHub Secret nahi mila."
        )

    service_account_info = json.loads(secret_value)

    if not firebase_admin._apps:

        cred = credentials.Certificate(
            service_account_info
        )

        firebase_admin.initialize_app(cred)

    return firestore.client()


# --------------------------------------------------
# 11. FIREBASE BATCH UPLOAD
# --------------------------------------------------

def upload_collection(db, collection_name, items):

    if not items:
        print(
            f"No new items for {collection_name}"
        )
        return 0

    collection = db.collection(collection_name)

    batch = db.batch()
    count = 0
    total = 0

    for item in items:

        document_id = hashlib.sha256(
            item["url"].encode("utf-8")
        ).hexdigest()

        document = collection.document(document_id)

        data = dict(item)

        data["updated_at"] = firestore.SERVER_TIMESTAMP

        batch.set(
            document,
            data,
            merge=True
        )

        count += 1
        total += 1

        # Firestore batch maximum is 500 operations.
        # Keep below the limit.
        if count >= 400:

            batch.commit()

            batch = db.batch()
            count = 0

    if count > 0:
        batch.commit()

    print(
        f"Firebase upload successful: "
        f"{collection_name} = {total}"
    )

    return total


# --------------------------------------------------
# 12. UPLOAD ALL COLLECTIONS
# --------------------------------------------------

def upload_to_firebase(result):

    db = connect_firebase()

    upload_collection(
        db,
        "board_updates",
        result["board_updates"]
    )

    upload_collection(
        db,
        "job_updates",
        result["job_updates"]
    )

    upload_collection(
        db,
        "exam_updates",
        result["exam_updates"]
    )

    upload_collection(
        db,
        "news_updates",
        result["news_updates"]
    )

    print("--------------------------------")
    print("ALL FIREBASE UPLOADS COMPLETED")
    print("--------------------------------")


# --------------------------------------------------
# 13. MAIN
# --------------------------------------------------

if __name__ == "__main__":

    result = get_updates()

    upload_to_firebase(result)
