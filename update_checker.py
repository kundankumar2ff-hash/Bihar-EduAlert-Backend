import os
import re
import json
import time
import hashlib
import logging
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

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

REQUEST_TIMEOUT = 20
MAX_AGE_DAYS = 45
MAX_LINKS_PER_SOURCE = 100
MAX_DETAIL_PAGES_PER_SOURCE = 20

OUTPUT_FILE = "updates.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-US;q=0.8,en;q=0.7",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
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
        "name": "CBSE",
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
        "urls": [
            "https://csbc.bihar.gov.in/",
        ],
    },
    {
        "name": "Bihar Police BPSSC",
        "category": "Police",
        "urls": [
            "https://bpssc.bihar.gov.in/",
        ],
    },
    {
        "name": "BPSC",
        "category": "BPSC",
        "urls": [
            "https://bpsc.bihar.gov.in/",
        ],
    },
    {
        "name": "BSSC",
        "category": "Teacher",
        "urls": [
            "https://bssc.bihar.gov.in/",
        ],
    },
    {
        "name": "BTSC",
        "category": "Teacher",
        "urls": [
            "https://btsc.bihar.gov.in/",
        ],
    },
    {
        "name": "Railway",
        "category": "Railway",
        "urls": [
            "https://www.rrbcdg.gov.in/",
        ],
    },
    {
        "name": "SSC",
        "category": "SSC",
        "urls": [
            "https://ssc.gov.in/",
        ],
    },
    {
        "name": "Indian Army",
        "category": "Army",
        "urls": [
            "https://joinindianarmy.nic.in/",
        ],
    },
    {
        "name": "Indian Navy",
        "category": "Defence",
        "urls": [
            "https://www.joinindiannavy.gov.in/",
        ],
    },
    {
        "name": "Indian Air Force",
        "category": "Defence",
        "urls": [
            "https://afcat.cdac.in/",
        ],
    },
    {
        "name": "India Post",
        "category": "Central Government",
        "urls": [
            "https://indiapost.gov.in/",
        ],
    },
]


RSS_SOURCES = [
    {
        "name": "Amar Ujala Bihar News",
        "category": "Bihar News",
        "url": (
            "https://www.amarujala.com/rss/"
            "bihar.xml"
        ),
        "type": "news",
    },
    {
        "name": "Prabhat Khabar Bihar",
        "category": "Bihar News",
        "url": (
            "https://www.prabhatkhabar.com/"
            "feed"
        ),
        "type": "news",
    },
    {
        "name": "Live Hindustan Bihar",
        "category": "Bihar News",
        "url": (
            "https://www.livehindustan.com/"
            "feed"
        ),
        "type": "news",
    },
    {
        "name": "Live Hindustan Education",
        "category": "Education News",
        "url": (
            "https://www.livehindustan.com/"
            "feed"
        ),
        "type": "education",
    },
]


# =========================================================
# KEYWORDS
# =========================================================

JOB_KEYWORDS = [
    "recruitment", "vacancy", "job", "online apply",
    "application form", "notification", "admit card",
    "result", "answer key", "selection", "भर्ती",
    "नियुक्ति", "रिक्ति", "वैकेंसी", "आवेदन",
    "प्रवेश पत्र", "एडमिट कार्ड", "रिजल्ट",
    "परीक्षा", "उत्तर कुंजी", "चयन सूची",
]

EXAM_KEYWORDS = [
    "exam", "examination", "admit card",
    "date sheet", "time table", "result",
    "answer key", "syllabus", "model paper",
    "परीक्षा", "प्रवेश पत्र", "समय सारणी",
    "रूटीन", "परिणाम", "उत्तर कुंजी",
    "पाठ्यक्रम", "मॉडल पेपर",
]

BOARD_KEYWORDS = [
    "bihar board", "bseb", "cbse",
    "class 10", "class 12", "intermediate",
    "matric", "academic", "school",
    "बिहार बोर्ड", "सीबीएसई", "मैट्रिक",
    "इंटर", "कक्षा 10", "कक्षा 12",
]

CATEGORY_KEYWORDS = {
    "Police": [
        "police", "constable", "daroga",
        "sub inspector", "si", "सिपाही",
        "पुलिस", "दरोगा",
    ],
    "Army": [
        "army", "agniveer", "soldier",
        "अग्निवीर", "सेना",
    ],
    "Railway": [
        "railway", "rrb", "ntpc", "group d",
        "रेलवे",
    ],
    "SSC": [
        "ssc", "cgl", "chsl", "mts", "gd constable",
    ],
    "Bank": [
        "bank", "ibps", "sbi", "rbi", "बैंक",
    ],
    "BPSC": [
        "bpsc", "बिहार लोक सेवा",
    ],
    "Teacher": [
        "teacher", "tet", "ctet", "stet",
        "शिक्षक", "अध्यापक", "bssc", "btsc",
    ],
    "Defence": [
        "navy", "air force", "afcat",
        "coast guard", "defence", "रक्षा",
    ],
}


# =========================================================
# BASIC HELPERS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def clean_text(value):
    if not value:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_url(url, base_url=None):
    if not url:
        return ""

    url = url.strip()

    if base_url:
        url = urljoin(base_url, url)

    url, _ = urldefrag(url)

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return ""

    if not parsed.netloc:
        return ""

    return url


def make_id(url, title=""):
    raw = (url or "") + "|" + (title or "")
    return hashlib.sha256(
        raw.encode("utf-8", errors="ignore")
    ).hexdigest()


def contains_keyword(text, keywords):
    text = (text or "").lower()

    return any(
        keyword.lower() in text
        for keyword in keywords
    )


def parse_date(text):
    if not text:
        return None

    text = clean_text(text)

    patterns = [
        r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b",
        r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            values = match.groups()

            if len(values[0]) == 4:
                year, month, day = map(int, values)
            else:
                day, month, year = map(int, values)

            return datetime(
                year, month, day,
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    return None


def find_date_near_labels(text, labels):
    text = clean_text(text)

    for label in labels:
        pattern = (
            re.escape(label)
            + r"[\s:：\-]{0,30}"
            + r"(\d{1,2}[-/.]\d{1,2}[-/.]20\d{2}"
            + r"|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})"
        )

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            return parse_date(match.group(1))

    return None


def find_deadline(text):
    labels = [
        "last date",
        "closing date",
        "apply before",
        "application last date",
        "last date to apply",
        "अंतिम तिथि",
        "अंतिम तारीख",
        "आवेदन की अंतिम तिथि",
        "आवेदन की अंतिम तारीख",
    ]

    return find_date_near_labels(text, labels)


def find_published_date(text):
    labels = [
        "published on",
        "date of publication",
        "release date",
        "जारी तिथि",
        "प्रकाशित",
        "दिनांक",
    ]

    return find_date_near_labels(text, labels)


def is_recent(date_value):
    if not date_value:
        return True

    return (
        utc_now() - date_value
    ) <= timedelta(days=MAX_AGE_DAYS)


def classify_update(title, description, source_category=""):
    text = (
        (title or "") + " " +
        (description or "")
    ).lower()

    if contains_keyword(text, BOARD_KEYWORDS):
        return "board"

    if contains_keyword(text, EXAM_KEYWORDS):
        return "exam"

    if contains_keyword(text, JOB_KEYWORDS):
        return "job"

    if source_category in ("Bihar News", "Education News"):
        return "news"

    if source_category:
        return "job"

    return "other"


def infer_category(text, source_category=""):
    text = (text or "").lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if contains_keyword(text, keywords):
            return category

    if source_category:
        return source_category

    return "General"


def get_source_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def make_update(
    title,
    description,
    url,
    source_name,
    source_category="",
    update_type="",
    published_at=None,
    last_date=None
):
    title = clean_text(title)
    description = clean_text(description)
    url = normalize_url(url)

    if not title or not url:
        return None

    if len(title) < 5:
        return None

    if not update_type:
        update_type = classify_update(
            title,
            description,
            source_category
        )

    category = infer_category(
        title + " " + description,
        source_category
    )

    if update_type == "news":
        category = source_category or "Bihar News"

    return {
        "id": make_id(url, title),
        "title": title[:300],
        "description": description[:3000],
        "url": url,
        "source": source_name,
        "category": category,
        "type": update_type,
        "published_at": (
            published_at.isoformat()
            if published_at else None
        ),
        "last_date": (
            last_date.isoformat()
            if last_date else None
        ),
        "created_at": iso_now(),
        "source_domain": get_source_domain(url),
    }


# =========================================================
# HTTP REQUESTS
# =========================================================

def get_page(url):
    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()

        return response

    except requests.RequestException as error:
        logging.warning(
            "Request failed: %s | %s",
            url,
            error
        )

        return None


def get_soup(url):
    response = get_page(url)

    if response is None:
        return None

    try:
        return BeautifulSoup(
            response.text,
            "html.parser"
        )
    except Exception as error:
        logging.warning(
            "HTML parsing failed: %s",
            error
        )

        return None


# =========================================================
# HTML LINK EXTRACTION
# =========================================================

def extract_links(url, soup):
    results = []
    seen = set()

    if soup is None:
        return results

    for anchor in soup.find_all("a", href=True):
        href = normalize_url(
            anchor.get("href"),
            url
        )

        if not href or href in seen:
            continue

        seen.add(href)

        title = clean_text(
            anchor.get_text(" ", strip=True)
        )

        if not title:
            title = clean_text(
                anchor.get("title", "")
            )

        if not title:
            continue

        if len(title) < 5:
            continue

        results.append({
            "title": title,
            "url": href,
        })

        if len(results) >= MAX_LINKS_PER_SOURCE:
            break

    return results


def is_useful_link(title, url, source_category=""):
    text = (
        (title or "") + " " +
        (url or "")
    ).lower()

    bad_words = [
        "login", "logout", "register",
        "privacy", "contact", "about us",
        "facebook", "instagram", "youtube",
        "twitter", "whatsapp",
    ]

    if contains_keyword(text, bad_words):
        return False

    good_words = (
        JOB_KEYWORDS +
        EXAM_KEYWORDS +
        BOARD_KEYWORDS
    )

    if source_category in ("Bihar News", "Education News"):
        return len(title or "") >= 12

    return contains_keyword(text, good_words)


# =========================================================
# DETAIL PAGE EXTRACTION
# =========================================================

def extract_detail(url):
    soup = get_soup(url)

    if soup is None:
        return "", None, None

    for element in soup([
        "script", "style", "nav",
        "footer", "header", "noscript"
    ]):
        element.decompose()

    title = ""

    if soup.title:
        title = clean_text(
            soup.title.get_text(" ", strip=True)
        )

    for tag in [
        soup.find("h1"),
        soup.find("h2"),
    ]:
        if tag:
            candidate = clean_text(
                tag.get_text(" ", strip=True)
            )

            if len(candidate) > len(title):
                title = candidate

    description = ""

    article = soup.find("article")

    if article:
        description = clean_text(
            article.get_text(" ", strip=True)
        )

    if not description:
        main = soup.find("main")

        if main:
            description = clean_text(
                main.get_text(" ", strip=True)
            )

    if not description:
        body = soup.find("body")

        if body:
            description = clean_text(
                body.get_text(" ", strip=True)
            )

    if len(description) > 12000:
        description = description[:12000]

    published_at = find_published_date(
        description
    )

    last_date = find_deadline(
        description
    )

    return description, published_at, last_date


# =========================================================
# HTML SOURCE SCRAPER
# =========================================================

def scrape_html_source(source, source_type):
    updates = []

    source_name = source["name"]
    source_category = source.get(
        "category", ""
    )

    for base_url in source.get("urls", []):
        logging.info(
            "Checking %s",
            base_url
        )

        soup = get_soup(base_url)

        if soup is None:
            continue

        links = extract_links(
            base_url,
            soup
        )

        logging.info(
            "%s links found: %s",
            source_name,
            len(links)
        )

        candidates = []

        for link in links:
            if is_useful_link(
                link["title"],
                link["url"],
                source_category
            ):
                candidates.append(link)

        candidates = candidates[
            :MAX_DETAIL_PAGES_PER_SOURCE
        ]

        for link in candidates:
            description, published_at, last_date = (
                extract_detail(link["url"])
            )

            title = link["title"]

            if not description:
                description = title

            update_type = classify_update(
                title,
                description,
                source_category
            )

            if source_type == "board":
                update_type = "board"

            if source_type == "job":
                if update_type not in ("job", "exam"):
                    update_type = "job"

            if source_type == "news":
                update_type = "news"

            date_for_age = (
                published_at or last_date
            )

            if date_for_age and not is_recent(
                date_for_age
            ):
                continue

            item = make_update(
                title=title,
                description=description,
                url=link["url"],
                source_name=source_name,
                source_category=source_category,
                update_type=update_type,
                published_at=published_at,
                last_date=last_date,
            )

            if item:
                updates.append(item)

            time.sleep(0.15)

    return updates
    # =========================================================
# RSS / ATOM HELPERS
# =========================================================

def xml_local_name(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[1].lower()

    return tag.lower()


def xml_child_text(element, names):
    names = [name.lower() for name in names]

    for child in element.iter():
        if xml_local_name(child.tag) in names:
            value = clean_text(
                "".join(child.itertext())
            )

            if value:
                return value

    return ""


def xml_entry_link(entry, feed_url):
    """
    RSS और Atom दोनों में सही article URL खोजने की कोशिश।
    Atom में self लिंक को article लिंक नहीं मानता।
    """

    for child in entry:
        if xml_local_name(child.tag) != "link":
            continue

        href = child.attrib.get("href", "")
        rel = child.attrib.get("rel", "alternate")
        link_type = child.attrib.get("type", "")

        if not href:
            continue

        if rel not in ("alternate", ""):
            continue

        if link_type and "html" not in link_type.lower():
            continue

        return normalize_url(href, feed_url)

    for child in entry:
        if xml_local_name(child.tag) == "link":
            value = clean_text(
                "".join(child.itertext())
            )

            if value.startswith("http"):
                return normalize_url(
                    value,
                    feed_url
                )

    return ""


def parse_xml_date(text):
    if not text:
        return None

    text = clean_text(text)

    try:
        from email.utils import parsedate_to_datetime

        value = parsedate_to_datetime(text)

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        value = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(timezone.utc)

    except Exception:
        return parse_date(text)


# =========================================================
# RSS / ATOM SCRAPER
# =========================================================

def scrape_rss_source(source):
    updates = []

    feed_url = source["url"]
    source_name = source["name"]
    source_category = source.get(
        "category", "Bihar News"
    )

    logging.info(
        "Checking RSS: %s",
        feed_url
    )

    response = get_page(feed_url)

    if response is None:
        return updates

    try:
        root = ET.fromstring(
            response.content
        )

    except ET.ParseError as error:
        logging.warning(
            "RSS XML error: %s | %s",
            feed_url,
            error
        )

        return updates

    entries = []

    for element in root.iter():
        name = xml_local_name(element.tag)

        if name in ("item", "entry"):
            entries.append(element)

    for entry in entries[:100]:
        title = xml_child_text(
            entry,
            ["title"]
        )

        description = xml_child_text(
            entry,
            [
                "description",
                "summary",
                "encoded",
                "content",
            ]
        )

        url = xml_entry_link(
            entry,
            feed_url
        )

        date_text = xml_child_text(
            entry,
            [
                "pubdate",
                "published",
                "updated",
                "date",
            ]
        )

        published_at = parse_xml_date(
            date_text
        )

        if not title or not url:
            continue

        if published_at and not is_recent(
            published_at
        ):
            continue

        if not description:
            description = title

        item = make_update(
            title=title,
            description=description,
            url=url,
            source_name=source_name,
            source_category=source_category,
            update_type="news",
            published_at=published_at,
            last_date=None,
        )

        if item:
            updates.append(item)

    logging.info(
        "%s RSS updates: %s",
        source_name,
        len(updates)
    )

    return updates


# =========================================================
# COLLECT ALL UPDATES
# =========================================================

def collect_all_updates():
    all_updates = []

    logging.info("=" * 55)
    logging.info("Starting Bihar EduAlert update checker")
    logging.info("=" * 55)

    # Board updates
    for source in BOARD_SOURCES:
        try:
            results = scrape_html_source(
                source,
                "board"
            )

            all_updates.extend(results)

        except Exception:
            logging.exception(
                "Board source failed: %s",
                source.get("name")
            )

    # Job and exam updates
    for source in JOB_SOURCES:
        try:
            results = scrape_html_source(
                source,
                "job"
            )

            all_updates.extend(results)

        except Exception:
            logging.exception(
                "Job source failed: %s",
                source.get("name")
            )

    # News RSS updates
    for source in RSS_SOURCES:
        try:
            results = scrape_rss_source(
                source
            )

            all_updates.extend(results)

        except Exception:
            logging.exception(
                "RSS source failed: %s",
                source.get("name")
            )

    return remove_duplicates(
        all_updates
    )


# =========================================================
# REMOVE DUPLICATES
# =========================================================

def remove_duplicates(items):
    unique = {}
    seen_urls = set()

    for item in items:
        if not item:
            continue

        url = normalize_url(
            item.get("url", "")
        )

        if not url:
            continue

        item_id = item.get("id") or make_id(
            url,
            item.get("title", "")
        )

        if item_id in unique:
            continue

        if url in seen_urls:
            continue

        item["id"] = item_id

        unique[item_id] = item
        seen_urls.add(url)

    result = list(unique.values())

    logging.info(
        "Unique updates: %s",
        len(result)
    )

    return result


# =========================================================
# GROUP UPDATES
# =========================================================

def group_updates(items):
    grouped = {
        "board_updates": [],
        "job_updates": [],
        "exam_updates": [],
        "news_updates": [],
    }

    for item in items:
        update_type = item.get(
            "type", "other"
        )

        if update_type == "board":
            grouped["board_updates"].append(
                item
            )

        elif update_type == "job":
            grouped["job_updates"].append(
                item
            )

        elif update_type == "exam":
            grouped["exam_updates"].append(
                item
            )

        elif update_type == "news":
            grouped["news_updates"].append(
                item
            )

    return grouped


# =========================================================
# FIREBASE INITIALIZATION
# =========================================================

def initialize_firebase():
    if firebase_admin is None:
        logging.error(
            "firebase-admin package is missing"
        )
        return None

    service_account_json = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT"
    )

    if not service_account_json:
        logging.error(
            "FIREBASE_SERVICE_ACCOUNT secret not found"
        )
        return None

    try:
        service_account_info = json.loads(
            service_account_json
        )

        if not firebase_admin._apps:
            credential = credentials.Certificate(
                service_account_info
            )

            firebase_admin.initialize_app(
                credential
            )

        return firestore.client()

    except Exception:
        logging.exception(
            "Firebase initialization failed"
        )

        return None


# =========================================================
# FIRESTORE UPLOAD
# =========================================================

def upload_to_firestore(db, grouped):
    if db is None:
        logging.warning(
            "Firebase not connected. Skipping upload."
        )
        return False

    total_uploaded = 0

    for collection_name, items in grouped.items():
        if not items:
            logging.info(
                "%s: no new updates",
                collection_name
            )
            continue

        logging.info(
            "Uploading %s items to %s",
            len(items),
            collection_name
        )

        try:
            # Firestore batch limit is 500.
            # 400 keeps a safety margin.
            for start in range(0, len(items), 400):
                batch_items = items[
                    start:start + 400
                ]

                batch = db.batch()

                for item in batch_items:
                    document_id = item["id"]

                    reference = db.collection(
                        collection_name
                    ).document(document_id)

                    # Keep the first-seen date when
                    # an existing document is updated.
                    old_doc = reference.get()

                    old_data = (
                        old_doc.to_dict()
                        if old_doc.exists else {}
                    )

                    first_seen_at = (
                        old_data.get("first_seen_at")
                        or item.get("created_at")
                        or iso_now()
                    )

                    data = dict(item)

                    data["first_seen_at"] = (
                        first_seen_at
                    )

                    data["last_checked_at"] = (
                        iso_now()
                    )

                    data["active"] = True

                    batch.set(
                        reference,
                        data,
                        merge=True
                    )

                batch.commit()

                total_uploaded += len(
                    batch_items
                )

            logging.info(
                "Uploaded successfully: %s",
                collection_name
            )

        except Exception:
            logging.exception(
                "Firestore upload failed: %s",
                collection_name
            )

    logging.info(
        "Total Firestore documents processed: %s",
        total_uploaded
    )

    return True


# =========================================================
# SAVE JSON OUTPUT
# =========================================================

def save_json(items, grouped):
    output = {
        "app": APP_NAME,
        "generated_at": iso_now(),
        "total_updates": len(items),
        "counts": {
            name: len(values)
            for name, values in grouped.items()
        },
        "updates": items,
        **grouped,
    }

    try:
        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2
            )

        logging.info(
            "JSON saved: %s",
            OUTPUT_FILE
        )

        return True

    except Exception:
        logging.exception(
            "Could not save updates.json"
        )

        return False


# =========================================================
# MAIN FUNCTION
# =========================================================

def main():
    start_time = time.time()

    logging.info("=" * 55)
    logging.info("BIHAR EDU ALERT AUTOMATIC UPDATES")
    logging.info("=" * 55)

    # Step 1: Scrape all websites and RSS feeds.
    all_updates = collect_all_updates()

    # Step 2: Group by collection.
    grouped = group_updates(
        all_updates
    )

    # Step 3: Save JSON artifact.
    save_json(
        all_updates,
        grouped
    )

    # Step 4: Connect Firebase.
    db = initialize_firebase()

    # Step 5: Upload updates.
    if db is not None:
        upload_to_firestore(
            db,
            grouped
        )
    else:
        logging.error(
            "Firebase upload skipped."
        )

    # Step 6: Print summary.
    elapsed = round(
        time.time() - start_time,
        2
    )

    logging.info("=" * 55)
    logging.info("UPDATE CHECKER FINISHED")
    logging.info("=" * 55)

    logging.info(
        "Board updates: %s",
        len(grouped["board_updates"])
    )

    logging.info(
        "Job updates: %s",
        len(grouped["job_updates"])
    )

    logging.info(
        "Exam updates: %s",
        len(grouped["exam_updates"])
    )

    logging.info(
        "News updates: %s",
        len(grouped["news_updates"])
    )

    logging.info(
        "Total updates: %s",
        len(all_updates)
    )

    logging.info(
        "Time taken: %s seconds",
        elapsed
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
