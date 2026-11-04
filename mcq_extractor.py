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


# ==================================================
# BIHAR EDU ALERT - MCQ EXTRACTOR
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

USER_AGENT = "BiharEduAlert/1.0"
TIMEOUT = 25
MAX_PDF_SIZE = 8 * 1024 * 1024
MAX_PAGES = 80
MAX_QUESTIONS = 100

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT
})


# ==================================================
# OFFICIAL SOURCES
# ==================================================

SOURCES = [
    {
        "name": "Bihar Board",
        "category": "Bihar Board",
        "urls": [
            "https://biharboardonline.bihar.gov.in/",
            "https://www.biharboardonline.org/",
            "https://intermediate.biharboardonline.com/"
        ]
    },
    {
        "name": "CBSE",
        "category": "CBSE Board",
        "urls": [
            "https://www.cbse.gov.in/",
            "https://cbseacademic.nic.in/"
        ]
    },
    {
        "name": "Bihar Police",
        "category": "Police",
        "urls": [
            "https://csbc.bihar.gov.in/",
            "https://bpssc.bihar.gov.in/"
        ]
    },
    {
        "name": "BPSC",
        "category": "BPSC",
        "urls": [
            "https://bpsc.bihar.gov.in/"
        ]
    },
    {
        "name": "SSC",
        "category": "SSC",
        "urls": [
            "https://ssc.gov.in/"
        ]
    },
    {
        "name": "Railway",
        "category": "Railway",
        "urls": [
            "https://rrb.indianrailways.gov.in/"
        ]
    },
    {
        "name": "Indian Army",
        "category": "Army",
        "urls": [
            "https://joinindianarmy.nic.in/"
        ]
    }
]


# ==================================================
# FIREBASE CONNECTION
# ==================================================

def connect_firebase():

    service_account = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT"
    )

    if not service_account:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT secret is missing"
        )

    credentials_data = json.loads(service_account)

    if not firebase_admin._apps:
        certificate = credentials.Certificate(
            credentials_data
        )

        firebase_admin.initialize_app(certificate)

    return firestore.client()


# ==================================================
# FIND PDF LINKS
# ==================================================

def find_pdf_links(source):

    pdf_links = set()

    for website in source["urls"]:

        try:
            response = session.get(
                website,
                timeout=TIMEOUT
            )

            response.raise_for_status()

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            for link in soup.find_all("a", href=True):

                pdf_url = urljoin(
                    response.url,
                    link["href"]
                )

                parsed = urlparse(pdf_url)

                if (
                    parsed.scheme in ("http", "https")
                    and parsed.path.lower().endswith(".pdf")
                ):
                    pdf_links.add(pdf_url)

        except requests.RequestException as error:

            logging.warning(
                "Website unavailable: %s - %s",
                website,
                error
            )

    return sorted(pdf_links)


# ==================================================
# DOWNLOAD PDF
# ==================================================

def download_pdf(url):

    try:

        response = session.get(
            url,
            timeout=TIMEOUT,
            stream=True
        )

        response.raise_for_status()

        data = bytearray()

        for chunk in response.iter_content(65536):

            if not chunk:
                continue

            data.extend(chunk)

            if len(data) > MAX_PDF_SIZE:
                logging.warning("PDF too large: %s", url)
                return None

        content = bytes(data)

        if not content.startswith(b"%PDF"):
            logging.warning("Not a valid PDF: %s", url)
            return None

        return content

    except requests.RequestException as error:

        logging.warning(
            "Download failed: %s - %s",
            url,
            error
        )

        return None


# ==================================================
# EXTRACT TEXT FROM PDF
# ==================================================

def extract_text(pdf_data):

    try:

        reader = PdfReader(
            BytesIO(pdf_data),
            strict=False
        )

        pages = []

        for page in reader.pages[:MAX_PAGES]:

            try:
                pages.append(
                    page.extract_text() or ""
                )

            except Exception:
                continue

        return "\n".join(pages)

    except Exception as error:

        logging.warning(
            "PDF extraction error: %s",
            error
        )

        return ""


# ==================================================
# EXTRACT ANSWER KEY
# ==================================================

def extract_answer_key(text):

    answers = {}
    in_answer_section = False

    for line in text.splitlines():

        line = line.strip()

        if re.search(
            r"answer\s*key|correct\s*answer|"
            r"उत्तर\s*माला|उत्तर\s*कुंजी",
            line,
            re.IGNORECASE
        ):
            in_answer_section = True

        if not in_answer_section:
            continue

        matches = re.findall(
            r"(?<!\d)(\d{1,3})\s*[\).:-]?\s*"
            r"(?:\(?([A-D])\)?)(?![A-Z])",
            line,
            re.IGNORECASE
        )

        for number, letter in matches:

            number = int(number)
            letter = letter.upper()

            if 1 <= number <= 500:

                answers[number] = (
                    ord(letter) - ord("A")
                )

    return answers


# ==================================================
# PARSE MCQ QUESTIONS
# ==================================================

def parse_mcqs(text):

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    answer_key = extract_answer_key(text)

    questions = []
    current = None

    question_pattern = re.compile(
        r"^(?:Q(?:uestion)?\.?\s*)?"
        r"(\d{1,3})[\).:-]\s*(.+)$",
        re.IGNORECASE
    )

    option_pattern = re.compile(
        r"^(?:\(([A-D])\)|([A-D])[\).:-])\s*(.+)$",
        re.IGNORECASE
    )

    def save_current():

        nonlocal current

        if current is None:
            return

        number = current["number"]
        options = current["options"]

        if (
            number in answer_key
            and len(options) == 4
            and all(options)
        ):

            questions.append({
                "question": current["question"],
                "options": options,
                "correct_index": answer_key[number]
            })

        current = None

    for line in lines:

        question_match = question_pattern.match(line)

        if question_match:

            save_current()

            current = {
                "number": int(question_match.group(1)),
                "question": question_match.group(2).strip(),
                "options": ["", "", "", ""]
            }

            continue

        if current is None:
            continue

        option_match = option_pattern.match(line)

        if option_match:

            letter = (
                option_match.group(1)
                or option_match.group(2)
            ).upper()

            option_text = option_match.group(3).strip()

            index = ord(letter) - ord("A")

            current["options"][index] = option_text

        else:

            # Continue multi-line question or option
            if not any(current["options"]):
                current["question"] += " " + line

    save_current()

    # Remove duplicate questions
    unique = []
    seen = set()

    for question in questions:

        key = question["question"].casefold()

        if key not in seen:

            seen.add(key)
            unique.append(question)

    return unique[:MAX_QUESTIONS]


# ==================================================
# GENERATE UNIQUE DOCUMENT ID
# ==================================================

def generate_document_id(url, question):

    raw = (
        url + "|" + question["question"]
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


# ==================================================
# SAVE MCQS TO FIRESTORE
# ==================================================

def save_mcqs(
    db,
    source,
    pdf_url,
    questions,
    pdf_hash
):

    saved = 0

    for index, question in enumerate(questions):

        document_id = generate_document_id(
            pdf_url,
            question
        )

        document = {
            "question": question["question"],
            "options": question["options"],
            "correctIndex": question["correct_index"],
            "category": source["category"],
            "source": source["name"],
            "sourceUrl": pdf_url,
            "pdfHash": pdf_hash,
            "questionNumber": index + 1,
            "active": True,
            "createdAt": firestore.SERVER_TIMESTAMP
        }

        try:

            reference = (
                db.collection("mock_tests")
                .document(document_id)
            )

            if reference.get().exists:
                continue

            reference.set(document)

            saved += 1

        except Exception as error:

            logging.error(
                "Firestore save failed: %s",
                error
            )

    return saved


# ==================================================
# PROCESS ONE PDF
# ==================================================

def process_pdf(db, source, pdf_url):

    logging.info("Processing: %s", pdf_url)

    pdf_data = download_pdf(pdf_url)

    if not pdf_data:
        return 0

    pdf_hash = hashlib.sha256(
        pdf_data
    ).hexdigest()

    text = extract_text(pdf_data)

    if not text.strip():

        logging.info(
            "No readable text. Scanned PDF may need OCR."
        )

        return 0

    questions = parse_mcqs(text)

    if not questions:

        logging.info(
            "No verified MCQs found in: %s",
            pdf_url
        )

        return 0

    saved = save_mcqs(
        db,
        source,
        pdf_url,
        questions,
        pdf_hash
    )

    logging.info(
        "Found %s questions, saved %s",
        len(questions),
        saved
    )

    return saved


# ==================================================
# MAIN FUNCTION
# ==================================================

def main():

    logging.info(
        "========== BIHAR EDU ALERT MCQ EXTRACTOR =========="
    )

    db = connect_firebase()

    total_saved = 0

    for source in SOURCES:

        logging.info(
            "Checking source: %s",
            source["name"]
        )

        pdf_links = find_pdf_links(source)

        logging.info(
            "Found %s PDF links",
            len(pdf_links)
        )

        for pdf_url in pdf_links:

            total_saved += process_pdf(
                db,
                source,
                pdf_url
            )

    logging.info(
        "Total new MCQs saved: %s",
        total_saved
    )

    logging.info(
        "========== EXTRACTION COMPLETE =========="
    )


if __name__ == "__main__":

    try:
        main()

    except Exception:

        logging.exception(
            "MCQ extractor failed"
        )

        raise
