
import os
import re
import json
import hashlib
import logging
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

import firebase_admin
from firebase_admin import credentials, firestore


# =====================================================
# CONFIG
# =====================================================

USER_AGENT = "Mozilla/5.0 BiharEduAlert-MCQ-Extractor/1.0"
MAX_PDF_SIZE = 8 * 1024 * 1024
MAX_PAGES = 80
MAX_QUESTIONS_PER_PDF = 80
TIMEOUT = 25

HEADERS = {"User-Agent": USER_AGENT}

SOURCES = [
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
        "name": "SSC",
        "category": "SSC",
        "urls": ["https://ssc.gov.in/"],
    },
    {
        "name": "Railway",
        "category": "Railway",
        "urls": ["https://rrb.indianrailways.gov.in/"],
    },
    {
        "name": "Indian Army",
        "category": "Army",
        "urls": ["https://joinindianarmy.nic.in/"],
    },
]

session = requests.Session()
session.headers.update(HEADERS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# =====================================================
# FIREBASE
# =====================================================

def initialize_firebase():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT secret missing")

    info = json.loads(raw)

    if not firebase_admin._apps:
        cred = credentials.Certificate(info)
        firebase_admin.initialize_app(cred)

    return firestore.client()


# =====================================================
# WEB / PDF
# =====================================================

def get_pdf_links(source):
    found = set()

    for page_url in source["urls"]:
        try:
            response = session.get(page_url, timeout=TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "lxml")

            for tag in soup.find_all("a", href=True):
                url = urljoin(response.url, tag["href"])
                parsed = urlparse(url)

                if (
                    parsed.scheme in ("http", "https")
                    and parsed.path.lower().endswith(".pdf")
                ):
                    found.add(url)

        except Exception as exc:
            logging.warning(
                "Page failed: %s | %s", page_url, exc
            )

    return sorted(found)


def download_pdf(url):
    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            stream=True,
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        buffer = BytesIO()
        total = 0

        for chunk in response.iter_content(65536):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_PDF_SIZE:
                logging.info("PDF too large: %s", url)
                return None

            buffer.write(chunk)

        data = buffer.getvalue()

        if not data.startswith(b"%PDF") and "pdf" not in content_type:
            return None

        return data

    except Exception as exc:
        logging.warning("PDF download failed: %s | %s", url, exc)
        return None


def extract_pdf_text(data):
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        parts = []

        for page in reader.pages[:MAX_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue

        return "\n".join(parts)

    except Exception as exc:
        logging.warning("PDF reading failed: %s", exc)
        return ""


# =====================================================
# MCQ PARSER
# =====================================================

QUESTION_START = re.compile(
    r"(?m)^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,3})\s*[\).:-]\s*(.+)$",
    re.IGNORECASE,
)

OPTION_START = re.compile(
    r"^\s*(?:\(([A-D])\)|([A-D])[\).:-])\s*(.+)$",
    re.IGNORECASE,
)

ANSWER_PATTERNS = [
    re.compile(
        r"(?<!\w)(\d{1,3})\s*[\).:-]\s*\(?([A-D])\)?(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)(\d{1,3})\s+([A-D])(?!\w)",
        re.IGNORECASE,
    ),
]

OPTION_INDEX = {
    "A": 0,
    "B": 1,
    "C": 2,
    "D": 3,
}


def extract_answer_key(text):
    """
    Extract numbered answer keys such as:
    1-A, 2-B, 3-C or 1. A, 2. B.

    Only explicit answer-key sections are considered.
    """
    answer_map = {}

    lines = text.splitlines()
    answer_section = False

    for line in lines:
        low = line.casefold()

        if re.search(
            r"answer\s*key|correct\s*answer|उत्तर\s*माला|उत्तर\s*कुंजी",
            low,
        ):
            answer_section = True

        if not answer_section:
            continue

        for pattern in ANSWER_PATTERNS:
            for match in pattern.finditer(line):
                number = int(match.group(1))
                letter = match.group(2).upper()

                if 1 <= number <= 500:
                    answer_map[number] = OPTION_INDEX[letter]

    return answer_map


def parse_questions(text):
    """
    Parse numbered questions and A/B/C/D options.
    A question is retained only when all four options
    and an explicit answer key are found.
    """
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    answer_key = extract_answer_key(text)

    questions = []
    current = None

    def finish_question():
        nonlocal current

        if not current:
            return

        if (
            len(current["options"]) == 4
            and current["number"] in answer_key
        ):
            correct = answer_key[current["number"]]

            if 0 <= correct < 4:
                question = current["question"].strip()
                options = [
                    item.strip()
                    for item in current["options"]
                ]

                if question and all(options):
                    questions.append({
                        "number": current["number"],
                        "question": question,
                        "options": options,
                        "correct_index": correct,
                    })

        current = None

    for line in lines:
        question_match = QUESTION_START.match(line)

        if question_match:
            finish_question()

            current = {
                "number": int(question_match.group(1)),
                "question": question_match.group(2).strip(),
                "options": [],
            }
            continue

        if current:
            option_match = OPTION_START.match(line)

            if option_match:
                letter = (
                    option_match.group(1)
                    or option_match.group(2)
                ).upper()

                index = OPTION_INDEX[letter]

                while len(current["options"]) <= index:
                    current["options"].append("")

                current["options"][index] = (
                    option_match.group(3).strip()
                )
            else:
                # Continue a wrapped question or option line.
                if current["options"]:
                    last = current["options"][-1]
                    if last:
                        current["options"][-1] = (
                            last + " " + line
                        )
                else:
                    current["question"] += " " + line

    finish_question()

    # Remove duplicate questions within this PDF.
    unique = []
    seen = set()

    for question in questions:
        key = re.sub(
            r"\W+", "",
            question["question"].casefold()
        )

        if key and key not in seen:
            seen.add(key)
            unique.append(question)

    return unique[:MAX_QUESTIONS_PER_PDF]


# =====================================================
# FIRESTORE UPLOAD
# =====================================================

def make_document_id(url):
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()


def make_test_questions(questions):
    output = []

    for item in questions:
        text = item["question"]
        options = item["options"]

        # Do not invent translations.
        is_hindi = bool(
            re.search(r"[\u0900-\u097F]", text)
        )

        output.append({
            "question_hi": text if is_hindi else "",
            "question_en": text if not is_hindi else "",
            "options": options,
            "correct_index": item["correct_index"],
            "explanation_hi": "",
            "explanation_en": "",
        })

    return output


def upload_test(db, source, pdf_url, questions):
    if not questions:
        return False

    doc_id = make_document_id(pdf_url)

    data = {
        "title": source["name"] + " PDF Practice Test",
        "board": "Career",
        "subject": source["category"],
        "category": source["category"],
        "source": source["name"],
        "pdf_url": pdf_url,
        "questions": make_test_questions(questions),
        "question_count": len(questions),
        "active": True,
        "type": "pdf_extracted",
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    ref = db.collection("mock_tests").document(doc_id)

    # A repeated scheduled run updates the same PDF document,
    # rather than creating another document.
    ref.set(data, merge=True)

    logging.info(
        "Uploaded %s questions: %s",
        len(questions), pdf_url
    )

    return True


# =====================================================
# MAIN
# =====================================================

def main():
    logging.info("Bihar EduAlert Free MCQ Extractor started")

    db = initialize_firebase()

    total_pdfs = 0
    total_questions = 0

    for source in SOURCES:
        logging.info("Checking source: %s", source["name"])

        pdf_links = get_pdf_links(source)

        logging.info(
            "%s PDF links found: %s",
            source["name"], len(pdf_links)
        )

        for pdf_url in pdf_links:
            data = download_pdf(pdf_url)

            if not data:
                continue

            text = extract_pdf_text(data)

            if not text.strip():
                logging.info("No selectable text: %s", pdf_url)
                continue

            questions = parse_questions(text)

            if not questions:
                logging.info("No verified MCQs: %s", pdf_url)
                continue

            try:
                if upload_test(db, source, pdf_url, questions):
                    total_pdfs += 1
                    total_questions += len(questions)

            except Exception as exc:
                logging.exception(
                    "Upload failed: %s | %s",
                    pdf_url, exc
                )

    logging.info(
        "Completed. PDFs uploaded: %s | Questions: %s",
        total_pdfs, total_questions
    )


if __name__ == "__main__":
    main()
