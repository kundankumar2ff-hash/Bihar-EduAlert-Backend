import os
import re
import json
import hashlib
import logging
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from email.utils import parsedate_to_datetime
from io import BytesIO

import requests
from bs4 import BeautifulSoup

import firebase_admin
from firebase_admin import credentials, firestore

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


# =====================================================
# SETTINGS
# =====================================================

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

NOW = datetime.now(UTC)
REQUEST_TIMEOUT = 25
MAX_AGE_DAYS = 30
BATCH_SIZE = 400
MAX_LINKS_PER_SITE = 100
MAX_PDF_SIZE = 8 * 1024 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


# =====================================================
# SOURCES
# =====================================================

BOARD_SOURCES = {
    "Bihar Board": [
        "https://biharboardonline.bihar.gov.in/",
        "https://secondary.biharboardonline.com/",
        "https://results.biharboardonline.com/"
    ],
    "CBSE Board": [
        "https://www.cbse.gov.in/",
        "https://cbseacademic.nic.in/"
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
        "https://bpsc.bihar.gov.in/",
        "https://bssc.bihar.gov.in/",
        "https://btsc.bihar.gov.in/"
    ],
    "Defence": [
        "https://afcat.cdac.in/",
        "https://joinindiannavy.gov.in/",
        "https://joinindiancoastguard.cdac.in/"
    ]
}

RSS_SOURCES = {
    "Bihar News": [
        "https://www.livehindustan.com/rss/bihar.xml",
        "https://www.amarujala.com/rss/bihar.xml",
        "https://www.prabhatkhabar.com/feed"
    ],
    "Education News": [
        "https://www.livehindustan.com/rss/education.xml"
    ],
    "Job News": [
        "https://www.livehindustan.com/rss/career.xml"
    ]
}


# =====================================================
# KEYWORDS
# =====================================================

JOB_KEYWORDS = [
    "recruitment", "vacancy", "notification", "application",
    "online form", "apply online", "admit card", "result",
    "appointment", "selection list", "भर्ती", "रिक्ति",
    "विज्ञापन", "आवेदन", "ऑनलाइन फॉर्म", "प्रवेश पत्र",
    "एडमिट कार्ड", "परिणाम", "नियुक्ति"
]

EXAM_KEYWORDS = [
    "exam date", "examination", "exam schedule",
    "time table", "date sheet", "answer key",
    "exam notice", "admit card", "परीक्षा तिथि",
    "परीक्षा कार्यक्रम", "समय सारणी", "उत्तर कुंजी",
    "परीक्षा", "प्रवेश पत्र"
]

CATEGORY_KEYWORDS = {
    "Police": [
        "police", "constable", "sub inspector", "daroga",
        "bihar police", "csbc", "bpssc",
        "सिपाही", "पुलिस", "दरोगा", "आरक्षी"
    ],
    "Army": [
        "indian army", "agniveer army", "army recruitment",
        "join indian army", "भारतीय सेना", "अग्निवीर सेना",
        "थल सेना"
    ],
    "Railway": [
        "railway", "rrb", "rrc", "ntpc", "group d",
        "रेलवे", "रेल भर्ती"
    ],
    "SSC": [
        "staff selection commission", "ssc cgl",
        "ssc chsl", "ssc gd", "ssc mts",
        "स्टाफ सिलेक्शन"
    ],
    "Bank": [
        "ibps", "sbi po", "sbi clerk", "bank recruitment",
        "banking", "बैंक भर्ती", "बैंकिंग"
    ],
    "BPSC": [
        "bpsc", "bihar public service commission",
        "बिहार लोक सेवा आयोग"
    ],
    "Teacher": [
        "teacher recruitment", "school teacher",
        "bihar teacher", "tet", "tre",
        "शिक्षक भर्ती", "अध्यापक भर्ती", "शिक्षक"
    ],
    "Defence": [
        "air force", "indian navy", "navy recruitment",
        "coast guard", "afcat", "agniveer vayu",
        "agniveer navy", "वायु सेना", "नौसेना",
        "तटरक्षक"
    ]
}


# =====================================================
# TEXT AND DATE HELPERS
# =====================================================

def clean_text(value):
    if not value:
        return ""

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    value = str(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def make_id(category, url):
    raw = (category + "|" + url).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        return result.astimezone(UTC)
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
        "%b %d, %Y"
    ]

    for fmt in formats:
        try:
            result = datetime.strptime(value, fmt)
            return result.replace(tzinfo=IST).astimezone(UTC)
        except Exception:
            continue

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        return result.astimezone(UTC)
    except Exception:
        return None


def date_to_string(value):
    if not value:
        return None

    return value.astimezone(IST).strftime("%d-%m-%Y")


def find_deadline(text):
    text = clean_text(text)

    patterns = [
        r"(?:last date|closing date|apply before|अंतिम तिथि|आवेदन की अंतिम तिथि)"
        r"\s*[:\-]?\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",

        r"(?:last date|closing date|अंतिम तिथि)"
        r"\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                return parsed

    return None


def keyword_match(text, keywords):
    text = clean_text(text).lower()

    return any(
        keyword.lower() in text
        for keyword in keywords
    )


def category_match(text, category):
    text = clean_text(text).lower()

    keywords = CATEGORY_KEYWORDS.get(category, [])

    return any(
        keyword.lower() in text
        for keyword in keywords
    )


def is_job_or_exam(text):
    return (
        keyword_match(text, JOB_KEYWORDS)
        or keyword_match(text, EXAM_KEYWORDS)
    )


# =====================================================
# HTTP REQUEST
# =====================================================

def page_request(url):
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()

        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"

        return response

    except Exception as error:
        logging.warning("Page error: %s | %s", url, error)
        return None


# =====================================================
# PDF READER
# =====================================================

def read_pdf(url):
    if PdfReader is None:
        return ""

    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            stream=True
        )

        response.raise_for_status()

        content = response.content

        if len(content) > MAX_PDF_SIZE:
            logging.warning("PDF too large: %s", url)
            return ""

        reader = PdfReader(BytesIO(content))

        pages = []

        for page in reader.pages[:10]:
            pages.append(page.extract_text() or "")

        return clean_text(" ".join(pages))

    except Exception as error:
        logging.warning("PDF read error: %s | %s", url, error)
        return ""


# =====================================================
# DETAIL PAGE INFORMATION
# =====================================================

def get_detail_information(url):
    if url.lower().endswith(".pdf"):
        return read_pdf(url)

    response = page_request(url)

    if not response:
        return ""

    soup = BeautifulSoup(response.text, "lxml")

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    description = ""

    meta = soup.find(
        "meta",
        attrs={"name": re.compile("description", re.I)}
    )

    if meta:
        description = meta.get("content", "")

    body_text = soup.get_text(" ", strip=True)

    return clean_text(
        title + " " + description + " " + body_text[:12000]
    )


# =====================================================
# WEBSITE LINK SCRAPER
# =====================================================

def get_page_links(url):
    response = page_request(url)

    if not response:
        return []

    soup = BeautifulSoup(response.text, "lxml")

    results = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()

        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:")
        ):
            continue

        full_url = urljoin(url, href)

        if urlparse(full_url).scheme not in ("http", "https"):
            continue

        title = clean_text(anchor.get_text(" ", strip=True))

        parent_text = clean_text(
            anchor.parent.get_text(" ", strip=True)
            if anchor.parent else ""
        )

        if len(title) < 3 and len(parent_text) < 5:
            continue

        if full_url in seen:
            continue

        seen.add(full_url)

        results.append({
            "title": title or parent_text[:150],
            "url": full_url,
            "context": parent_text[:500]
        })

        if len(results) >= MAX_LINKS_PER_SITE:
            break

    return results


# =====================================================
# UPDATE CREATION
# =====================================================

def create_update(
    title,
    url,
    category,
    description="",
    published_at=None,
    last_date=None,
    update_type="job"
):
    title = clean_text(title)
    description = clean_text(description)

    if not title or not url:
        return None

    return {
        "id": make_id(category, url),
        "title": title[:300],
        "url": url,
        "category": category,
        "description": description[:2000],
        "published_at": published_at.isoformat() if published_at else None,
        "last_date": last_date.isoformat() if last_date else None,
        "type": update_type,
        "source": urlparse(url).netloc,
        "first_seen_at": NOW.isoformat(),
        "updated_at": NOW.isoformat()
    }


# =====================================================
# BOARD SCRAPER
# =====================================================

def scrape_boards():
    updates = []

    keywords = [
        "result", "admit card", "registration", "exam",
        "date sheet", "syllabus", "notice", "notification",
        "परिणाम", "परीक्षा", "प्रवेश पत्र", "सूचना",
        "रजिस्ट्रेशन", "पाठ्यक्रम"
    ]

    for category, sources in BOARD_SOURCES.items():
        for source in sources:
            logging.info("Checking board: %s", source)

            links = get_page_links(source)

            for item in links:
                combined = item["title"] + " " + item["context"]

                if not keyword_match(combined, keywords):
                    continue

                detail = get_detail_information(item["url"])
                full_text = combined + " " + detail

                deadline = find_deadline(full_text)

                update = create_update(
                    item["title"],
                    item["url"],
                    category,
                    description=detail[:1000],
                    last_date=deadline,
                    update_type="board"
                )

                if update:
                    updates.append(update)

    return updates


# =====================================================
# JOB AND EXAM SCRAPER
# =====================================================

def scrape_jobs():
    jobs = []
    exams = []

    for category, sources in JOB_SOURCES.items():
        for source in sources:
            logging.info(
                "Checking job category %s: %s",
                category,
                source
            )

            links = get_page_links(source)

            for item in links:
                combined = (
                    item["title"] + " " + item["context"]
                )

                # Check title and surrounding text first.
                if not category_match(combined, category):
                    # The source itself may identify the category.
                    # Detail page will be checked before rejecting.
                    pass

                detail = get_detail_information(item["url"])
                full_text = combined + " " + detail

                if not category_match(full_text, category):
                    continue

                if not is_job_or_exam(full_text):
                    continue

                deadline = find_deadline(full_text)

                is_exam = keyword_match(
                    full_text,
                    EXAM_KEYWORDS
                )

                update = create_update(
                    item["title"],
                    item["url"],
                    category,
                    description=detail[:1500],
                    last_date=deadline,
                    update_type="exam" if is_exam else "job"
                )

                if not update:
                    continue

                if is_exam:
                    exams.append(update)
                else:
                    jobs.append(update)

    return jobs, exams


# =====================================================
# RSS AND ATOM READER
# =====================================================

def get_xml_text(element, names):
    for name in names:
        found = element.find(name)

        if found is not None and found.text:
            return clean_text(found.text)

    # Namespace-independent search
    for child in element.iter():
        local_name = child.tag.split("}")[-1].lower()

        if local_name in [name.lower() for name in names]:
            if child.text:
                return clean_text(child.text)

    return ""


def get_xml_link(element):
    for child in element:
        local_name = child.tag.split("}")[-1].lower()

        if local_name == "link":
            href = child.attrib.get("href")

            if href:
                return href

            if child.text:
                return clean_text(child.text)

    return ""


def scrape_rss():
    news = []

    for category, feeds in RSS_SOURCES.items():
        for feed_url in feeds:
            logging.info("Reading RSS: %s", feed_url)

            response = page_request(feed_url)

            if not response:
                continue

            try:
                root = ET.fromstring(response.content)

            except Exception as error:
                logging.warning(
                    "RSS parse error: %s | %s",
                    feed_url,
                    error
                )
                continue

            entries = []

            for element in root.iter():
                local_name = element.tag.split("}")[-1].lower()

                if local_name in ("item", "entry"):
                    entries.append(element)

            for entry in entries:
                title = get_xml_text(entry, ["title"])
                link = get_xml_link(entry)

                description = (
                    get_xml_text(entry, ["description", "summary", "content"])
                )

                date_text = get_xml_text(
                    entry,
                    [
                        "pubDate",
                        "published",
                        "updated",
                        "date"
                    ]
                )

                if not title or not link:
                    continue

                link = urljoin(feed_url, link)

                published = parse_date(date_text)

                if published:
                    if published < NOW - timedelta(days=MAX_AGE_DAYS):
                        continue

                update = create_update(
                    title,
                    link,
                    category,
                    description=description,
                    published_at=published,
                    update_type="news"
                )

                if update:
                    news.append(update)

    return news


# =====================================================
# DUPLICATE REMOVAL
# =====================================================

def remove_duplicates(updates):
    unique = {}

    for item in updates:
        key = item.get("id")

        if key:
            unique[key] = item

    return list(unique.values())


# =====================================================
# FIREBASE CONNECTION
# =====================================================

def connect_firebase():
    if firebase_admin._apps:
        return firestore.client()

    service_account = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT"
    )

    if not service_account:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT GitHub Secret missing"
        )

    try:
        account_data = json.loads(service_account)

        credential = credentials.Certificate(account_data)

        firebase_admin.initialize_app(credential)

        logging.info("Firebase connected successfully")

        return firestore.client()

    except Exception as error:
        raise RuntimeError(
            "Firebase connection failed: " + str(error)
        )


# =====================================================
# FIRESTORE UPLOAD
# =====================================================

def upload_updates(db, collection_name, updates):
    collection = db.collection(collection_name)

    batch = db.batch()
    count = 0
    total = 0

    for item in updates:
        document_id = item["id"]
        ref = collection.document(document_id)

        existing = ref.get()

        data = dict(item)

        if existing.exists:
            old_data = existing.to_dict() or {}

            # Preserve original first_seen_at.
            if old_data.get("first_seen_at"):
                data["first_seen_at"] = old_data["first_seen_at"]

        data["updated_at"] = firestore.SERVER_TIMESTAMP

        if not existing.exists:
            data["first_seen_at"] = firestore.SERVER_TIMESTAMP

        batch.set(ref, data, merge=True)

        count += 1
        total += 1

        if count >= BATCH_SIZE:
            batch.commit()
            batch = db.batch()
            count = 0

    if count:
        batch.commit()

    logging.info(
        "%s uploaded: %s",
        collection_name,
        total
    )


# =====================================================
# AUTOMATIC CLEANUP
# =====================================================

def get_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)

        return result.astimezone(UTC)

    except Exception:
        return None


def expiry_for_document(data, collection_name):
    first_seen = get_datetime(data.get("first_seen_at"))

    if not first_seen:
        first_seen = NOW

    if collection_name == "jobs":
        last_date = get_datetime(data.get("last_date"))

        if last_date:
            # Keep the update until the end of its last date.
            return last_date + timedelta(days=1)

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "exams":
        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "news":
        published = get_datetime(data.get("published_at"))

        if published:
            return published + timedelta(days=MAX_AGE_DAYS)

        return first_seen + timedelta(days=MAX_AGE_DAYS)

    if collection_name == "board_updates":
        return first_seen + timedelta(days=MAX_AGE_DAYS)

    return first_seen + timedelta(days=MAX_AGE_DAYS)


def cleanup_old_updates(db):
    collections = [
        "jobs",
        "exams",
        "news",
        "board_updates"
    ]

    deleted_total = 0

    for collection_name in collections:
        logging.info("Cleaning collection: %s", collection_name)

        docs = db.collection(collection_name).stream()

        batch = db.batch()
        count = 0

        for doc in docs:
            data = doc.to_dict() or {}

            expiry = expiry_for_document(
                data,
                collection_name
            )

            if expiry <= NOW:
                batch.delete(doc.reference)
                count += 1
                deleted_total += 1

                if count >= BATCH_SIZE:
                    batch.commit()
                    batch = db.batch()
                    count = 0

        if count:
            batch.commit()

    logging.info(
        "Total expired updates deleted: %s",
        deleted_total
    )


# =====================================================
# SAVE JSON OUTPUT
# =====================================================

def save_json(updates):
    output = {
        "generated_at": NOW.isoformat(),
        "total": len(updates),
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

    logging.info("updates.json saved")


# =====================================================
# MAIN
# =====================================================

def main():
    logging.info("========== BIHAR EDUALERT START ==========")

    board_updates = scrape_boards()

    jobs, exams = scrape_jobs()

    news = scrape_rss()

    board_updates = remove_duplicates(board_updates)
    jobs = remove_duplicates(jobs)
    exams = remove_duplicates(exams)
    news = remove_duplicates(news)

    all_updates = (
        board_updates +
        jobs +
        exams +
        news
    )

    logging.info("Bihar/CBSE Board: %s", len(board_updates))
    logging.info("Jobs: %s", len(jobs))
    logging.info("Exams: %s", len(exams))
    logging.info("News: %s", len(news))
    logging.info("Total: %s", len(all_updates))

    save_json(all_updates)

    db = connect_firebase()

    upload_updates(
        db,
        "board_updates",
        board_updates
    )

    upload_updates(
        db,
        "jobs",
        jobs
    )

    upload_updates(
        db,
        "exams",
        exams
    )

    upload_updates(
        db,
        "news",
        news
    )

    cleanup_old_updates(db)

    logging.info("========== BIHAR EDUALERT FINISHED ==========")


if __name__ == "__main__":
    main()
