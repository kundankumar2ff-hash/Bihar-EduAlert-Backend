import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore


# =========================================================
# SETTINGS
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
NOW = datetime.now(UTC)

REQUEST_TIMEOUT = 25
MAX_AGE_DAYS = 30
BATCH_SIZE = 400

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

# =========================================================
# OFFICIAL BOARD SOURCES
# =========================================================

BOARD_SOURCES = [
    {"category": "Bihar Board", "url": "https://biharboardonline.bihar.gov.in/"},
    {"category": "Bihar Board", "url": "https://secondary.biharboardonline.com/"},
    {"category": "Bihar Board", "url": "https://results.biharboardonline.com/"},
    {"category": "CBSE Board", "url": "https://www.cbse.gov.in/"},
    {"category": "CBSE Board", "url": "https://cbseacademic.nic.in/"},
]

# =========================================================
# JOB SOURCES
# =========================================================

JOB_SOURCES = [
    {"category": "Police", "url": "https://csbc.bihar.gov.in/"},
    {"category": "Police", "url": "https://bpssc.bihar.gov.in/"},
    {"category": "Army", "url": "https://joinindianarmy.nic.in/"},
    {"category": "Railway", "url": "https://www.rrbapply.gov.in/"},
    {"category": "SSC", "url": "https://ssc.gov.in/"},
    {"category": "Bank", "url": "https://www.ibps.in/"},
    {"category": "BPSC", "url": "https://bpsc.bihar.gov.in/"},
    {"category": "Teacher", "url": "https://bpsc.bihar.gov.in/"},
    {"category": "Teacher", "url": "https://bssc.bihar.gov.in/"},
    {"category": "Teacher", "url": "https://btsc.bihar.gov.in/"},
    {"category": "Defence", "url": "https://afcat.cdac.in/"},
    {"category": "Defence", "url": "https://joinindiannavy.gov.in/"},
    {"category": "Defence", "url": "https://joinindiancoastguard.cdac.in/"},
]

# =========================================================
# NEWS RSS SOURCES
# =========================================================

NEWS_RSS_SOURCES = [
    {"name": "Hindustan Bihar", "url": "https://www.livehindustan.com/rss/bihar.xml"},
    {"name": "Amar Ujala Bihar", "url": "https://www.amarujala.com/rss/bihar.xml"},
    {"name": "Prabhat Khabar Bihar", "url": "https://www.prabhatkhabar.com/feed"},
    {"name": "Hindustan Education", "url": "https://www.livehindustan.com/rss/education.xml"},
    {"name": "Hindustan Jobs", "url": "https://www.livehindustan.com/rss/career.xml"},
]

# =========================================================
# KEYWORDS
# =========================================================

JOB_KEYWORDS = [
    "recruitment", "vacancy", "notification", "application",
    "online form", "apply online", "admit card", "result",
    "भर्ती", "रिक्ति", "विज्ञापन", "आवेदन", "प्रवेश पत्र",
    "परिणाम", "नियुक्ति"
]

EXAM_KEYWORDS = [
    "exam date", "examination", "exam schedule", "time table",
    "date sheet", "answer key", "exam notice", "प्रवेश परीक्षा",
    "परीक्षा", "परीक्षा तिथि", "उत्तर कुंजी", "समय सारणी"
]

CATEGORY_KEYWORDS = {
    "Police": [
        "police", "constable", "sub inspector", "si recruitment",
        "daroga", "सिपाही", "पुलिस", "दरोगा"
    ],
    "Army": [
        "indian army", "agniveer army", "army recruitment",
        "अग्निवीर सेना", "भारतीय सेना"
    ],
    "Railway": [
        "railway", "rrb", "rrc", "ntpc", "group d",
        "रेलवे", "रेल भर्ती"
    ],
    "SSC": [
        "staff selection commission", "ssc cgl", "ssc chsl",
        "ssc gd", "mts", "स्टाफ सिलेक्शन"
    ],
    "Bank": [
        "ibps", "sbi po", "sbi clerk", "bank recruitment",
        "banking", "बैंक भर्ती"
    ],
    "BPSC": [
        "bpsc", "bihar public service commission",
        "बिहार लोक सेवा आयोग"
    ],
    "Teacher": [
        "teacher recruitment", "school teacher", "tre",
        "tet", "bihar teacher", "शिक्षक भर्ती", "अध्यापक भर्ती"
    ],
    "Defence": [
        "air force", "navy", "coast guard", "afcat",
        "agniveer vayu", "agniveer navy", "वायु सेना",
        "नौसेना", "तटरक्षक"
    ],
}

# =========================================================
# GENERAL HELPERS
# =========================================================

def clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def make_id(category, url):
    raw = f"{category}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_date(value):
    """Convert common date strings into timezone-aware UTC datetime."""
    if not value:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    value = clean_text(value)

    try:
        parsed = parsedate_to_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    except Exception:
        pass

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def find_deadline(text):
    """
    Conservative deadline extraction.
    Only dates close to deadline-related labels are considered.
    """
    if not text:
        return None

    patterns = [
        r"(?:last date|closing date| आवेदन की अंतिम तिथि|अंतिम तिथि|आवेदन की अंतिम तारीख)"
        r"\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})",

        r"(?:last date|closing date|अंतिम तिथि|अंतिम तारीख)"
        r"\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result = parse_date(match.group(1))
            if result:
                return result

    return None


def page_request(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response
    except Exception as error:
        logging.warning("Page error %s : %s", url, error)
        return None


def keyword_match(text, keywords):
    text = clean_text(text).casefold()
    return any(word.casefold() in text for word in keywords)


def category_match(title, category):
    title = clean_text(title).casefold()
    keywords = CATEGORY_KEYWORDS.get(category, [])

    for keyword in keywords:
        keyword = keyword.casefold()

        # Short English terms are matched as complete words.
        if keyword.isascii() and len(keyword) <= 4:
            if re.search(r"\b" + re.escape(keyword) + r"\b", title):
                return True
        elif keyword in title:
            return True

    return False


def get_page_links(url):
    response = page_request(url)
    if not response:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for anchor in soup.find_all("a", href=True):
        title = clean_text(anchor.get_text(" ", strip=True))
        link = urljoin(url, anchor["href"])

        if not title or len(title) < 4:
            continue

        if not link.startswith(("http://", "https://")):
            continue

        results.append({
            "title": title,
            "url": link
        })

    return results


def classify_job(title, source_category):
    """
    A source category is only a hint.
    The title must also contain a matching category keyword.
    """
    if category_match(title, source_category):
        return source_category

    return None


def is_job_or_exam(title):
    is_job = keyword_match(title, JOB_KEYWORDS)
    is_exam = keyword_match(title, EXAM_KEYWORDS)
    return is_job, is_exam


# =========================================================
# BOARD UPDATES
# =========================================================

def get_board_updates(source_status):
    updates = []

    for source in BOARD_SOURCES:
        url = source["url"]
        category = source["category"]

        links = get_page_links(url)
        source_status[url] = {
            "status": "success" if links else "no_links_or_error",
            "links_found": len(links)
        }

        for item in links:
            title = item["title"]

            if len(title) < 6:
                continue

            if keyword_match(
                title,
                [
                    "notice", "notification", "result", "admit",
                    "exam", "time table", "date sheet", "syllabus",
                    "नोटिस", "सूचना", "परीक्षा", "परिणाम",
                    "प्रवेश पत्र", "पाठ्यक्रम"
                ]
            ):
                updates.append({
                    "category": category,
                    "title": title,
                    "url": item["url"],
                    "source": url,
                    "type": "board",
                    "published_at": None
                })

    return updates


# =========================================================
# JOB AND EXAM UPDATES
# =========================================================

def get_job_and_exam_updates(source_status):
    jobs = []
    exams = []

    for source in JOB_SOURCES:
        source_url = source["url"]
        source_category = source["category"]

        links = get_page_links(source_url)

        source_status[source_url] = {
            "status": "success" if links else "no_links_or_error",
            "links_found": len(links)
        }

        for item in links:
            title = item["title"]
            link = item["url"]

            if len(title) < 6:
                continue

            is_job, is_exam = is_job_or_exam(title)

            category = classify_job(title, source_category)

            if not category:
                continue

            deadline = find_deadline(title)

            common = {
                "category": category,
                "title": title,
                "url": link,
                "source": source_url,
                "last_date": deadline.isoformat() if deadline else None
            }

            if is_job:
                job_item = dict(common)
                job_item["type"] = "job"
                jobs.append(job_item)

            if is_exam:
                exam_item = dict(common)
                exam_item["type"] = "exam"
                exams.append(exam_item)

    return jobs, exams


# =========================================================
# NEWS RSS
# =========================================================

def get_news_updates(source_status):
    news = []

    for source in NEWS_RSS_SOURCES:
        feed_url = source["url"]
        response = page_request(feed_url)

        if not response:
            source_status[feed_url] = {
                "status": "error",
                "links_found": 0
            }
            continue

        soup = BeautifulSoup(response.content, "xml")
        items = soup.find_all(["item", "entry"])

        source_status[feed_url] = {
            "status": "success" if items else "no_items",
            "links_found": len(items)
        }

        for item in items:
            title_tag = item.find("title")
            link_tag = item.find("link")
            date_tag = (
                item.find("pubDate")
                or item.find("published")
                or item.find("updated")
            )
            description_tag = (
                item.find("description")
                or item.find("summary")
            )

            title = clean_text(
                title_tag.get_text(" ", strip=True) if title_tag else ""
            )

            link = ""
            if link_tag:
                link = link_tag.get("href") or link_tag.get_text(strip=True)

            link = urljoin(feed_url, link)

            published = None
            if date_tag:
                published = parse_date(date_tag.get_text(" ", strip=True))

            description = clean_text(
                description_tag.get_text(" ", strip=True)
                if description_tag else ""
            )

            if not title or not link:
                continue

            # Skip news already older than 30 days.
            if published and published < NOW - timedelta(days=MAX_AGE_DAYS):
                continue

            news.append({
                "category": "Bihar News",
                "title": title,
                "description": description[:1000],
                "url": link,
                "source": source["name"],
                "type": "news",
                "published_at": published.isoformat() if published else None
            })

    return news


# =========================================================
# REMOVE DUPLICATES
# =========================================================

def unique_updates(items):
    output = []
    seen = set()

    for item in items:
        url = clean_text(item.get("url"))
        category = clean_text(item.get("category"))
        key = (category, url)

        if not url or key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


# =========================================================
# FIREBASE CONNECTION
# =========================================================

def connect_firebase():
    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

    if not service_account_json:
        logging.warning(
            "FIREBASE_SERVICE_ACCOUNT secret not found. "
            "JSON will be created, but Firebase upload/delete is skipped."
        )
        return None

    try:
        service_account_info = json.loads(service_account_json)
        credential = credentials.Certificate(service_account_info)

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credential)

        return firestore.client()

    except Exception as error:
        logging.exception("Firebase connection failed: %s", error)
        return None


# =========================================================
# FIRESTORE DATE HELPERS
# =========================================================

def firestore_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    return parse_date(value)


def get_first_seen(existing_data):
    if existing_data:
        old_date = firestore_datetime(existing_data.get("first_seen_at"))
        if old_date:
            return old_date

    return NOW


def upload_collection(db, collection_name, items):
    """
    Upload documents while preserving first_seen_at.
    Existing documents keep their original first_seen_at.
    """
    collection = db.collection(collection_name)
    batch = db.batch()
    count = 0
    batch_count = 0

    for item in items:
        url = item.get("url")
        category = item.get("category")

        if not url or not category:
            continue

        document_id = make_id(category, url)
        document_ref = collection.document(document_id)

        old_snapshot = document_ref.get()
        old_data = old_snapshot.to_dict() if old_snapshot.exists else {}

        first_seen = get_first_seen(old_data)

        data = dict(item)
        data["first_seen_at"] = first_seen
        data["updated_at"] = firestore.SERVER_TIMESTAMP
        data["fetched_at"] = firestore.SERVER_TIMESTAMP

        batch.set(document_ref, data, merge=True)

        count += 1
        batch_count += 1

        if batch_count >= BATCH_SIZE:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count:
        batch.commit()

    logging.info("%s uploaded: %s", collection_name, count)


# =========================================================
# AUTOMATIC DELETE
# =========================================================

def expiry_date_for_document(data, collection_name):
    """
    Return the date after which this document expires.
    """

    first_seen = firestore_datetime(data.get("first_seen_at"))

    # For older documents that don't have first_seen_at,
    # start their 30-day grace period from this cleanup run.
    if not first_seen:
        first_seen = NOW

    if collection_name == "job_updates":
        last_date = firestore_datetime(data.get("last_date"))

        if last_date:
            return last_date

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "news_updates":
        published_at = firestore_datetime(data.get("published_at"))

        if published_at:
            return published_at + timedelta(days=MAX_AGE_DAYS)

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "exam_updates":
        exam_date = (
            firestore_datetime(data.get("exam_date"))
            or firestore_datetime(data.get("event_date"))
            or firestore_datetime(data.get("last_date"))
        )

        if exam_date:
            return exam_date

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "board_updates":
        published_at = firestore_datetime(data.get("published_at"))

        if published_at:
            return published_at + timedelta(days=MAX_AGE_DAYS)

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    return None


def delete_expired_documents(db):
    """
    Delete expired documents only.
    This does NOT delete documents merely because a website
    failed or an item was missing from the latest scrape.
    """
    collections = [
        "job_updates",
        "exam_updates",
        "news_updates",
        "board_updates"
    ]

    total_deleted = 0

    for collection_name in collections:
        logging.info("Checking expired documents: %s", collection_name)

        collection = db.collection(collection_name)
        snapshots = collection.stream()

        batch = db.batch()
        batch_count = 0
        deleted_here = 0

        for snapshot in snapshots:
            data = snapshot.to_dict() or {}

            expiry = expiry_date_for_document(data, collection_name)

            if not expiry or expiry > NOW:
                # Add first_seen_at to old records so their
                # 30-day grace period does not restart next run.
                if not data.get("first_seen_at"):
                    batch.set(
                        snapshot.reference,
                        {"first_seen_at": NOW},
                        merge=True
                    )
                    batch_count += 1

                if batch_count >= BATCH_SIZE:
                    batch.commit()
                    batch = db.batch()
                    batch_count = 0

                continue

            batch.delete(snapshot.reference)
            batch_count += 1
            deleted_here += 1

            if batch_count >= BATCH_SIZE:
                batch.commit()
                batch = db.batch()
                batch_count = 0

        if batch_count:
            batch.commit()

        total_deleted += deleted_here
        logging.info(
            "%s deleted from %s",
            deleted_here,
            collection_name
        )

    logging.info("Total expired documents deleted: %s", total_deleted)


# =========================================================
# MAIN
# =========================================================

def main():
    logging.info("Bihar EduAlert update checker started")

    source_status = {}

    board_updates = get_board_updates(source_status)

    job_updates, exam_updates = get_job_and_exam_updates(
        source_status
    )

    news_updates = get_news_updates(source_status)

    board_updates = unique_updates(board_updates)
    job_updates = unique_updates(job_updates)
    exam_updates = unique_updates(exam_updates)
    news_updates = unique_updates(news_updates)

    all_updates = (
        board_updates
        + job_updates
        + exam_updates
        + news_updates
    )

    output = {
        "checked_at": NOW.isoformat(),
        "total_updates": len(all_updates),
        "board_updates": board_updates,
        "job_updates": job_updates,
        "exam_updates": exam_updates,
        "news_updates": news_updates,
        "source_status": source_status,
        "updates": all_updates
    }

    with open("updates.json", "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    logging.info("JSON created: updates.json")
    logging.info("Board updates: %s", len(board_updates))
    logging.info("Job updates: %s", len(job_updates))
    logging.info("Exam updates: %s", len(exam_updates))
    logging.info("News updates: %s", len(news_updates))

    db = connect_firebase()

    if db:
        try:
            upload_collection(db, "board_updates", board_updates)
            upload_collection(db, "job_updates", job_updates)
            upload_collection(db, "exam_updates", exam_updates)
            upload_collection(db, "news_updates", news_updates)

            # Run expiry cleanup after uploads.
            delete_expired_documents(db)

            logging.info("Firebase upload and cleanup completed.")

        except Exception as error:
            logging.exception(
                "Firebase upload/cleanup failed: %s",
                error
            )
            raise

    logging.info("Bihar EduAlert update checker finished.")


if __name__ == "__main__":
    main()
