import os
import re
import json
import hashlib
import logging
import time
import html
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urldefrag, quote
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from io import BytesIO

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None


# =========================================================
# CONFIGURATION
# =========================================================

APP_NAME = "Bihar EduAlert"
OUTPUT_FILE = "updates.json"

REQUEST_TIMEOUT = 25
MAX_AGE_DAYS = 90
MAX_LINKS_PER_SOURCE = 150
MAX_PDF_SIZE = 8 * 1024 * 1024
MAX_PDF_PAGES = 8
MAX_RSS_ENTRIES = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml,"
        "application/rss+xml,application/pdf,*/*"
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

session = requests.Session()
session.headers.update(HEADERS)


# =========================================================
# OFFICIAL BOARD SOURCES
# =========================================================

BOARD_SOURCES = [
    {
        "name": "Bihar Board",
        "category": "Bihar Board",
        "urls": [
            "https://www.biharboardonline.org/home",
            "https://exam.biharboardonline.org/",
            "https://intermediate.biharboardonline.com/",
        ],
    },
    {
        "name": "CBSE Board",
        "category": "CBSE Board",
        "urls": [
            "https://www.cbse.gov.in/cbsenew/examination_Circular.html",
            "https://cbseacademic.nic.in/",
        ],
    },
]


# =========================================================
# OFFICIAL JOB SOURCES
# =========================================================

JOB_SOURCES = [
    {
        "name": "Bihar Police CSBC",
        "category": "Police",
        "urls": ["https://csbc.bihar.gov.in/"],
    },
    {
        "name": "Bihar Police BPSSC",
        "category": "Police",
        "urls": ["https://bpssc.bihar.gov.in/"],
    },
    {
        "name": "BPSC",
        "category": "BPSC",
        "urls": ["https://bpsc.bihar.gov.in/"],
    },
    {
        "name": "BSSC",
        "category": "BSSC",
        "urls": ["https://bssc.bihar.gov.in/"],
    },
    {
        "name": "BTSC",
        "category": "BTSC",
        "urls": ["https://btsc.bihar.gov.in/"],
    },
    {
        "name": "Railway Recruitment Board",
        "category": "Railway",
        "urls": [
            "https://rrb.indianrailways.gov.in/",
            "https://www.rrbapply.gov.in/",
        ],
    },
    {
        "name": "SSC",
        "category": "SSC",
        "urls": ["https://ssc.gov.in/"],
    },
    {
        "name": "Indian Army",
        "category": "Army",
        "urls": ["https://joinindianarmy.nic.in/"],
    },
    {
        "name": "Indian Navy",
        "category": "Navy",
        "urls": ["https://www.joinindiannavy.gov.in/"],
    },
    {
        "name": "Indian Air Force",
        "category": "Air Force",
        "urls": ["https://afcat.cdac.in/"],
    },
    {
        "name": "India Post",
        "category": "Central Government",
        "urls": ["https://indiapost.gov.in/"],
    },
]


# =========================================================
# RSS NEWS SOURCES
# =========================================================

RSS_SOURCES = [
    {
        "name": "Amar Ujala Bihar",
        "category": "Bihar News",
        "url": "https://www.amarujala.com/rss/bihar.xml",
    },
    {
        "name": "Prabhat Khabar",
        "category": "Bihar News",
        "url": "https://www.prabhatkhabar.com/feed",
    },
    {
        "name": "Live Hindustan",
        "category": "Bihar News",
        "url": "https://www.livehindustan.com/rss/bihar.xml",
    },
]


# =========================================================
# KEYWORDS
# =========================================================

JOB_WORDS = [
    "recruitment", "vacancy", "online application", "apply online",
    "appointment", "भर्ती", "नियुक्ति", "रिक्ति", "आवेदन",
    "रोजगार", "नौकरी", "पदों पर",
]

ADMIT_WORDS = [
    "admit card", "e-admit card", "hall ticket",
    "प्रवेश पत्र", "एडमिट कार्ड",
]

RESULT_WORDS = [
    "result", "merit list", "selection list", "final result",
    "परिणाम", "रिजल्ट", "मेधा सूची", "चयन सूची",
]

EXAM_WORDS = [
    "exam date", "examination", "exam schedule", "time table",
    "answer key", "exam calendar", "परीक्षा", "उत्तर कुंजी",
    "परीक्षा तिथि", "समय सारणी",
]

BOARD_WORDS = [
    "bihar board", "bseb", "intermediate", "matric",
    "cbse", "class 10", "class 12", "class x", "class xii",
    "syllabus", "sample paper", "model paper", "पाठ्यक्रम",
    "मैट्रिक", "इंटर", "मॉडल पेपर",
]

LAST_DATE_PATTERNS = [
    r"(?:last date|closing date|date of closure|अंतिम तिथि)"
    r"\s*[:\-]?\s*([0-3]?\d[./\-][01]?\d[./\-](?:20)?\d{2})",
    r"(?:last date|closing date|अंतिम तिथि)"
    r"\s*[:\-]?\s*((?:\d{1,2}\s+)?"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4})",
]


# =========================================================
# DATE HELPERS
# =========================================================

def parse_date(value):
    if not value:
        return None

    value = html.unescape(str(value)).strip()

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue

    return None


def extract_date_from_text(text):
    if not text:
        return None

    patterns = [
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)\s+\d{4}\b",
        r"\b(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result = parse_date(match.group(0))
            if result:
                return result

    return None


def extract_last_date(text):
    if not text:
        return None

    for pattern in LAST_DATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result = parse_date(match.group(1))
            if result:
                return result

    return None


def is_recent(published_at):
    if not published_at:
        # Date missing: retain the update rather than silently discard it.
        return True

    return published_at >= datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)


# =========================================================
# URL HELPERS
# =========================================================

def clean_url(url, base_url):
    if not url:
        return None

    url = html.unescape(str(url)).strip()
    url = url.replace(" ", "%20")

    try:
        url = urljoin(base_url, url)
        url, _ = urldefrag(url)
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return None

        if not parsed.netloc:
            return None

        return url
    except Exception:
        return None


def make_id(url, title):
    raw = (url or "") + "|" + (title or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =========================================================
# CLASSIFICATION
# =========================================================

def contains_any(text, words):
    text = (text or "").lower()
    return any(word.lower() in text for word in words)


def classify_update(title, summary, source_category):
    text = f"{title} {summary}".lower()

    if contains_any(text, ADMIT_WORDS):
        return "Admit Card"

    if contains_any(text, RESULT_WORDS):
        return "Result"

    if contains_any(text, EXAM_WORDS):
        return "Exam Update"

    if contains_any(text, JOB_WORDS):
        return source_category

    if contains_any(text, BOARD_WORDS):
        if "cbse" in text:
            return "CBSE Board"
        if "bihar" in text or "bseb" in text or "matric" in text:
            return "Bihar Board"
        return source_category

    return source_category


# =========================================================
# HTTP FETCH
# =========================================================

def fetch_url(url):
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError as exc:
        logging.error("SSL error: %s | %s", url, exc)
    except requests.exceptions.Timeout:
        logging.error("Timeout: %s", url)
    except requests.exceptions.RequestException as exc:
        logging.error("Request failed: %s | %s", url, exc)
    except Exception as exc:
        logging.error("Unexpected fetch error: %s | %s", url, exc)

    return None


# =========================================================
# PDF TEXT EXTRACTION
# =========================================================

def extract_pdf_text(url):
    response = fetch_url(url)
    if not response:
        return ""

    content_type = response.headers.get("Content-Type", "").lower()
    data = response.content

    if len(data) > MAX_PDF_SIZE:
        logging.warning("PDF too large; skipped: %s", url)
        return ""

    if "pdf" not in content_type and not data.startswith(b"%PDF"):
        return ""

    try:
        reader = PdfReader(BytesIO(data))
        parts = []

        for page in reader.pages[:MAX_PDF_PAGES]:
            parts.append(page.extract_text() or "")

        return " ".join(parts).strip()
    except Exception as exc:
        logging.warning("PDF reading failed: %s | %s", url, exc)
        return ""


# =========================================================
# HTML PAGE SCRAPER
# =========================================================

def extract_page_metadata(soup):
    published = None

    selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "publish-date"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"itemprop": "datePublished"}),
    ]

    for tag_name, attrs in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            value = tag.get("content") or tag.get("datetime")
            published = parse_date(value)
            if published:
                break

    if not published:
        tag = soup.find("time")
        if tag:
            published = parse_date(
                tag.get("datetime") or tag.get_text(" ", strip=True)
            )

    return published


def scrape_html_source(source):
    source_name = source["name"]
    category = source["category"]
    results = []

    for page_url in source["urls"]:
        logging.info("Checking %s: %s", source_name, page_url)

        response = fetch_url(page_url)
        if not response:
            continue

        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type and "text" not in content_type:
            logging.warning("Not an HTML page: %s", page_url)
            continue

        soup = BeautifulSoup(response.text, "lxml")
        page_date = extract_page_metadata(soup)

        links = soup.find_all("a", href=True)
        logging.info("%s links found on %s", len(links), page_url)

        accepted = 0

        for link in links[:MAX_LINKS_PER_SOURCE]:
            title = link.get_text(" ", strip=True)
            raw_url = link.get("href")

            if not title or len(title) < 4:
                continue

            full_url = clean_url(raw_url, response.url)
            if not full_url:
                continue

            parsed = urlparse(full_url)
            if parsed.netloc == urlparse(page_url).netloc:
                pass

            lower_url = full_url.lower()
            lower_title = title.lower()

            ignored = [
                "javascript:",
                "mailto:",
                "tel:",
                "facebook.com",
                "twitter.com",
                "instagram.com",
                "youtube.com",
                "whatsapp.com",
            ]

            if any(item in lower_url for item in ignored):
                continue

            # Keep links that look like notices, documents or useful updates.
            useful_words = (
                JOB_WORDS + ADMIT_WORDS + RESULT_WORDS + EXAM_WORDS
                + BOARD_WORDS
            )

            file_link = lower_url.endswith(
                (".pdf", ".doc", ".docx", ".xls", ".xlsx")
            )

            if not file_link and not contains_any(
                lower_title, useful_words
            ):
                continue

            pdf_text = ""
            if lower_url.endswith(".pdf"):
                pdf_text = extract_pdf_text(full_url)

            combined_text = f"{title} {pdf_text}"
            published = (
                extract_date_from_text(title)
                or extract_date_from_text(pdf_text)
                or page_date
            )

            last_date = extract_last_date(combined_text)

            if not is_recent(published):
                continue

            final_category = classify_update(
                title,
                pdf_text,
                category,
            )

            results.append({
                "id": make_id(full_url, title),
                "title": title[:300],
                "category": final_category,
                "source": source_name,
                "url": full_url,
                "published_at": (
                    published.isoformat() if published else ""
                ),
                "last_date": (
                    last_date.date().isoformat() if last_date else ""
                ),
                "summary": pdf_text[:1200],
                "active": True,
            })

            accepted += 1

        logging.info(
            "%s accepted %s updates from %s",
            source_name,
            accepted,
            page_url,
        )

        time.sleep(0.5)

    return results


# =========================================================
# RSS SCRAPER
# =========================================================

def scrape_rss_source(source):
    source_name = source["name"]
    category = source["category"]
    feed_url = source["url"]

    logging.info("Checking RSS: %s", feed_url)

    response = fetch_url(feed_url)
    if not response:
        return []

    soup = BeautifulSoup(response.content, "xml")
    entries = soup.find_all(["item", "entry"])
    results = []

    for entry in entries[:MAX_RSS_ENTRIES]:
        title_tag = entry.find("title")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""

        link_tag = entry.find("link")
        link = ""

        if link_tag:
            link = link_tag.get("href") or link_tag.get_text(strip=True)

        link = clean_url(link, feed_url)

        if not title or not link:
            continue

        description_tag = entry.find(
            ["description", "summary", "content"]
        )
        summary = (
            description_tag.get_text(" ", strip=True)
            if description_tag else ""
        )

        date_tag = entry.find(
            ["pubDate", "published", "updated", "date"]
        )
        published = (
            parse_date(date_tag.get_text(" ", strip=True))
            if date_tag else None
        )

        if not is_recent(published):
            continue

        final_category = classify_update(
            title,
            summary,
            category,
        )

        results.append({
            "id": make_id(link, title),
            "title": title[:300],
            "category": final_category,
            "source": source_name,
            "url": link,
            "published_at": (
                published.isoformat() if published else ""
            ),
            "last_date": (
                extract_last_date(summary).date().isoformat()
                if extract_last_date(summary) else ""
            ),
            "summary": BeautifulSoup(
                summary, "html.parser"
            ).get_text(" ", strip=True)[:1200],
            "active": True,
        })

    logging.info("%s RSS updates: %s", source_name, len(results))
    return results


# =========================================================
# DEDUPLICATION
# =========================================================

def deduplicate_updates(updates):
    unique = {}

    for item in updates:
        key = item.get("url") or item.get("id")

        if not key:
            key = make_id(
                "",
                item.get("title", ""),
            )

        if key not in unique:
            unique[key] = item
            continue

        old = unique[key]

        # Prefer the record that has more useful information.
        old_score = (
            bool(old.get("published_at"))
            + bool(old.get("last_date"))
            + bool(old.get("summary"))
        )
        new_score = (
            bool(item.get("published_at"))
            + bool(item.get("last_date"))
            + bool(item.get("summary"))
        )

        if new_score > old_score:
            unique[key] = item

    return list(unique.values())


# =========================================================
# FIREBASE
# =========================================================

def initialize_firebase():
    if firebase_admin is None:
        logging.error("firebase-admin library is not installed.")
        return None

    service_account_json = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT"
    )

    if not service_account_json:
        logging.error("FIREBASE_SERVICE_ACCOUNT secret is missing.")
        return None

    try:
        service_account_info = json.loads(service_account_json)

        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)

        return firestore.client()

    except Exception as exc:
        logging.error("Firebase initialization failed: %s", exc)
        return None


def get_collection(category):
    category = (category or "").lower()

    if "news" in category:
        return "news_updates"

    if "board" in category:
        return "board_updates"

    if category in ("admit card", "result", "exam update"):
        return "exam_updates"

    return "job_updates"


def upload_to_firestore(updates):
    db = initialize_firebase()

    if db is None:
        logging.error("Firebase upload skipped.")
        return False

    try:
        batch = db.batch()
        count = 0
        uploaded = 0

        for item in updates:
            collection_name = get_collection(item.get("category"))
            doc_ref = db.collection(collection_name).document(
                item["id"]
            )

            data = dict(item)
            data["updated_at"] = firestore.SERVER_TIMESTAMP
            data["active"] = True

            batch.set(doc_ref, data, merge=True)
            count += 1

            if count >= 400:
                batch.commit()
                uploaded += count
                logging.info("Firebase batch uploaded: %s", count)
                batch = db.batch()
                count = 0

        if count:
            batch.commit()
            uploaded += count

        logging.info(
            "Firebase upload successful: %s records",
            uploaded,
        )
        return True

    except Exception as exc:
        logging.exception("Firebase upload failed: %s", exc)
        return False


# =========================================================
# JSON OUTPUT
# =========================================================

def save_json(updates):
    try:
        output = {
            "app": APP_NAME,
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "total_updates": len(updates),
            "updates": updates,
        }

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2,
            )

        logging.info(
            "JSON saved: %s (%s updates)",
            OUTPUT_FILE,
            len(updates),
        )
        return True

    except Exception as exc:
        logging.exception("JSON save failed: %s", exc)
        return False


# =========================================================
# MAIN
# =========================================================

def main():
    logging.info("=" * 60)
    logging.info("%s update checker started", APP_NAME)
    logging.info("=" * 60)

    all_updates = []

    # Board websites
    for source in BOARD_SOURCES:
        try:
            all_updates.extend(scrape_html_source(source))
        except Exception as exc:
            logging.exception(
                "Board source error: %s | %s",
                source["name"],
                exc,
            )

    # Recruitment websites
    for source in JOB_SOURCES:
        try:
            all_updates.extend(scrape_html_source(source))
        except Exception as exc:
            logging.exception(
                "Job source error: %s | %s",
                source["name"],
                exc,
            )

    # News RSS
    for source in RSS_SOURCES:
        try:
            all_updates.extend(scrape_rss_source(source))
        except Exception as exc:
            logging.exception(
                "RSS source error: %s | %s",
                source["name"],
                exc,
            )

    all_updates = deduplicate_updates(all_updates)

    logging.info("Total unique updates: %s", len(all_updates))

    # Always save JSON first, even if Firebase has an error.
    json_saved = save_json(all_updates)

    if not json_saved:
        raise RuntimeError("Could not save updates.json")

    # Firebase must succeed for the workflow to be considered successful.
    if not upload_to_firestore(all_updates):
        raise RuntimeError("Firebase upload failed")

    logging.info("=" * 60)
    logging.info("Bihar EduAlert update checker completed.")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
