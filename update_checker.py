import os
import re
import json
import hashlib
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

import firebase_admin
from firebase_admin import credentials, firestore


# ==========================================================
# BIHAR EDUALERT - UPDATE CHECKER
# SarkariResult listings + official source homepages
# IMPORTANT: scraped information is NOT automatically verified.
# ==========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

USER_AGENT = "BiharEduAlert/2.0 (educational update reader)"
TIMEOUT = 25
MAX_PAGE_SIZE = 5 * 1024 * 1024
MAX_ARTICLES_PER_SECTION = 20
REQUEST_DELAY_SECONDS = 1.0

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

SARKARI_RESULT_SECTIONS = [
    ("Latest Jobs", "https://sarkariresult.com.cm/latest-jobs/"),
    ("Results", "https://sarkariresult.com.cm/result/"),
    ("Admit Card", "https://sarkariresult.com.cm/admit-card/"),
    ("Answer Key", "https://sarkariresult.com.cm/answer-key/"),
    ("Syllabus", "https://sarkariresult.com.cm/syllabus/"),
    ("Admission", "https://sarkariresult.com.cm/admission/"),
    ("Documents", "https://sarkariresult.com.cm/documents/"),
]

OFFICIAL_SOURCES = [
    {"name": "Bihar Public Service Commission", "category": "BPSC", "url": "https://bpsc.bihar.gov.in/"},
    {"name": "Bihar Staff Selection Commission", "category": "BSSC", "url": "https://bssc.bihar.gov.in/"},
    {"name": "Bihar Police CSBC", "category": "Police", "url": "https://csbc.bihar.gov.in/"},
    {"name": "Bihar Police BPSSC", "category": "Police", "url": "https://bpssc.bihar.gov.in/"},
    {"name": "Bihar Board", "category": "Bihar Board", "url": "https://biharboardonline.bihar.gov.in/"},
    {"name": "CBSE", "category": "CBSE Board", "url": "https://www.cbse.gov.in/"},
    {"name": "CBSE Academic", "category": "CBSE Board", "url": "https://cbseacademic.nic.in/"},
    {"name": "Staff Selection Commission", "category": "SSC", "url": "https://ssc.gov.in/"},
    {"name": "Indian Railways Recruitment", "category": "Railway", "url": "https://www.rrbapply.gov.in/"},
    {"name": "Indian Army", "category": "Army", "url": "https://joinindianarmy.nic.in/"},
    {"name": "National Career Service", "category": "Government Jobs", "url": "https://www.ncs.gov.in/"},
]

KEYWORDS = [
    "recruitment", "vacancy", "notification", "advertisement", "apply online",
    "application", "admit card", "hall ticket", "result", "merit list",
    "answer key", "exam date", "examination", "syllabus", "scholarship",
    "scheme", "government job", "भर्ती", "नियुक्ति", "विज्ञापन", "आवेदन",
    "प्रवेश पत्र", "एडमिट कार्ड", "परिणाम", "रिजल्ट", "उत्तर कुंजी",
    "परीक्षा", "छात्रवृत्ति", "योजना", "class 10", "class 12", "matric",
    "intermediate", "board exam"
]

DATE_PATTERNS = [
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{2,4}\b",
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{2,4}\b",
]


def initialize_firebase():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT secret is missing")
    service_account = json.loads(raw)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(service_account))
    return firestore.client()


def clean_url(url):
    url, _fragment = urldefrag(url)
    return url.strip()


def fetch_html(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        if len(response.content) > MAX_PAGE_SIZE:
            logging.warning("Page too large; skipped: %s", url)
            return None
        if "html" not in response.headers.get("Content-Type", "").lower():
            return None
        time.sleep(REQUEST_DELAY_SECONDS)
        return response.url, BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as exc:
        logging.warning("Could not fetch %s: %s", url, exc)
        return None


def identify_type(text):
    value = text.casefold()
    rules = [
        ("Admit Card", ["admit card", "hall ticket", "प्रवेश पत्र", "एडमिट कार्ड"]),
        ("Result", ["result", "merit list", "परिणाम", "रिजल्ट"]),
        ("Answer Key", ["answer key", "उत्तर कुंजी"]),
        ("Recruitment", ["recruitment", "vacancy", "भर्ती", "नियुक्ति", "advertisement", "apply online"]),
        ("Scholarship", ["scholarship", "छात्रवृत्ति"]),
        ("Government Scheme", ["scheme", "योजना"]),
        ("Exam Update", ["exam date", "examination", "परीक्षा", "syllabus"]),
        ("Admission", ["admission", "प्रवेश"]),
    ]
    for label, words in rules:
        if any(word in value for word in words):
            return label
    return "Official Notice"


def extract_dates(text):
    found = []
    for pattern in DATE_PATTERNS:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    # Preserve order and remove duplicates.
    return list(dict.fromkeys(item.strip() for item in found))[:20]


def is_probable_official_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host.endswith(".gov.in") or host == "gov.in" or host.endswith(".nic.in") or host == "nic.in"


def extract_official_links(soup, page_url):
    links = []
    for anchor in soup.find_all("a", href=True):
        link = clean_url(urljoin(page_url, anchor["href"]))
        if urlparse(link).scheme in ("http", "https") and is_probable_official_url(link):
            label = anchor.get_text(" ", strip=True)[:180]
            item = {"label": label or "Official website link", "url": link}
            if item not in links:
                links.append(item)
    return links[:15]


def extract_article_details(url, fallback_title, section_name):
    fetched = fetch_html(url)
    if not fetched:
        return None
    final_url, soup = fetched

    for element in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        element.decompose()

    title = ""
    if soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        title = fallback_title

    article = soup.find("article") or soup.find("main") or soup.body or soup
    full_text = article.get_text("\n", strip=True)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    summary = re.sub(r"\s+", " ", full_text).strip()[:1200]

    official_links = extract_official_links(soup, final_url)
    dates = extract_dates(full_text)

    # Extract links with labels; never assume a button is a valid application endpoint.
    useful_links = []
    for anchor in article.find_all("a", href=True):
        label = anchor.get_text(" ", strip=True)
        link = clean_url(urljoin(final_url, anchor["href"]))
        if not label or urlparse(link).scheme not in ("http", "https"):
            continue
        if any(word in label.casefold() for word in (
            "apply", "notification", "official", "download", "advertisement",
            "registration", "आवेदन", "अधिसूचना", "विज्ञापन"
        )):
            useful_links.append({"label": label[:120], "url": link})
    useful_links = useful_links[:20]

    # These fields are extracted only when visible in the page text.
    return {
        "title": title[:300],
        "summary": summary,
        "detailsText": full_text[:12000],
        "type": identify_type(title + " " + section_name + " " + full_text[:2500]),
        "datesFound": dates,
        "posts": "",
        "qualification": "",
        "ageLimit": "",
        "applicationFee": "",
        "lastDate": "",
        "applyUrl": "",
        "usefulLinks": useful_links,
        "officialLinks": official_links,
        "sourceUrl": final_url,
        "source": "SarkariResult.com.cm",
        "category": section_name,
        "verified": False,
        "verificationStatus": "pending",
        "verificationNote": "Third-party listing. Check the linked official notification before applying.",
    }


def get_sarkariresult_updates():
    updates = []
    seen = set()

    for section_name, section_url in SARKARI_RESULT_SECTIONS:
        logging.info("Checking SarkariResult section: %s", section_name)
        fetched = fetch_html(section_url)
        if not fetched:
            continue
        page_url, soup = fetched
        candidates = []

        for anchor in soup.find_all("a", href=True):
            title = anchor.get_text(" ", strip=True)
            link = clean_url(urljoin(page_url, anchor["href"]))
            host = (urlparse(link).hostname or "").lower()

            if not title or len(title) < 5 or len(title) > 300:
                continue
            if host not in ("sarkariresult.com.cm", "www.sarkariresult.com.cm"):
                continue
            if link.rstrip("/") == page_url.rstrip("/"):
                continue
            if not any(word in (title + " " + link).casefold() for word in KEYWORDS):
                # Section category itself can be useful, but skip generic navigation.
                if section_name not in ("Latest Jobs", "Results", "Admit Card", "Answer Key"):
                    continue
            if link not in seen:
                seen.add(link)
                candidates.append((title, link))

        for title, link in candidates[:MAX_ARTICLES_PER_SECTION]:
            detail = extract_article_details(link, title, section_name)
            if detail:
                updates.append(detail)

    return updates


def get_official_homepage_updates(source):
    fetched = fetch_html(source["url"])
    if not fetched:
        return []
    page_url, soup = fetched
    updates = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        title = anchor.get_text(" ", strip=True)
        link = clean_url(urljoin(page_url, anchor["href"]))
        if not title or len(title) < 5 or len(title) > 300:
            continue
        if urlparse(link).scheme not in ("http", "https") or link in seen:
            continue
        if not any(word in title.casefold() for word in KEYWORDS):
            continue
        seen.add(link)
        updates.append({
            "title": title,
            "summary": "",
            "detailsText": "",
            "type": identify_type(title),
            "datesFound": [],
            "posts": "",
            "qualification": "",
            "ageLimit": "",
            "applicationFee": "",
            "lastDate": "",
            "applyUrl": "",
            "usefulLinks": [],
            "officialLinks": [{"label": source["name"], "url": page_url}],
            "sourceUrl": link,
            "source": source["name"],
            "category": source["category"],
            "verified": False,
            "verificationStatus": "pending",
            "verificationNote": "Official homepage link found; specific notice details still need checking.",
        })
    return updates


def make_update_id(update):
    key_url = update.get("sourceUrl") or update.get("url") or ""
    key_title = update.get("title") or ""
    return hashlib.sha256((key_url + "|" + key_title).encode("utf-8")).hexdigest()


def save_update(db, update):
    document_id = make_update_id(update)
    document = dict(update)
    document.update({
        "url": update.get("sourceUrl", ""),
        "active": True,
        "verified": False,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    })

    try:
        ref = db.collection("announcements").document(document_id)
        old = ref.get()
        if old.exists:
            # Preserve the original creation time and refresh extracted details.
            old_data = old.to_dict() or {}
            document["createdAt"] = old_data.get("createdAt", firestore.SERVER_TIMESTAMP)
        ref.set(document, merge=True)

        # Keep job cards compatible with the current app's job_updates query.
        if document.get("type") == "Recruitment" or document.get("category") in (
            "Latest Jobs", "BPSC", "BSSC", "Police", "SSC", "Railway", "Army", "Government Jobs"
        ):
            db.collection("job_updates").document(document_id).set(document, merge=True)
        return True
    except Exception as exc:
        logging.error("Firestore save failed for %s: %s", update.get("title"), exc)
        return False


def save_json(updates):
    report = {
        "project": "Bihar EduAlert",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalUpdates": len(updates),
        "note": "Scraped listings are unverified until checked against the actual official notification.",
        "updates": updates,
    }
    with open("updates.json", "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def main():
    logging.info("========== BIHAR EDUALERT UPDATE CHECKER ==========")
    db = initialize_firebase()
    all_updates = []

    all_updates.extend(get_sarkariresult_updates())

    for source in OFFICIAL_SOURCES:
        logging.info("Checking official source: %s", source["name"])
        all_updates.extend(get_official_homepage_updates(source))

    # Deduplicate by canonical page URL + title.
    unique = {}
    for item in all_updates:
        unique[make_update_id(item)] = item
    all_updates = list(unique.values())

    saved = 0
    for item in all_updates:
        if save_update(db, item):
            saved += 1

    save_json(all_updates)
    logging.info("Total updates found: %s", len(all_updates))
    logging.info("Updates written/refreshed in Firestore: %s", saved)
    logging.info("Finished. Review verificationStatus before displaying a Verified badge.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Update checker failed")
        raise
