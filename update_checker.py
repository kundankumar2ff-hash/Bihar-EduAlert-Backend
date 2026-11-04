import os
import re
import json
import hashlib
import logging
import time
import html
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from email.utils import parsedate_to_datetime
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

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

HTML_TIMEOUT = 18
PDF_TIMEOUT = 12
RSS_TIMEOUT = 15
MAX_AGE_DAYS = 90
MAX_LINKS_PER_PAGE = 250
MAX_PDF_SIZE = 5 * 1024 * 1024
MAX_PDF_PAGES = 5
MAX_RSS_ENTRIES = 60
MAX_PDF_DOWNLOADS_PER_PAGE = 18
SLEEP_BETWEEN_PAGES = 0.3

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
# SOURCES
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
        # BPSC and BPSSC are different commissions/websites.
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
        # rrbapply.gov.in is an application portal, not a notices page.
        "urls": ["https://rrb.indianrailways.gov.in/"],
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
    "recruitment", "vacancy", "vacancies", "online application",
    "apply online", "application form", "appointment", "भर्ती",
    "नियुक्ति", "रिक्ति", "आवेदन", "रोजगार", "नौकरी", "पदों पर",
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
    "date sheet", "answer key", "exam calendar", "परीक्षा",
    "उत्तर कुंजी", "परीक्षा तिथि", "समय सारणी", "डेट शीट",
]

BOARD_WORDS = [
    "bihar board", "bseb", "intermediate", "matric", "cbse",
    "class 10", "class 12", "class x", "class xii", "syllabus",
    "sample paper", "model paper", "पाठ्यक्रम", "मैट्रिक",
    "इंटर", "मॉडल पेपर",
]

NOTICE_WORDS = [
    "notice", "notification", "circular", "advertisement",
    "corrigendum", "tender", "सूचना", "अधिसूचना", "विज्ञप्ति",
]

IGNORE_TITLE_WORDS = [
    "home", "homepage", "contact us", "about us", "login",
    "logout", "privacy policy", "disclaimer", "tender",
    "facebook", "twitter", "instagram", "youtube",
]

DATE_TOKEN = (
    r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+\d{4}|"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4})"
)

LAST_DATE_LABELS = (
    r"(?:last date|closing date|date of closure|last date for "
    r"submission|closing of application|अंतिम तिथि|अंतिम तारीख|"
    r"आवेदन की अंतिम तिथि)"
)


# =========================================================
# TEXT AND DATE HELPERS
# =========================================================

def clean_text(value):
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    value = re.sub(r"\s+", " ", value).strip()

    # Fix common UTF-8 text accidentally decoded as Windows-1252.
    if any(token in value for token in ("â€“", "â€”", "â€™", "Ã", "Â")):
        try:
            repaired = value.encode("cp1252").decode("utf-8")
            if repaired:
                value = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return value


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        pass

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError, OverflowError):
        pass

    formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%Y-%m-%d", "%d %B %Y", "%d %b %Y",
        "%B %d, %Y", "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def extract_date_from_text(text):
    if not text:
        return None

    match = re.search(DATE_TOKEN, text, re.IGNORECASE)
    if match:
        return parse_date(match.group(0))
    return None


def extract_last_date(text):
    if not text:
        return None

    pattern = LAST_DATE_LABELS + r"\s*(?:is|:|-|को)?\s*(" + DATE_TOKEN + r")"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return parse_date(match.group(1))
    return None


def is_recent(published_at):
    if not published_at:
        # Keep undated official notices, but label the date as unavailable.
        return True

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return published_at >= now - timedelta(days=MAX_AGE_DAYS)


def contains_any(text, words):
    text = (text or "").casefold()
    return any(word.casefold() in text for word in words)


# =========================================================
# URL HELPERS
# =========================================================

def clean_url(url, base_url):
    if not url:
        return None
    url = html.unescape(str(url)).strip()
    if url.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None

    try:
        url = urljoin(base_url, url)
        url, _ = urldefrag(url)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return url
    except Exception:
        return None


def make_id(url, title):
    raw = (url or "") + "|" + (title or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_title(title):
    title = unicodedata.normalize("NFKC", clean_text(title)).casefold()
    title = re.sub(r"[^\w\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def is_social_or_irrelevant_url(url):
    host = urlparse(url).netloc.casefold()
    ignored_hosts = (
        "facebook.com", "twitter.com", "x.com", "instagram.com",
        "youtube.com", "whatsapp.com", "linkedin.com",
    )
    return any(host == item or host.endswith("." + item) for item in ignored_hosts)


def is_document_url(url):
    path = urlparse(url).path.casefold()
    return path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx"))


def looks_useful(title, url):
    text = f"{title} {url}".casefold()
    if contains_any(title, IGNORE_TITLE_WORDS):
        return False

    keywords = JOB_WORDS + ADMIT_WORDS + RESULT_WORDS + EXAM_WORDS
    keywords += BOARD_WORDS + NOTICE_WORDS
    return is_document_url(url) or contains_any(text, keywords)


def title_from_filename(url):
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.(pdf|docx?|xlsx?)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[_-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:200]


def useful_title(title):
    title = clean_text(title)
    if len(title) < 4:
        return False
    generic = {
        "english", "hindi", "download", "click here", "view",
        "read more", "more", "pdf",
    }
    return title.casefold() not in generic


# =========================================================
# CLASSIFICATION
# =========================================================

def classify_update(title, summary, source_category):
    text = f"{title} {summary}".casefold()

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
        if any(word in text for word in ("bihar", "bseb", "matric", "intermediate")):
            return "Bihar Board"
    return source_category


# =========================================================
# HTTP FETCH
# =========================================================

def fetch_url(url, timeout=HTML_TIMEOUT, stream=False):
    try:
        response = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            stream=stream,
        )
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError as exc:
        logging.error("SSL error: %s | %s", url, exc)
    except requests.exceptions.Timeout:
        logging.warning("Timeout: %s", url)
    except requests.exceptions.RequestException as exc:
        logging.warning("Request failed: %s | %s", url, exc)
    except Exception as exc:
        logging.warning("Unexpected fetch error: %s | %s", url, exc)
    return None


# =========================================================
# PDF EXTRACTION
# =========================================================

def extract_pdf_text(url):
    response = fetch_url(url, timeout=PDF_TIMEOUT, stream=True)
    if not response:
        return ""

    try:
        content_type = response.headers.get("Content-Type", "").casefold()
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > MAX_PDF_SIZE:
            logging.info("PDF skipped (Content-Length too large): %s", url)
            return ""

        buffer = BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_PDF_SIZE:
                logging.info("PDF skipped (over size limit): %s", url)
                return ""
            buffer.write(chunk)

        data = buffer.getvalue()
        if "pdf" not in content_type and not data.startswith(b"%PDF"):
            return ""

        reader = PdfReader(BytesIO(data), strict=False)
        parts = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue

        return clean_text(" ".join(parts))[:5000]
    except Exception as exc:
        logging.warning("PDF extraction failed: %s | %s", url, exc)
        return ""
    finally:
        response.close()


# =========================================================
# HTML METADATA
# =========================================================

def extract_page_metadata(soup):
    selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "publish-date"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"itemprop": "datePublished"}),
        ("meta", {"property": "og:updated_time"}),
    ]

    for tag_name, attrs in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            value = tag.get("content") or tag.get("datetime")
            result = parse_date(value)
            if result:
                return result

    tag = soup.find("time")
    if tag:
        return parse_date(tag.get("datetime") or tag.get_text(" ", strip=True))
    return None


def extract_pdf_title(pdf_text, fallback):
    if not pdf_text:
        return fallback

    for line in pdf_text.splitlines():
        line = clean_text(line)
        if 8 <= len(line) <= 180 and not re.fullmatch(r"\d+", line):
            if line.casefold() not in {"english", "hindi", "government of india"}:
                return line
    return fallback


# =========================================================
# HTML SCRAPER
# =========================================================

def scrape_html_source(source):
    source_name = source["name"]
    category = source["category"]
    results = []

    for page_url in source["urls"]:
        logging.info("Checking %s: %s", source_name, page_url)
        response = fetch_url(page_url, timeout=HTML_TIMEOUT)
        if not response:
            continue

        try:
            content_type = response.headers.get("Content-Type", "").casefold()
            if "html" not in content_type and "text" not in content_type:
                logging.warning("Not an HTML page: %s", page_url)
                continue

            soup = BeautifulSoup(response.content, "lxml")
            page_date = extract_page_metadata(soup)
            links = soup.find_all("a", href=True)
            logging.info("%s links found on %s", len(links), page_url)

            accepted = 0
            pdf_downloads = 0
            page_seen_urls = set()

            for link in links[:MAX_LINKS_PER_PAGE]:
                raw_url = link.get("href")
                full_url = clean_url(raw_url, response.url)
                if not full_url or full_url in page_seen_urls:
                    continue
                page_seen_urls.add(full_url)

                if is_social_or_irrelevant_url(full_url):
                    continue

                title = clean_text(link.get_text(" ", strip=True))
                if not title:
                    title = title_from_filename(full_url)

                if not useful_title(title) and not is_document_url(full_url):
                    continue
                if not looks_useful(title, full_url):
                    continue

                pdf_text = ""
                if is_document_url(full_url):
                    # Limit PDF downloads so one large notice page cannot take
                    # several minutes on every scheduled run.
                    if pdf_downloads >= MAX_PDF_DOWNLOADS_PER_PAGE:
                        continue
                    pdf_downloads += 1
                    if urlparse(full_url).path.casefold().endswith(".pdf"):
                        pdf_text = extract_pdf_text(full_url)

                if title.casefold() in {"english", "hindi", "download", "pdf"}:
                    title = extract_pdf_title(pdf_text, title_from_filename(full_url))

                if not useful_title(title):
                    title = title_from_filename(full_url)
                if len(title) < 4:
                    continue

                summary = pdf_text[:1200]
                combined_text = f"{title} {summary}"

                published = (
                    extract_date_from_text(title)
                    or extract_date_from_text(pdf_text)
                    or page_date
                )
                last_date = extract_last_date(combined_text)

                if not is_recent(published):
                    continue

                final_category = classify_update(title, summary, category)

                results.append({
                    "id": make_id(full_url, title),
                    "title": title[:300],
                    "category": final_category,
                    "source": source_name,
                    "url": full_url,
                    "published_at": published.isoformat() if published else "",
                    "last_date": last_date.date().isoformat() if last_date else "",
                    "summary": summary,
                    "active": True,
                })
                accepted += 1

            logging.info(
                "%s accepted %s updates from %s (PDF checked: %s)",
                source_name, accepted, page_url, pdf_downloads,
            )
        except Exception as exc:
            logging.exception("Page scraping failed: %s | %s", page_url, exc)
        finally:
            response.close()

        time.sleep(SLEEP_BETWEEN_PAGES)

    return results


# =========================================================
# RSS SCRAPER
# =========================================================

def scrape_rss_source(source):
    feed_url = source["url"]
    source_name = source["name"]
    category = source["category"]

    logging.info("Checking RSS: %s", feed_url)
    response = fetch_url(feed_url, timeout=RSS_TIMEOUT)
    if not response:
        return []

    results = []
    try:
        soup = BeautifulSoup(response.content, "xml")
        entries = soup.find_all(["item", "entry"])

        for entry in entries[:MAX_RSS_ENTRIES]:
            title_tag = entry.find("title")
            title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else ""

            link_tag = entry.find("link")
            link = ""
            if link_tag:
                link = link_tag.get("href") or link_tag.get_text(strip=True)
            link = clean_url(link, feed_url)

            if not title or not link:
                continue

            description_tag = entry.find(["description", "summary", "content"])
            summary = (
                clean_text(description_tag.get_text(" ", strip=True))
                if description_tag else ""
            )

            date_tag = entry.find(["pubDate", "published", "updated", "date"])
            published = parse_date(date_tag.get_text(" ", strip=True)) if date_tag else None

            if not is_recent(published):
                continue

            final_category = classify_update(title, summary, category)
            last_date = extract_last_date(summary)

            results.append({
                "id": make_id(link, title),
                "title": title[:300],
                "category": final_category,
                "source": source_name,
                "url": link,
                "published_at": published.isoformat() if published else "",
                "last_date": last_date.date().isoformat() if last_date else "",
                "summary": summary[:1200],
                "active": True,
            })
    except Exception as exc:
        logging.exception("RSS parsing failed: %s | %s", feed_url, exc)
    finally:
        response.close()

    logging.info("%s RSS updates: %s", source_name, len(results))
    return results


# =========================================================
# DEDUPLICATION
# =========================================================

def deduplicate_updates(updates):
    unique_by_url = {}

    for item in updates:
        url = item.get("url", "").strip()
        key = url.casefold() if url else item.get("id", "")
        if not key:
            key = make_id("", item.get("title", ""))

        if key not in unique_by_url:
            unique_by_url[key] = item
            continue

        old = unique_by_url[key]
        old_score = sum(bool(old.get(field)) for field in (
            "published_at", "last_date", "summary"
        ))
        new_score = sum(bool(item.get(field)) for field in (
            "published_at", "last_date", "summary"
        ))
        if new_score > old_score:
            unique_by_url[key] = item

    # A second pass removes exact duplicate titles from the same source.
    # Different URLs are retained if their titles differ or source differs.
    final = []
    seen = set()
    for item in unique_by_url.values():
        key = (item.get("source", ""), normalize_title(item.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        final.append(item)

    final.sort(
        key=lambda item: item.get("published_at") or "",
        reverse=True,
    )
    return final


# =========================================================
# FIREBASE
# =========================================================

def initialize_firebase():
    if firebase_admin is None:
        logging.error("firebase-admin library is not installed.")
        return None

    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
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
        logging.exception("Firebase initialization failed: %s", exc)
        return None


def get_collection(category):
    category = (category or "").casefold()

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
        uploaded = 0
        batch = db.batch()
        batch_count = 0

        for item in updates:
            collection_name = get_collection(item.get("category"))
            doc_ref = db.collection(collection_name).document(item["id"])

            data = dict(item)
            data["updated_at"] = firestore.SERVER_TIMESTAMP
            data["active"] = True
            batch.set(doc_ref, data, merge=True)
            batch_count += 1

            if batch_count >= 400:
                batch.commit()
                uploaded += batch_count
                logging.info("Firebase batch uploaded: %s", batch_count)
                batch = db.batch()
                batch_count = 0

        if batch_count:
            batch.commit()
            uploaded += batch_count

        logging.info("Firebase upload successful: %s records", uploaded)
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_updates": len(updates),
            "updates": updates,
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(output, file, ensure_ascii=False, indent=2)

        logging.info("JSON saved: %s (%s updates)", OUTPUT_FILE, len(updates))
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

    for source in BOARD_SOURCES:
        try:
            all_updates.extend(scrape_html_source(source))
        except Exception as exc:
            logging.exception("Board source error: %s | %s", source["name"], exc)

    for source in JOB_SOURCES:
        try:
            all_updates.extend(scrape_html_source(source))
        except Exception as exc:
            logging.exception("Job source error: %s | %s", source["name"], exc)

    for source in RSS_SOURCES:
        try:
            all_updates.extend(scrape_rss_source(source))
        except Exception as exc:
            logging.exception("RSS source error: %s | %s", source["name"], exc)

    all_updates = deduplicate_updates(all_updates)
    logging.info("Total unique updates: %s", len(all_updates))

    if not save_json(all_updates):
        raise RuntimeError("Could not save updates.json")

    if not upload_to_firestore(all_updates):
        raise RuntimeError("Firebase upload failed")

    logging.info("=" * 60)
    logging.info("Bihar EduAlert update checker completed.")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
