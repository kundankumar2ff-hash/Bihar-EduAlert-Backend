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
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timedelta


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


# Bihar News RSS feeds. RSS feeds provide title, link and publication date.
# These are third-party publishers; verify each article at its original link.
NEWS_RSS_SOURCES = {
    "General": [
        ("Hindustan Bihar", "https://api.livehindustan.com/feeds/rss/bihar/rssfeed.xml"),
        ("Amar Ujala Bihar", "https://www.amarujala.com/rss/bihar.xml"),
        ("Prabhat Khabar State", "https://www.prabhatkhabar.com/state/feed/"),
    ],
    "District": [
        ("Hindustan Patna", "https://api.livehindustan.com/feeds/rss/bihar/patna/rssfeed.xml"),
        ("Hindustan Muzaffarpur", "https://api.livehindustan.com/feeds/rss/bihar/muzaffarpur/rssfeed.xml"),
        ("Hindustan Darbhanga", "https://api.livehindustan.com/feeds/rss/bihar/darbhanga/rssfeed.xml"),
        ("Hindustan Purnia", "https://api.livehindustan.com/feeds/rss/bihar/purnia/rssfeed.xml"),
        ("Hindustan Katihar", "https://api.livehindustan.com/feeds/rss/bihar/katihar/rssfeed.xml"),
        ("Hindustan Bhagalpur", "https://api.livehindustan.com/feeds/rss/bihar/bhagalpur/rssfeed.xml"),
        ("Hindustan Gaya", "https://api.livehindustan.com/feeds/rss/bihar/gaya/rssfeed.xml"),
        ("Hindustan Begusarai", "https://api.livehindustan.com/feeds/rss/bihar/begusarai/rssfeed.xml"),
        ("Hindustan Araria", "https://api.livehindustan.com/feeds/rss/bihar/araria/rssfeed.xml"),
        ("Hindustan Kishanganj", "https://api.livehindustan.com/feeds/rss/bihar/kishanganj/rssfeed.xml"),
    ],
    "Education": [
        ("Hindustan Education", "https://api.livehindustan.com/feeds/rss/career/education/rssfeed.xml"),
    ],
    "Employment": [
        ("Hindustan Jobs", "https://api.livehindustan.com/feeds/rss/career/jobs/rssfeed.xml"),
    ],
}

# Official Bihar pages remain useful for government announcements.
NEWS_WEB_SOURCES = {
    "Education": ["https://state.bihar.gov.in/educationbihar/"],
    "Govt Scheme": ["https://state.bihar.gov.in/prdbihar/", "https://cm.bihar.gov.in/users/NewsN.aspx"],
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
    "bihar", "patna", "muzaffarpur", "darbhanga", "purnia", "katihar",
    "bhagalpur", "gaya", "araria", "kishanganj", "begusarai",
    "education", "school", "college", "university", "student",
    "government", "scheme", "yojana", "scholarship", "employment",
    "job", "recruitment", "politics", "election", "weather", "flood",
    "बारिश", "बाढ़", "मौसम", "बिहार", "पटना", "मुजफ्फरपुर",
    "दरभंगा", "पूर्णिया", "कटिहार", "भागलपुर", "गया", "अररिया",
    "किशनगंज", "बेगूसराय", "शिक्षा", "स्कूल", "कॉलेज", "योजना",
    "छात्रवृत्ति", "रोजगार", "नौकरी", "राजनीति", "चुनाव"
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

def parse_rss_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def rss_text(element, name):
    node = element.find(name)
    if node is None:
        # RSS feeds sometimes use namespaced elements.
        node = next((x for x in element if x.tag.split("}")[-1] == name), None)
    if node is None:
        return ""
    return clean_text("".join(node.itertext()))


def get_news_updates(session, source_status):
    updates = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # First read RSS feeds: they provide the actual publication date.
    for category, sources in NEWS_RSS_SOURCES.items():
        for publisher, feed_url in sources:
            try:
                response = session.get(feed_url, timeout=TIMEOUT)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                entries = [node for node in root.iter() if node.tag.split("}")[-1] in ("item", "entry")]
                found = 0

                for entry in entries:
                    title = rss_text(entry, "title")
                    link = rss_text(entry, "link")
                    if not link:
                        link_node = next((x for x in entry if x.tag.split("}")[-1] == "link"), None)
                        if link_node is not None:
                            link = link_node.attrib.get("href", "")
                    link = link.strip()
                    if not title or not link:
                        continue
                    full_url = urljoin(feed_url, link)
                    if not valid_url(full_url):
                        continue

                    date_value = rss_text(entry, "pubDate") or rss_text(entry, "published") or rss_text(entry, "updated")
                    published = parse_rss_date(date_value)
                    # Do not import undated or clearly old RSS entries as fresh news.
                    if published is None or published < cutoff:
                        continue

                    description = rss_text(entry, "description") or rss_text(entry, "summary") or title
                    updates.append({
                        "category": category,
                        "title": title,
                        "url": full_url,
                        "source": publisher,
                        "description": description[:1200],
                        "published_at": published.isoformat(),
                        "checked_at": now_utc()
                    })
                    found += 1

                source_status.append({"type": "news_rss", "category": category, "source": feed_url, "status": "success", "links_found": found})
                print(f"NEWS RSS: {publisher} | Found: {found}")
            except Exception as error:
                source_status.append({"type": "news_rss", "category": category, "source": feed_url, "status": "error", "error": str(error)[:500]})
                print("NEWS RSS ERROR:", feed_url, error)

    # Also scrape official Bihar government announcement pages. These pages
    # may not expose publication dates, so mark their date as unverified.
    for category, urls in NEWS_WEB_SOURCES.items():
        for page_url in urls:
            try:
                links = scrape_page(session, page_url)
                found = 0
                for item in links:
                    if not matches_keyword(item["title"], NEWS_KEYWORDS):
                        continue
                    updates.append({
                        "category": category,
                        "title": item["title"],
                        "url": item["url"],
                        "source": page_url,
                        "description": item["title"],
                        "published_at": "",
                        "date_status": "unverified",
                        "checked_at": now_utc()
                    })
                    found += 1
                source_status.append({"type": "news_official", "category": category, "source": page_url, "status": "success", "links_found": found})
                print(f"OFFICIAL NEWS: {category} | Found: {found}")
            except Exception as error:
                source_status.append({"type": "news_official", "category": category, "source": page_url, "status": "error", "error": str(error)[:500]})
                print("OFFICIAL NEWS ERROR:", page_url, error)

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

        data["updated_at"] = firestore.SERVER_TIMESTAMP  # fetch time; not the article publication date
        data["fetched_at"] = firestore.SERVER_TIMESTAMP

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
