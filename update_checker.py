import os
import re
import json
import time
import hashlib
import logging
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import (
    urljoin, urlparse, urldefrag,
    parse_qsl, urlencode, urlunparse
)

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
MAX_RSS_ENTRIES = 100

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

TRACKING_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid",
    "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content",
}


# =========================================================
# OFFICIAL SOURCES
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
        "name": "Railway",
        "category": "Railway",
        "urls": ["https://www.rrbcdg.gov.in/"],
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
        "category": "Defence",
        "urls": ["https://www.joinindiannavy.gov.in/"],
    },
    {
        "name": "Indian Air Force",
        "category": "Defence",
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
        "name": "Amar Ujala Bihar News",
        "category": "Bihar News",
        "url": "https://www.amarujala.com/rss/bihar.xml",
    },
    {
        "name": "Prabhat Khabar Bihar",
        "category": "Bihar News",
        "url": "https://www.prabhatkhabar.com/feed",
    },
    {
        "name": "Live Hindustan Bihar",
        "category": "Bihar News",
        "url": "https://www.livehindustan.com/feed",
    },
]


# =========================================================
# KEYWORDS
# =========================================================

JOB_KEYWORDS = [
    "recruitment", "vacancy", "job", "online apply",
    "application form", "notification", "admit card",
    "result", "answer key", "selection",
    "भर्ती", "नियुक्ति", "रिक्ति", "वैकेंसी",
    "आवेदन", "प्रवेश पत्र", "एडमिट कार्ड",
    "रिजल्ट", "उत्तर कुंजी", "चयन सूची",
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
        "sub inspector", "s.i.", "सिपाही",
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
        "ssc", "cgl", "chsl", "mts",
        "gd constable",
    ],
    "Bank": [
        "bank", "ibps", "sbi", "rbi", "बैंक",
    ],
    "BPSC": [
        "bpsc", "बिहार लोक सेवा",
    ],
    "BSSC": ["bssc"],
    "BTSC": ["btsc"],
    "Teacher": [
        "teacher", "tet", "ctet", "stet",
        "शिक्षक", "अध्यापक",
    ],
    "Defence": [
        "navy", "air force", "afcat",
        "coast guard", "defence", "रक्षा",
        "नौसेना", "वायु सेना",
    ],
    "Central Government": [
        "india post", "post office", "postal",
        "डाक विभाग",
    ],
}

BAD_TITLES = {
    "home", "english", "hindi", "login",
    "logout", "register", "contact us",
    "about us", "privacy policy",
    "terms and conditions", "sitemap",
    "skip to main content", "click here",
    "read more", "latest news",
}

DEADLINE_LABELS = [
    "last date to apply",
    "application last date",
    "closing date",
    "last date",
    "apply before",
    "अंतिम तिथि",
    "अंतिम तारीख",
    "आवेदन की अंतिम तिथि",
    "आवेदन की अंतिम तारीख",
]

PUBLISHED_LABELS = [
    "published on",
    "date of publication",
    "release date",
    "published",
    "जारी तिथि",
    "प्रकाशित",
    "दिनांक",
]


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

    url = str(url).strip()

    if base_url:
        url = urljoin(base_url, url)

    url, _ = urldefrag(url)
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ("http", "https"):
        return ""

    if not parsed.netloc:
        return ""

    query = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True
        )
        if key.lower() not in TRACKING_PARAMETERS
    ]

    parsed = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(query, doseq=True),
        fragment="",
    )

    return urlunparse(parsed)


def make_id(url, title=""):
    raw = normalize_url(url).lower()

    return hashlib.sha256(
        raw.encode("utf-8", errors="ignore")
    ).hexdigest()


def contains_keyword(text, keywords):
    text = (text or "").lower()

    return any(
        keyword.lower() in text
        for keyword in keywords
    )


def valid_title(title):
    title = clean_text(title)

    if len(title) < 12:
        return False

    if title.lower().strip(" .:-|") in BAD_TITLES:
        return False

    if len(title.split()) == 1 and title.isupper():
        return False

    return True


def get_source_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# =========================================================
# DATE PARSING
# =========================================================

def parse_date(value):
    if not value:
        return None

    text = clean_text(value)

    if not text:
        return None

    # RSS date format
    try:
        parsed = parsedate_to_datetime(text)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(timezone.utc)

    except Exception:
        pass

    # ISO date/time
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(timezone.utc)

    except Exception:
        pass

    patterns = [
        (
            r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)",
            "dmy"
        ),
        (
            r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)",
            "ymd"
        ),
    ]

    for pattern, order in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            first, second, third = map(
                int,
                match.groups()
            )

            if order == "dmy":
                day, month, year = first, second, third
            else:
                year, month, day = first, second, third

            return datetime(
                year,
                month,
                day,
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    # Textual month dates
    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                text,
                fmt
            ).replace(tzinfo=timezone.utc)

        except ValueError:
            continue

    return None


def find_date_near_labels(text, labels):
    text = clean_text(text)

    date_pattern = (
        r"(\d{1,2}[-/.]\d{1,2}[-/.]20\d{2}"
        r"|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}"
        r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2})"
    )

    for label in labels:
        pattern = (
            re.escape(label)
            + r"[\s:：\-]{0,40}"
            + date_pattern
        )

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            parsed = parse_date(match.group(1))

            if parsed:
                return parsed

    return None


def find_deadline(text):
    return find_date_near_labels(
        text,
        DEADLINE_LABELS
    )


def find_published_date(text):
    return find_date_near_labels(
        text,
        PUBLISHED_LABELS
    )


def is_recent(date_value):
    if not date_value:
        return True

    if date_value.tzinfo is None:
        date_value = date_value.replace(
            tzinfo=timezone.utc
        )

    age = utc_now() - date_value.astimezone(
        timezone.utc
    )

    return (
        timedelta(0)
        <= age
        <= timedelta(days=MAX_AGE_DAYS)
    )


# =========================================================
# CLASSIFICATION
# =========================================================

def classify_update(
    title,
    description,
    source_type,
    source_category
):
    title_text = clean_text(title).lower()

    full_text = (
        title_text
        + " "
        + clean_text(description).lower()
    )

    if source_type == "board":
        return "board"

    if source_type == "news":
        return "news"

    title_is_exam = contains_keyword(
        title_text,
        EXAM_KEYWORDS
    )

    title_is_job = contains_keyword(
        title_text,
        JOB_KEYWORDS
    )

    if title_is_job and not title_is_exam:
        return "job"

    if title_is_exam:
        return "exam"

    if title_is_job:
        return "job"

    if source_type == "job":
        return "job"

    if contains_keyword(
        full_text,
        EXAM_KEYWORDS
    ):
        return "exam"

    return "other"


def infer_category(title, source_category=""):
    text = clean_text(title).lower()

    ordered_categories = [
        "Police",
        "Army",
        "Railway",
        "Bank",
        "SSC",
        "BPSC",
        "BSSC",
        "BTSC",
        "Teacher",
        "Defence",
        "Central Government",
    ]

    for category in ordered_categories:
        if contains_keyword(
            text,
            CATEGORY_KEYWORDS[category]
        ):
            return category

    return source_category or "General"


# =========================================================
# CREATE UPDATE
# =========================================================

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

    if not valid_title(title) or not url:
        return None

    category = infer_category(
        title,
        source_category
    )

    if update_type == "board":
        category = source_category or "Board"

    elif update_type == "news":
        category = source_category or "Bihar News"

    return {
        "id": make_id(url),
        "title": title[:300],
        "description": description[:3000],
        "url": url,
        "source": source_name,
        "category": category,
        "type": update_type or "other",
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

    for anchor in soup.find_all(
        "a",
        href=True
    ):
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
            image = anchor.find("img", alt=True)

            if image:
                title = clean_text(
                    image.get("alt", "")
                )

        if not valid_title(title):
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
        clean_text(title)
        + " "
        + (url or "")
    ).lower()

    bad_words = [
        "login", "logout", "register",
        "privacy", "contact", "about-us",
        "facebook", "instagram", "youtube",
        "twitter", "whatsapp",
    ]

    if contains_keyword(text, bad_words):
        return False

    if source_category == "Bihar News":
        return valid_title(title)

    if source_category in (
        "Bihar Board",
        "CBSE Board"
    ):
        return contains_keyword(
            text,
            BOARD_KEYWORDS + EXAM_KEYWORDS
        )

    return contains_keyword(
        text,
        JOB_KEYWORDS + EXAM_KEYWORDS
    )


# =========================================================
# DETAIL PAGE EXTRACTION
# =========================================================

def get_meta_content(soup, keys):
    for key in keys:
        meta = (
            soup.find(
                "meta",
                attrs={"property": key}
            )
            or soup.find(
                "meta",
                attrs={"name": key}
            )
            or soup.find(
                "meta",
                attrs={"itemprop": key}
            )
        )

        if meta and meta.get("content"):
            value = clean_text(
                meta.get("content")
            )

            if value:
                return value

    return ""


def extract_detail(url):
    soup = get_soup(url)

    if soup is None:
        return "", None, None, ""

    # Read metadata before removing page elements.
    published_meta = get_meta_content(
        soup,
        [
            "article:published_time",
            "datePublished",
            "date",
            "pubdate",
            "publishdate",
        ]
    )

    modified_meta = get_meta_content(
        soup,
        [
            "article:modified_time",
            "dateModified",
        ]
    )

    meta_description = get_meta_content(
        soup,
        [
            "description",
            "og:description",
            "twitter:description",
        ]
    )

    for element in soup([
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "noscript"
    ]):
        element.decompose()

    title = ""

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(" ", strip=True)
        )

    if not title:
        title = get_meta_content(
            soup,
            ["og:title", "twitter:title"]
        )

    if not title and soup.title:
        title = clean_text(
            soup.title.get_text(" ", strip=True)
        )

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

    if not description:
        description = meta_description

    description = description[:12000]

    published_at = (
        parse_date(published_meta)
        or find_published_date(description)
    )

    last_date = find_deadline(description)

    if not published_at and modified_meta:
        published_at = parse_date(modified_meta)

    return (
        description,
        published_at,
        last_date,
        title
    )


# =========================================================
# HTML SOURCE SCRAPER
# =========================================================

def scrape_html_source(source, source_type):
    updates = []

    source_name = source["name"]
    source_category = source.get("category", "")

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
            (
                description,
                published_at,
                last_date,
                detail_title
            ) = extract_detail(
                link["url"]
            )

            title = (
                detail_title
                if valid_title(detail_title)
                else link["title"]
            )

            if not valid_title(title):
                continue

            if not description:
                description = title

            update_type = classify_update(
                title,
                description,
                source_type,
                source_category
            )

            # Only publication date controls the age filter.
            # A deadline is not a publication date.
            if published_at and not is_recent(published_at):
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

    logging.info(
        "%s accepted updates: %s",
        source_name,
        len(updates)
    )

    return updates


# =========================================================
# RSS / ATOM HELPERS
# =========================================================

def xml_local_name(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[1].lower()

    return tag.lower()


def xml_child_text(element, names):
    names = [
        name.lower()
        for name in names
    ]

    for child in element.iter():
        if xml_local_name(child.tag) in names:
            value = clean_text(
                "".join(child.itertext())
            )

            if value:
                return value

    return ""


def xml_entry_link(entry, feed_url):
    for child in entry:
        if xml_local_name(child.tag) != "link":
            continue

        href = child.attrib.get("href", "")
        rel = child.attrib.get(
            "rel",
            "alternate"
        )

        link_type = child.attrib.get(
            "type",
            ""
        )

        if not href:
            continue

        if rel not in ("alternate", ""):
            continue

        if link_type and "html" not in link_type.lower():
            continue

        return normalize_url(
            href,
            feed_url
        )

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
    return parse_date(text)


# =========================================================
# RSS / ATOM SCRAPER
# =========================================================

def scrape_rss_source(source):
    updates = []

    feed_url = source["url"]
    source_name = source["name"]

    source_category = source.get(
        "category",
        "Bihar News"
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
        if xml_local_name(element.tag) in (
            "item",
            "entry"
        ):
            entries.append(element)

    for entry in entries[:MAX_RSS_ENTRIES]:
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
                "content"
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
                "date"
            ]
        )

        published_at = parse_xml_date(
            date_text
        )

        if not title or not url:
            continue

        if not valid_title(title):
            continue

        if published_at and not is_recent(published_at):
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

        parsed = urlparse(url)

        normalized_key = (
            parsed.netloc.lower()
            + parsed.path.rstrip("/").lower()
            + (
                "?" + parsed.query
                if parsed.query else ""
            )
        )

        if normalized_key in seen_urls:
            continue

        item["url"] = url
        item["id"] = make_id(url)

        unique[item["id"]] = item
        seen_urls.add(normalized_key)

    result = list(unique.values())

    logging.info(
        "Unique updates: %s",
        len(result)
    )

    return result


# =========================================================
# COLLECT ALL UPDATES
# =========================================================

def collect_all_updates():
    all_updates = []

    logging.info("=" * 55)
    logging.info(
        "Starting Bihar EduAlert update checker"
    )
    logging.info("=" * 55)

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
            "type",
            "other"
        )

        if update_type == "board":
            grouped["board_updates"].append(item)

        elif update_type == "job":
            grouped["job_updates"].append(item)

        elif update_type == "exam":
            grouped["exam_updates"].append(item)

        elif update_type == "news":
            grouped["news_updates"].append(item)

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
        logging.error(
            "Firebase database is not available"
        )

        return False

    total_uploaded = 0
    all_successful = True

    for collection_name, items in grouped.items():
        if not items:
            logging.info(
                "%s: no updates",
                collection_name
            )

            continue

        logging.info(
            "Uploading %s items to %s",
            len(items),
            collection_name
        )

        try:
            for start in range(0, len(items), 400):
                batch_items = items[start:start + 400]

                batch = db.batch()
                references = []

                for item in batch_items:
                    reference = db.collection(
                        collection_name
                    ).document(
                        item["id"]
                    )

                    old_doc = reference.get()

                    old_data = (
                        old_doc.to_dict()
                        if old_doc.exists
                        else {}
                    )

                    data = dict(item)

                    data["first_seen_at"] = (
                        old_data.get("first_seen_at")
                        or item.get("created_at")
                        or iso_now()
                    )

                    data["last_checked_at"] = iso_now()
                    data["active"] = True

                    batch.set(
                        reference,
                        data,
                        merge=True
                    )

                    references.append(reference)

                batch.commit()

                total_uploaded += len(references)

            logging.info(
                "Uploaded successfully: %s",
                collection_name
            )

        except Exception:
            all_successful = False

            logging.exception(
                "Firestore upload failed: %s",
                collection_name
            )

    logging.info(
        "Total Firestore documents processed: %s",
        total_uploaded
    )

    return all_successful


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
    logging.info(
        "BIHAR EDU ALERT AUTOMATIC UPDATES"
    )
    logging.info("=" * 55)

    # Step 1: Collect updates
    all_updates = collect_all_updates()

    # Step 2: Group updates
    grouped = group_updates(
        all_updates
    )

    # Step 3: Save JSON
    json_saved = save_json(
        all_updates,
        grouped
    )

    # Step 4: Connect Firebase
    db = initialize_firebase()

    # Step 5: Upload to Firebase
    firebase_success = False

    if db is not None:
        firebase_success = upload_to_firestore(
            db,
            grouped
        )

    else:
        logging.error(
            "Firebase upload skipped"
        )

    # Step 6: Summary
    elapsed = round(
        time.time() - start_time,
        2
    )

    logging.info("=" * 55)
    logging.info(
        "UPDATE CHECKER FINISHED"
    )
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
        "JSON saved: %s",
        json_saved
    )

    logging.info(
        "Firebase upload successful: %s",
        firebase_success
    )

    logging.info(
        "Time taken: %s seconds",
        elapsed
    )

    # Fail GitHub Actions if important output failed.
    if not json_saved or not firebase_success:
        raise RuntimeError(
            "JSON generation or Firebase upload failed."
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
