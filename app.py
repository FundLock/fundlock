import streamlit as st
import fitz  # PyMuPDF
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import re


st.set_page_config(page_title="FundLock", layout="centered")

st.title("MCA FundLock 🔒")
st.write(
    "Protect your MCA deals before they get shopped. Get more approvals from top lenders—built for brokers, powered by AI."
)

business_name = st.text_input(
    "Business Name",
    value="",
    placeholder="e.g., ABC Funding",
    key="business_name_input_v2"
)

watermark_text = business_name

uploaded_file = st.file_uploader(
    "Upload your MCA Application",
    type=["pdf"]
)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)
    full_text = []

    for page in doc:
        full_text.append(page.get_text("text", sort=True))

    doc.close()
    return "\n".join(full_text)


def unique_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def detect_emails(text: str) -> list[str]:
    matches = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        text
    )
    return unique_preserve_order(matches)


def detect_phones(text: str) -> list[str]:
    matches = re.findall(
        r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)",
        text
    )

    filtered = []
    for match in matches:
        cleaned = match.strip()
        digits = re.sub(r"\D", "", cleaned)

        if len(digits) == 10:
            filtered.append(cleaned)
        elif len(digits) == 11 and digits.startswith("1"):
            filtered.append(cleaned)

    return unique_preserve_order(filtered)


def detect_ssns(text: str) -> list[str]:
    matches = re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text)
    return unique_preserve_order(matches)


def mask_email(text: str) -> str:
    if "@" not in text:
        return "*****"
    name, domain = text.split("@", 1)
    masked_name = name[0] + "***" if len(name) > 1 else "*"
    return f"{masked_name}@{domain}"


def mask_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < 4:
        return "*****"
    return f"(***) ***-{digits[-4:]}"


def mask_ssn(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) != 9:
        return "***-**-****"
    return f"***-**-{digits[-4:]}"


def get_replacement(original: str, field_type: str) -> str:
    if field_type == "email":
        return mask_email(original)
    if field_type == "phone":
        return mask_phone(original)
    if field_type == "ssn":
        return mask_ssn(original)
    return "*****"


def apply_redaction(page, search_text: str, replacement_text: str):
    if not search_text or not search_text.strip():
        return

    matches = page.search_for(search_text)
    for rect in matches:
        adjusted_rect = fitz.Rect(
            rect.x0 + 0.5,
            rect.y0 + 1.5,
            rect.x1 - 0.5,
            rect.y1 - 1.5
        )

        page.add_redact_annot(
            adjusted_rect,
            text=replacement_text,
            fontsize=9,
            fill=(1, 1, 1),
            text_color=(0, 0, 0),
        )


def insert_centered_text(page, y, text, fontsize=8, color=(0.25, 0.25, 0.25)):
    text_width = fitz.get_text_length(text, fontsize=fontsize)
    x = max((page.rect.width - text_width) / 2, 8)
    page.insert_text(
        (x, y),
        text,
        fontsize=fontsize,
        color=color,
        overlay=True,
    )


def add_header_and_footer_if_space(page, broker: str, timestamp: str):
    rect = page.rect

    protected_line = f"Protected by FundLock | {broker} | {timestamp}"
    disclaimer_line = "Merchant info will be shared upon deal interest"

    insert_centered_text(
        page,
        y=10,
        text=protected_line,
        fontsize=7,
        color=(0.35, 0.35, 0.35),
    )

    insert_centered_text(
        page,
        y=18,
        text=disclaimer_line,
        fontsize=6,
        color=(0.5, 0.5, 0.5),
    )

    insert_centered_text(
        page,
        y=rect.height - 6,
        text=protected_line,
        fontsize=7,
        color=(0.35, 0.35, 0.35),
    )


# =========================
# ULTRA-CONSERVATIVE DEAL FACTS
# =========================
def clean_value(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip(" :-")
    return value.strip()


def format_currency(value: str) -> str:
    if not value:
        return ""
    raw = value.replace("$", "").replace(",", "").strip()
    try:
        num = int(float(raw))
        return "${:,.0f}".format(num)
    except Exception:
        return value


def get_sorted_page_lines(page, y_tolerance=3):
    words = page.get_text("words", sort=True)
    if not words:
        return []

    words = sorted(words, key=lambda w: (w[1], w[0]))
    lines = []
    current_line = []
    current_y = None

    for word in words:
        x0, y0, x1, y1, text, *_ = word
        if current_y is None:
            current_y = y0
            current_line = [(x0, text)]
            continue

        if abs(y0 - current_y) <= y_tolerance:
            current_line.append((x0, text))
            current_y = (current_y + y0) / 2
        else:
            line_text = " ".join(text for _, text in sorted(current_line, key=lambda t: t[0]))
            lines.append(clean_value(line_text))
            current_line = [(x0, text)]
            current_y = y0

    if current_line:
        line_text = " ".join(text for _, text in sorted(current_line, key=lambda t: t[0]))
        lines.append(clean_value(line_text))

    return [line for line in lines if line]


def find_line_index(lines, phrases):
    for i, line in enumerate(lines):
        lower_line = line.lower()
        for phrase in phrases:
            if phrase.lower() in lower_line:
                return i
    return None


def find_value_below_label(lines, label_phrases, pattern, lookahead=3, validator=None):
    idx = find_line_index(lines, label_phrases)
    if idx is None:
        return ""

    end = min(len(lines), idx + 1 + lookahead)
    for j in range(idx + 1, end):
        line = lines[j]
        matches = re.findall(pattern, line)
        for match in matches:
            candidate = match if isinstance(match, str) else match[0]
            candidate = clean_value(candidate)
            if not candidate:
                continue
            if validator and not validator(candidate):
                continue
            return candidate

    return ""


def is_valid_credit_score(value: str) -> bool:
    if not re.fullmatch(r"\d{3}", value):
        return False
    num = int(value)
    return 300 <= num <= 850


def is_valid_monthly_sales(value: str) -> bool:
    raw = value.replace(",", "").strip()
    if not re.fullmatch(r"\d{4,9}", raw):
        return False
    num = int(raw)
    return 1000 <= num <= 50000000


def is_valid_mm_yyyy(value: str) -> bool:
    if not re.fullmatch(r"\d{1,2}/\d{4}", value):
        return False
    month, year = value.split("/")
    month_num = int(month)
    year_num = int(year)
    current_year = datetime.now().year
    return 1 <= month_num <= 12 and 1900 <= year_num <= current_year


def extract_deal_facts_from_pdf(pdf_bytes: bytes) -> dict:
    facts = {
        "business_start_date": "",
        "credit_score": "",
        "monthly_sales": "",
    }

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)

    if len(doc) > 0:
        page = doc[0]
        lines = get_sorted_page_lines(page)

        facts["business_start_date"] = find_value_below_label(
            lines,
            ["Date/Year Started", "Business Start Date", "Date Established", "In Business Since"],
            r"\b(\d{1,2}/\d{4})\b",
            lookahead=3,
            validator=is_valid_mm_yyyy,
        )

        facts["credit_score"] = find_value_below_label(
            lines,
            ["Credit score", "Credit score (If known)", "FICO", "Personal Credit Score"],
            r"\b(\d{3})\b",
            lookahead=3,
            validator=is_valid_credit_score,
        )

        facts["monthly_sales"] = find_value_below_label(
            lines,
            ["Monthly Total Sales", "Monthly Revenue", "Monthly Sales"],
            r"\b(\d{4,9})\b",
            lookahead=3,
            validator=is_valid_monthly_sales,
        )

    doc.close()
    return facts


def build_deal_facts_bullets(facts: dict) -> list[str]:
    bullets = []

    if facts.get("business_start_date"):
        bullets.append(f"Started {facts['business_start_date']}.")

    if facts.get("credit_score"):
        bullets.append(f"Credit score: {facts['credit_score']}.")

    if facts.get("monthly_sales"):
        bullets.append(f"Monthly sales: {format_currency(facts['monthly_sales'])}.")

    safe_bullets = []
    seen = set()

    for bullet in bullets:
        bullet = clean_value(bullet)
        if not bullet:
            continue

        word_count = len(bullet.replace("•", "").split())
        if word_count > 10:
            continue

        key = bullet.lower()
        if key in seen:
            continue

        seen.add(key)
        safe_bullets.append(bullet)

    return safe_bullets[:5]


def protect_pdf(
    pdf_bytes: bytes,
    broker: str,
    watermark: str,
    merchant_emails: list[str],
    merchant_phones: list[str],
    merchant_ssns: list[str],
) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)

    timestamp = datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y %I:%M %p")

    for page in doc:
        rect = page.rect

        add_header_and_footer_if_space(page, broker, timestamp)

        page.insert_text(
            (rect.width / 2 - 150, rect.height / 2 + 50),
            watermark.upper(),
            fontsize=60,
            color=(0.88, 0.88, 0.88),
            overlay=True,
        )

        for merchant_email in merchant_emails:
            if merchant_email.strip():
                apply_redaction(
                    page,
                    merchant_email,
                    get_replacement(merchant_email, "email")
                )

        for merchant_phone in merchant_phones:
            if merchant_phone.strip():
                apply_redaction(
                    page,
                    merchant_phone,
                    get_replacement(merchant_phone, "phone")
                )

        for merchant_ssn in merchant_ssns:
            if merchant_ssn.strip():
                apply_redaction(
                    page,
                    merchant_ssn,
                    get_replacement(merchant_ssn, "ssn")
                )

        page.apply_redactions()

    output_path = temp_path.replace(".pdf", "_protected.pdf")
    doc.save(output_path)
    doc.close()

    return output_path


merchant_email = ""
merchant_phone = ""
merchant_ssn = ""

if uploaded_file is not None:
    pdf_bytes = uploaded_file.read()
    extracted_text = extract_pdf_text(pdf_bytes)

    detected_emails = detect_emails(extracted_text)
    detected_phones = detect_phones(extracted_text)
    detected_ssns = detect_ssns(extracted_text)

    deal_facts = extract_deal_facts_from_pdf(pdf_bytes)
    deal_fact_bullets = build_deal_facts_bullets(deal_facts)

    st.subheader("Deal Snapshot")
    st.caption("Snapshot based on application data — not underwriting or legal advice.")

    if deal_fact_bullets:
        for bullet in deal_fact_bullets:
            st.markdown(f"- {bullet}")
    else:
        st.markdown("- No reliable facts extracted.")

    st.subheader("Mask Sensitive Merchant Info")
    merchant_email = st.text_input(
        "Merchant Email",
        value=", ".join(detected_emails)
    )
    merchant_phone = st.text_input(
        "Merchant Phone",
        value=", ".join(detected_phones)
    )
    merchant_ssn = st.text_input(
        "Merchant SSN / Social",
        value=", ".join(detected_ssns)
    )

    if st.button("Protect File"):
        try:
            merchant_emails = unique_preserve_order(merchant_email.split(","))
            merchant_phones = unique_preserve_order(merchant_phone.split(","))
            merchant_ssns = unique_preserve_order(merchant_ssn.split(","))

            output_path = protect_pdf(
                pdf_bytes,
                business_name.strip() or "Broker",
                watermark_text.strip(),
                merchant_emails,
                merchant_phones,
                merchant_ssns,
            )

            st.success("File protected and masked successfully.")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download Protected File",
                    data=f,
                    file_name="protected.pdf",
                    mime="application/pdf",
                )

        except Exception as e:
            st.error(f"Something went wrong: {e}")
