import streamlit as st
import fitz  # PyMuPDF
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import re


st.set_page_config(page_title="FundLock", layout="centered")

st.title("MCA FundLock 🔒")
st.write(
    "Protect your submission window. Reduce deal shopping. Get more approvals."
)

tab1, tab2 = st.tabs([
    "Protect Documents",
    "Document Transfer (Beta)"
])


# =========================
# SHARED FUNCTIONS
# =========================

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


def insert_responsive_watermark(page, text: str):
    if not text or not text.strip():
        return

    rect = page.rect
    watermark = text.upper().strip()

    fontsize = 60
    max_width = rect.width - 40

    text_width = fitz.get_text_length(watermark, fontsize=fontsize)
    while text_width > max_width and fontsize > 18:
        fontsize -= 2
        text_width = fitz.get_text_length(watermark, fontsize=fontsize)

    x = max((rect.width - text_width) / 2, 12)
    y = rect.height / 2

    page.insert_text(
        (x, y),
        watermark,
        fontsize=fontsize,
        color=(0.88, 0.88, 0.88),
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


def find_credit_score_near_label(lines, label_phrases):
    idx = find_line_index(lines, label_phrases)
    if idx is None:
        return ""

    candidate_lines = []
    candidate_lines.append(lines[idx])

    if idx + 1 < len(lines):
        candidate_lines.append(lines[idx + 1])

    for line in candidate_lines:
        matches = re.findall(r"\b(\d{3})\b", line)
        for match in matches:
            candidate = clean_value(match)
            if is_valid_credit_score(candidate):
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

        facts["credit_score"] = find_credit_score_near_label(
            lines,
            ["Credit score", "Credit score (If known)", "FICO", "Personal Credit Score"],
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
) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)

    timestamp = datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y %I:%M %p")

    for page in doc:
        add_header_and_footer_if_space(page, broker, timestamp)

        if watermark:
            insert_responsive_watermark(page, watermark)

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

        page.apply_redactions()

    output_path = temp_path.replace(".pdf", "_protected.pdf")
    doc.save(output_path)
    doc.close()

    return output_path


# =========================
# TAB 1 — EXISTING PROTECT DOCUMENTS WORKFLOW
# =========================

with tab1:

    business_name = st.text_input(
        "Business Name",
        value="",
        placeholder="e.g., ABC Funding",
        key="business_name_input_v2"
    )

    watermark_text = business_name

    uploaded_files = st.file_uploader(
        "Upload your MCA Application(s) (PDF)",
        type=["pdf"],
        accept_multiple_files=True
    )

    st.caption("Documents are processed in real time and never stored.")

    merchant_email = ""
    merchant_phone = ""

    if uploaded_files:
        st.subheader("Uploaded Documents")

        successful_files = []

        for uploaded_file in uploaded_files:
            try:
                pdf_bytes = uploaded_file.getvalue()

                successful_files.append({
                    "name": uploaded_file.name,
                    "bytes": pdf_bytes
                })

            except Exception as e:
                st.error(f"{uploaded_file.name} failed to upload: {e}")

        if successful_files:
            for file in successful_files:
                st.caption(f"✓ {file['name']}")

            if len(successful_files) == 1:
                selected_file = successful_files[0]
                selected_file_name = selected_file["name"]
            else:
                selected_file_name = st.selectbox(
                    "Select document to review and protect",
                    [file["name"] for file in successful_files]
                )

                selected_file = next(
                    file for file in successful_files
                    if file["name"] == selected_file_name
                )

            pdf_bytes = selected_file["bytes"]
            safe_key = re.sub(r"[^A-Za-z0-9_]", "_", selected_file_name)

            # Detect merchant email/phone from the full uploaded package.
            # This avoids blank fields when the selected doc has no contact info,
            # but another uploaded doc does.
            all_detected_emails = []
            all_detected_phones = []

            for file in successful_files:
                try:
                    file_text = extract_pdf_text(file["bytes"])
                    all_detected_emails.extend(detect_emails(file_text))
                    all_detected_phones.extend(detect_phones(file_text))
                except Exception:
                    pass

            detected_emails = unique_preserve_order(all_detected_emails)
            detected_phones = unique_preserve_order(all_detected_phones)

            # Only show masking fields if something was detected.
            # Keeps UX close to the original FundLock experience.
            if detected_emails or detected_phones:
                st.subheader("Mask Sensitive Merchant Info")

                merchant_email = st.text_input(
                    "Merchant Email",
                    value=", ".join(detected_emails),
                    key=f"merchant_email_{safe_key}"
                )

                merchant_phone = st.text_input(
                    "Merchant Phone",
                    value=", ".join(detected_phones),
                    key=f"merchant_phone_{safe_key}"
                )

            else:
                merchant_email = ""
                merchant_phone = ""

            if st.button("Protect File"):
                try:
                    merchant_emails = unique_preserve_order(merchant_email.split(","))
                    merchant_phones = unique_preserve_order(merchant_phone.split(","))

                    output_path = protect_pdf(
                        pdf_bytes,
                        business_name.strip() or "Broker",
                        watermark_text.strip(),
                        merchant_emails,
                        merchant_phones,
                    )

                    st.success(f"{selected_file_name} protected and masked successfully.")

                    download_name = selected_file_name.replace(".pdf", "_protected.pdf")

                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="Download Protected File",
                            data=f,
                            file_name=download_name,
                            mime="application/pdf",
                        )

                except Exception as e:
                    st.error(f"Something went wrong: {e}")

            # Keep Deal Snapshot tied only to the selected document.
            # This preserves your current logic and avoids pulling noisy facts
            # from bank statements or support docs.
            deal_facts = extract_deal_facts_from_pdf(pdf_bytes)
            deal_fact_bullets = build_deal_facts_bullets(deal_facts)

            st.subheader("Deal Snapshot")
            st.caption("Snapshot based on application data — not underwriting or legal advice.")

            if deal_fact_bullets:
                for bullet in deal_fact_bullets:
                    st.markdown(f"- {bullet}")
            else:
                st.markdown("- No reliable facts extracted.")




# =========================
# TAB 2 STRICT EXTRACTION HELPERS
# =========================

DESTINATION_FIELD_OPTIONS = [
    "Business Name",
    "Owner Name",
    "Business Address",
    "Phone",
    "Email",
    "EIN",
    "Business Start Date",
    "Years in Business",
    "Monthly Revenue",
    "Credit Score",
    "Industry",
    "Requested Funding Amount",
    "Do Not Transfer",
]

LABEL_WORDS = [
    "legal business name", "business phone number", "business fax number",
    "dba business name", "address", "date/year started", "monthly rent",
    "web address", "email address", "alternate cell phone number",
    "business address", "street", "city", "state", "zip",
    "seasonal business", "tax id number", "nature of business",
    "style of business", "monthly total sales", "avg daily bank balance",
    "use of funds", "advance amount requested", "please fill out completely",
    "how long have you owned your business"
]


def is_probably_label_text(value: str) -> bool:
    if not value:
        return True

    cleaned = clean_value(value).lower()
    if not cleaned:
        return True

    # If the "value" is mostly known form labels, reject it.
    label_hits = sum(1 for label in LABEL_WORDS if label in cleaned)
    if label_hits >= 1 and not re.search(r"\d|@|\$|llc|inc|corp|co\.|ltd", cleaned):
        return True

    # Reject obvious merged label rows.
    if label_hits >= 2:
        return True

    return False


def extract_pdf_lines_for_transfer(pdf_bytes: bytes) -> list[str]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)
    lines = []

    for page in doc:
        page_lines = get_sorted_page_lines(page, y_tolerance=4)
        lines.extend(page_lines)

    doc.close()
    return [clean_value(line) for line in lines if clean_value(line)]


def get_text_after_label_line(lines: list[str], label_phrases: list[str], lookahead: int = 4) -> str:
    idx = find_line_index(lines, label_phrases)
    if idx is None:
        return ""

    for j in range(idx + 1, min(len(lines), idx + 1 + lookahead)):
        candidate = clean_value(lines[j])
        if candidate and not is_probably_label_text(candidate):
            return candidate

    return ""


def find_pattern_near_label(
    lines: list[str],
    label_phrases: list[str],
    pattern: str,
    lookahead: int = 5,
    validator=None,
) -> str:
    idx = find_line_index(lines, label_phrases)
    if idx is None:
        return ""

    candidate_lines = lines[idx:min(len(lines), idx + 1 + lookahead)]

    for line in candidate_lines:
        for match in re.findall(pattern, line, flags=re.IGNORECASE):
            candidate = match if isinstance(match, str) else match[0]
            candidate = clean_value(candidate)
            if not candidate:
                continue
            if validator and not validator(candidate):
                continue
            return candidate

    return ""


def remove_phone_email_and_labels(value: str) -> str:
    value = clean_value(value)
    value = re.sub(r"\(?\*{3}\)?\s*\*{3}[-.\s]?\d{4}", " ", value)
    value = re.sub(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)", " ", value)
    value = re.sub(r"[A-Za-z0-9_*.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", value)
    for label in LABEL_WORDS:
        value = re.sub(re.escape(label), " ", value, flags=re.IGNORECASE)
    return clean_value(value)


def detect_masked_or_real_phones(text: str) -> list[str]:
    phones = detect_phones(text)

    masked = re.findall(r"\(?\*{3}\)?\s*\*{3}[-.\s]?\d{4}", text)
    phones.extend(masked)

    return unique_preserve_order(phones)


def detect_masked_or_real_emails(text: str) -> list[str]:
    matches = re.findall(
        r"[A-Za-z0-9_*.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        text
    )
    return unique_preserve_order(matches)


def extract_business_name_strict(lines: list[str], full_text: str) -> str:
    # Best case: value is on the line after Legal Business Name.
    raw = get_text_after_label_line(lines, ["Legal Business Name", "Business Name"], lookahead=3)
    cleaned = remove_phone_email_and_labels(raw)

    # Keep common business suffixes and reject obvious bad extractions.
    if cleaned and not is_probably_label_text(cleaned):
        # If the line still contains lots of columns, keep only the left business-looking piece.
        parts = re.split(r"\s{2,}|\t", cleaned)
        if parts:
            cleaned = clean_value(parts[0])
        if cleaned and not re.fullmatch(r"[-–—_\s]+", cleaned):
            return cleaned

    # Fallback: look for a business suffix anywhere in the text.
    match = re.search(
        r"\b([A-Z][A-Za-z0-9&',.\- ]{2,80}\s+(?:LLC|L\.L\.C\.|Inc\.?|Corp\.?|Corporation|Co\.?|Company|Ltd\.?))\b",
        full_text,
        flags=re.IGNORECASE,
    )
    if match:
        return clean_value(match.group(1))

    return ""


def normalize_zip(zip_value: str) -> str:
    """
    Keep ZIPs strict:
    - accepts 5 digit ZIPs
    - accepts ZIP+4 but returns first 5
    - rejects anything under 5 digits
    """
    if not zip_value:
        return ""

    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", zip_value)
    return match.group(1) if match else ""


def find_first_valid_zip(text: str) -> str:
    return normalize_zip(text)


def looks_like_street_address(value: str) -> bool:
    if not value:
        return False

    return bool(
        re.search(r"\b\d{1,6}\b", value)
        and re.search(
            r"\b(street|st\.|road|rd\.|avenue|ave\.|blvd|boulevard|drive|dr\.|lane|ln\.|way|court|ct\.|main|franks)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def clean_address_value(value: str) -> str:
    value = clean_value(value)
    for label in [
        "Business Address: Street",
        "Business Address",
        "Street",
        "City",
        "State",
        "Zip",
    ]:
        value = re.sub(re.escape(label), " ", value, flags=re.IGNORECASE)
    return clean_value(value)



def normalize_business_address_output(street: str, city_state_zip: str) -> str:
    street = clean_address_value(street)
    city_state_zip = clean_address_value(city_state_zip)

    # Restore common street suffix if OCR separated/dropped it.
    if re.search(r"\bMain\b", street, flags=re.IGNORECASE) and not re.search(
        r"\b(street|st\.|road|rd\.|avenue|ave\.|blvd|drive|dr\.|lane|ln\.|way|court|ct\.)\b",
        street,
        flags=re.IGNORECASE,
    ):
        street = re.sub(r"\bMain\b", "Main Street", street, flags=re.IGNORECASE)

    # If the city/state/zip line contains a valid 5-digit ZIP, cut everything after it.
    zip_match = re.search(r"\b\d{5}\b", city_state_zip)
    if zip_match:
        city_state_zip = city_state_zip[:zip_match.end()]
    else:
        # If OCR only gave a broken 4-digit ZIP fragment like 7310, remove that fragment.
        city_state_zip = re.sub(r"\b\d{4}\b.*$", "", city_state_zip).strip(" ,:-")

    # Remove obvious OCR leftovers after cleanup.
    city_state_zip = re.sub(r"\s+", " ", city_state_zip).strip(" ,:-")
    city_state_zip = re.sub(r",\s*[A-Za-z]{1,2}$", "", city_state_zip).strip(" ,:-")

    # If full string accidentally has junk after ZIP, cut again after ZIP.
    combined = clean_value(f"{street}, {city_state_zip}") if city_state_zip else street
    zip_match_combined = re.search(r"\b\d{5}\b", combined)
    if zip_match_combined:
        combined = combined[:zip_match_combined.end()]

    return clean_value(combined).strip(" ,:-")

def extract_business_address_strict(lines: list[str], full_text: str) -> str:
    """
    MCA apps often split business address across:
    Street | City | State | Zip

    Rules:
    - Street must look like a street address.
    - ZIP must be a real 5-digit ZIP if shown.
    - Broken 4-digit ZIP fragments are removed instead of trusted.
    """
    street = ""
    city_state_zip = ""

    idx = find_line_index(lines, ["Business Address: Street", "Business Address Street"])
    if idx is None:
        idx = find_line_index(lines, ["Business Address", "Street"])

    if idx is not None:
        for j in range(idx + 1, min(len(lines), idx + 8)):
            candidate = clean_address_value(lines[j])
            if not candidate or is_probably_label_text(candidate):
                continue

            if not street and looks_like_street_address(candidate):
                street = candidate
                continue

            if street and not city_state_zip:
                # Prefer line containing a valid 5-digit ZIP.
                if find_first_valid_zip(candidate):
                    city_state_zip = candidate
                    break

                # If line looks like city/state but has no ZIP, keep it as fallback.
                if re.search(r"[A-Za-z]{3,}", candidate) and not looks_like_street_address(candidate):
                    city_state_zip = candidate

    # Fallback: locate a street-looking line anywhere, then look nearby for ZIP line.
    if not street:
        for i, line in enumerate(lines):
            candidate = clean_address_value(line)
            if looks_like_street_address(candidate):
                street = candidate
                for j in range(i + 1, min(len(lines), i + 6)):
                    possible_zip_line = clean_address_value(lines[j])
                    if find_first_valid_zip(possible_zip_line):
                        city_state_zip = possible_zip_line
                        break
                break

    if not street:
        return ""

    # Last resort: if OCR split ZIP oddly, look anywhere in nearby full text for valid 5-digit ZIP.
    if not find_first_valid_zip(city_state_zip):
        zip_from_full_text = find_first_valid_zip(full_text)
        if zip_from_full_text and zip_from_full_text not in city_state_zip:
            # Only append ZIP if city/state text exists and does not already contain a broken fragment.
            if city_state_zip and not re.search(r"\b\d{4}\b", city_state_zip):
                city_state_zip = f"{city_state_zip} {zip_from_full_text}"

    return normalize_business_address_output(street, city_state_zip)

def looks_like_person_name(value: str) -> bool:
    """
    Strict person-name detection for owner fields.

    Accept:
    - Daniel Neal
    - Victoria Veales
    - John Smith
    - Mary Ann Jones

    Reject:
    - AUTHORIZATION GIP FUNDING
    - Rapha Roots LLC
    - ABC Capital Group
    - Name Personal Annual Revenue
    - Business Information
    """
    value = clean_value(value)

    if not value:
        return False

    if re.search(r"\d", value):
        return False

    # Reject all-caps phrases. Human names from these apps usually come through as Title Case.
    if value.isupper():
        return False

    lowered = value.lower()

    # Reject business/entity/header/form words.
    business_or_form_terms = [
        "llc", "l.l.c", "inc", "corp", "corporation", "company", "co.",
        "funding", "capital", "group", "holdings", "partners", "asset",
        "finance", "financial", "merchant", "advance", "authorization",
        "business", "information", "application", "applicant", "legal",
        "dba", "tax", "ein", "revenue", "sales", "bank", "balance",
        "name personal", "personal annual revenue", "credit score",
        "if known", "social security", "social security no", "dob",
        "date of birth", "address", "phone", "home phone", "owner",
        "title", "percentage", "ownership", "city", "state", "zip",
        "rent", "own", "please fill", "qualifying questions",
    ]

    if any(term in lowered for term in business_or_form_terms):
        return False

    # Accept only 2-4 Title Case name tokens.
    # This rejects uppercase headers and random label text.
    return bool(
        re.fullmatch(
            r"[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,3}",
            value,
        )
    )


def extract_capitalized_person_names(value: str) -> list[str]:
    """
    Pull likely human names from a noisy OCR line.

    Examples:
    - 'Daniel Neal 727 Victoria Veales' -> ['Daniel Neal', 'Victoria Veales']
    - 'Name Personal Annual Revenue' -> []
    - 'AUTHORIZATION GIP FUNDING' -> []
    """
    value = clean_value(value)

    if not value:
        return []

    # Remove common header labels before searching.
    value = re.sub(
        r"\b(Name|Personal Annual Revenue|Credit score|Credit Score|If known|Social Security No\.?|DOB|Address|Title/Percentage|ownership)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = clean_value(value)

    candidates = []

    # Look only for Title Case names, not ALL CAPS business/header text.
    for match in re.findall(
        r"\b([A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,3})\b",
        value,
    ):
        match = clean_value(match)
        if looks_like_person_name(match):
            candidates.append(match)

    return unique_preserve_order(candidates)


def extract_owner_name_strict(lines: list[str], full_text: str) -> str:
    # Generic owner labels first, but only accept if truly person-like.
    raw = get_text_after_label_line(
        lines,
        ["Owner Name", "Principal Name", "Applicant Name", "Owner/Officer Name"],
        lookahead=4
    )
    cleaned = remove_phone_email_and_labels(raw)
    if looks_like_person_name(cleaned):
        return cleaned

    # Main MCA layout: OWNER 1 section has header row, then values row.
    idx = find_line_index(lines, ["OWNER 1", "Owner 1"])

    if idx is not None:
        # Keep the owner window intentionally tight so we do not drift into authorization pages.
        owner_window = lines[idx + 1:min(len(lines), idx + 12)]

        # Pass 1: direct line-by-line scan. Catches clean isolated rows like "Daniel Neal".
        for line in owner_window:
            candidate = clean_value(line)

            if looks_like_person_name(candidate):
                return candidate

        # Pass 2: split noisy OCR rows into chunks and scan each chunk.
        for line in owner_window:
            candidate = clean_value(line)

            # Skip rows that are obviously only labels/headers unless they contain title-case names.
            lowered = candidate.lower()
            is_headerish = any(label in lowered for label in [
                "personal annual revenue",
                "credit score",
                "social security",
                "dob",
                "title/percentage",
                "percentage",
                "ownership",
                "address",
                "city/county",
                "home phone",
                "rent/own",
            ])

            if is_headerish and not re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", candidate):
                continue

            chunks = re.split(r"\s{2,}|\t|\|", candidate)
            for chunk in chunks:
                chunk = clean_value(chunk)
                chunk = remove_phone_email_and_labels(chunk)

                if looks_like_person_name(chunk):
                    return chunk

                names = extract_capitalized_person_names(chunk)
                if names:
                    return names[0]

        # Pass 3: joined owner window. Useful when OCR merges table text.
        # Still uses strict person-vs-business logic.
        owner_text = "\n".join(owner_window)
        names = extract_capitalized_person_names(owner_text)
        if names:
            return names[0]

    # Last fallback: search only a small section near OWNER 1 in full text.
    # Keep it tight to avoid pulling authorization/company headers.
    owner_area_match = re.search(
        r"OWNER\s*1(.{0,450})",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if owner_area_match:
        owner_area = owner_area_match.group(1)
        names = extract_capitalized_person_names(owner_area)
        if names:
            return names[0]

    return ""


def parse_business_start_date(start_date: str):
    """
    Accepts:
    - MM/YYYY, like 10/2021
    - M/YYYY, like 3/2020
    - YYYY, like 2021

    Year-only assumes January of that year.
    """
    start_date = clean_value(start_date)

    if re.fullmatch(r"\d{1,2}/\d{4}", start_date):
        month, year = start_date.split("/")
        month = int(month)
        year = int(year)
        if 1 <= month <= 12 and 1900 <= year <= datetime.now().year:
            return year, month

    if re.fullmatch(r"\d{4}", start_date):
        year = int(start_date)
        if 1900 <= year <= datetime.now().year:
            return year, 1

    return None


def extract_years_in_business(start_date: str) -> str:
    parsed = parse_business_start_date(start_date)
    if not parsed:
        return ""

    start_year, start_month = parsed
    today = datetime.now()
    months = (today.year - start_year) * 12 + (today.month - start_month)

    if months < 0:
        return ""

    years = months // 12
    rem_months = months % 12

    if years == 0:
        return f"{rem_months} months"
    if rem_months == 0:
        return f"{years} years"
    return f"{years} years, {rem_months} months"


def field_confidence(field_name: str, value: str) -> str:
    value = clean_value(value)

    if not value:
        return "Missing"

    if is_probably_label_text(value):
        return "Needs Review"

    if field_name == "Email":
        return "High" if re.fullmatch(r"[A-Za-z0-9_*.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value) else "Needs Review"

    if field_name == "Phone":
        real_phone = re.fullmatch(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)", value)
        masked_phone = re.fullmatch(r"\(?\*{3}\)?\s*\*{3}[-.\s]?\d{4}", value)
        return "High" if real_phone or masked_phone else "Needs Review"

    if field_name == "Credit Score":
        return "High" if is_valid_credit_score(value) else "Needs Review"

    if field_name == "Monthly Revenue":
        raw = value.replace("$", "").replace(",", "").strip()
        return "High" if is_valid_monthly_sales(raw) else "Needs Review"

    if field_name == "Business Start Date":
        return "High" if is_valid_mm_yyyy(value) else "Needs Review"

    if field_name == "EIN":
        return "High" if re.fullmatch(r"\d{2}-\d{7}", value) else "Needs Review"

    if field_name == "Owner Name":
        return "Needs Review" if looks_like_person_name(value) else "Missing"

    if field_name == "Business Name":
        if re.search(r"\b(llc|inc|corp|corporation|company|co\.|ltd)\b", value, flags=re.IGNORECASE):
            return "High"
        return "Needs Review"

    if field_name == "Years in Business":
        return "High" if re.search(r"\d", value) else "Needs Review"

    if field_name == "Business Address":
        has_street = looks_like_street_address(value)
        has_zip = bool(find_first_valid_zip(value))
        if has_street and has_zip:
            return "High"
        if has_street:
            return "Needs Review"
        return "Needs Review"

    if field_name == "Industry":
        if (
            value
            and not is_probably_label_text(value)
            and not re.search(r"yes|no|tax id|seasonal|\d{2}-\d{7}|\d{5}", value, flags=re.IGNORECASE)
            and re.fullmatch(r"[A-Za-z&/ -]+", value)
        ):
            return "Needs Review"
        return "Missing"

    if field_name == "Requested Funding Amount":
        raw = value.replace("$", "").replace(",", "").strip()
        return "High" if is_valid_monthly_sales(raw) else "Needs Review"

    return "Needs Review"


def confidence_icon(confidence: str) -> str:
    if confidence == "High":
        return "✅"
    if confidence == "Missing":
        return "❌"
    return "⚠️"



def extract_business_type_or_industry(lines: list[str]) -> str:
    """
    Industry is helpful, but not transfer-critical.
    Only return it when clean/reliable. Otherwise return blank so it stays hidden.
    """

    joined = "\n".join(lines)

    # Safe business-type/entity fallback only when the form clearly marks it.
    checked_patterns = [
        ("LLC", r"(?:☑|✓|■|●|◉|[Xx])\s*L\.?L\.?C\.?"),
        ("Corporation", r"(?:☑|✓|■|●|◉|[Xx])\s*Corporation"),
        ("Partnership", r"(?:☑|✓|■|●|◉|[Xx])\s*Partnership"),
        ("Sole Proprietor", r"(?:☑|✓|■|●|◉|[Xx])\s*Sole Proprietor"),
    ]

    for label, pattern in checked_patterns:
        if re.search(pattern, joined, flags=re.IGNORECASE):
            return label

    raw = get_text_after_label_line(
        lines,
        ["Nature of Business", "Industry", "Business Type", "Type of Business"],
        lookahead=3,
    )
    cleaned = remove_phone_email_and_labels(raw)

    # Reject rows that are really neighboring labels or unrelated values.
    bad_terms = [
        "seasonal business",
        "tax id",
        "monthly total sales",
        "advance amount",
        "business address",
        "date/year started",
        "use of funds",
        "yes",
        "no",
    ]

    if not cleaned:
        return ""

    if any(term in cleaned.lower() for term in bad_terms):
        return ""

    if re.search(r"\d{2}-\d{7}|\d{3}-\d{2}-\d{4}|\d{5}", cleaned):
        return ""

    # Only accept short, normal text like Restaurant, Construction, Retail, Trucking.
    if (
        not is_probably_label_text(cleaned)
        and 1 <= len(cleaned.split()) <= 4
        and re.fullmatch(r"[A-Za-z&/ -]+", cleaned)
    ):
        return cleaned

    return ""

def extract_requested_funding_amount(lines: list[str]) -> str:
    amount = find_pattern_near_label(
        lines,
        ["Advance Amount Requested", "Requested Funding Amount", "Funding Amount", "Amount Requested", "Use of Funds"],
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{4,9})\b",
        lookahead=6,
    )
    return format_currency(amount) if amount else ""

def extract_transfer_fields_strict(pdf_bytes: bytes) -> dict:
    full_text = extract_pdf_text(pdf_bytes)
    lines = extract_pdf_lines_for_transfer(pdf_bytes)

    emails = detect_masked_or_real_emails(full_text)
    phones = detect_masked_or_real_phones(full_text)

    business_start_date = find_pattern_near_label(
        lines,
        ["Date/Year Started", "Business Start Date", "Date Established", "In Business Since"],
        r"\b(\d{1,2}/\d{4}|\d{4})\b",
        lookahead=5,
        validator=lambda v: parse_business_start_date(v) is not None,
    )

    monthly_sales = find_pattern_near_label(
        lines,
        ["Monthly Total Sales", "Monthly Revenue", "Monthly Sales"],
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{4,9})\b",
        lookahead=5,
        validator=lambda v: is_valid_monthly_sales(v.replace(",", "").replace("$", "").strip()),
    )

    credit_score = find_credit_score_near_label(
        lines,
        ["Credit score", "Credit Score", "FICO", "Personal Credit Score"],
    )

    ein = find_pattern_near_label(
        lines,
        ["Tax ID Number", "EIN", "Federal Tax ID"],
        r"\b(\d{2}-\d{7})\b",
        lookahead=5,
    )

    fields = {
        "Business Name": extract_business_name_strict(lines, full_text),
        "Owner Name": extract_owner_name_strict(lines, full_text),
        "Business Address": extract_business_address_strict(lines, full_text),
        "Phone": phones[0] if phones else "",
        "Email": emails[0] if emails else "",
        "EIN": ein,
        "Business Start Date": business_start_date,
        "Years in Business": extract_years_in_business(business_start_date),
        "Monthly Revenue": format_currency(monthly_sales) if monthly_sales else "",
        "Credit Score": credit_score,
        "Industry": extract_business_type_or_industry(lines),
        "Requested Funding Amount": extract_requested_funding_amount(lines),
    }

    return fields


def extract_possible_template_labels(pdf_bytes: bytes) -> list[str]:
    text = extract_pdf_text(pdf_bytes)
    found = []

    checks = [
        ("Business Name", ["business name", "legal business name"]),
        ("Owner Name", ["owner name", "principal name", "applicant name"]),
        ("Business Address", ["business address", "street address"]),
        ("Phone", ["phone", "cell phone", "business phone"]),
        ("Email", ["email"]),
        ("EIN", ["ein", "tax id"]),
        ("Business Start Date", ["date/year started", "business start", "date established"]),
        ("Years in Business", ["years in business", "how long"]),
        ("Monthly Revenue", ["monthly revenue", "monthly total sales", "monthly sales"]),
        ("Credit Score", ["credit score", "fico"]),
        ("Industry", ["industry", "nature of business"]),
        ("Requested Funding Amount", ["advance amount", "funding amount", "amount requested"]),
    ]

    lower_text = text.lower()
    for standard_name, terms in checks:
        if any(term in lower_text for term in terms):
            found.append(standard_name)

    # Always include defaults so the dropdown is predictable.
    for default in DESTINATION_FIELD_OPTIONS:
        if default not in found:
            found.append(default)

    return found


def render_transfer_match_row(field_name: str, value: str, destination_options: list[str]):
    confidence = field_confidence(field_name, value)
    default_use = confidence != "Missing"

    st.markdown(f"**{field_name} — {confidence_icon(confidence)} {confidence}**")

    col_use, col_value, col_dest = st.columns([0.6, 2.2, 2.2])

    with col_use:
        use_field = st.checkbox(
            "Use",
            value=default_use,
            key=f"use_transfer_{field_name}"
        )

    with col_value:
        updated_value = st.text_input(
            "Source value",
            value=value,
            key=f"value_transfer_{field_name}"
        )

    with col_dest:
        default_index = destination_options.index(field_name) if field_name in destination_options else 0
        destination = st.selectbox(
            "Destination on blank app",
            destination_options,
            index=default_index,
            key=f"destination_transfer_{field_name}"
        )

    st.divider()

    return {
        "field": field_name,
        "use": use_field,
        "value": updated_value,
        "destination": destination,
        "confidence": field_confidence(field_name, updated_value),
    }


# =========================
# TAB 2 — DOCUMENT TRANSFER BETA
# =========================

with tab2:

    st.subheader("Document Transfer (Beta)")
    st.caption(
        "Upload a completed app and a blank app. FundLock will suggest field matches, then you confirm only what looks right."
    )

    st.info(
        "Broker-friendly beta: upload both apps, review only the suggested transfer matches, fix anything missing, then confirm the transfer map."
    )

    col1, col2 = st.columns(2)

    with col1:
        source_app = st.file_uploader(
            "1. Upload completed application",
            type=["pdf"],
            key="source_app_beta_strict"
        )

    with col2:
        target_template = st.file_uploader(
            "2. Upload blank application",
            type=["pdf"],
            key="target_template_beta_strict"
        )


    if source_app:
        st.success("Completed application uploaded.")

    if target_template:
        st.success("Blank application uploaded.")

    if source_app and target_template:

        st.markdown("---")
        st.subheader("Suggested Matches")

        source_pdf_bytes = source_app.getvalue()
        target_pdf_bytes = target_template.getvalue()

        try:
            extracted_fields = extract_transfer_fields_strict(source_pdf_bytes)
        except Exception as e:
            st.error(f"Could not extract fields from completed application: {e}")
            extracted_fields = {}

        try:
            destination_options = extract_possible_template_labels(target_pdf_bytes)
        except Exception:
            destination_options = DESTINATION_FIELD_OPTIONS

        if extracted_fields:
            confidence_counts = {
                "High": 0,
                "Needs Review": 0,
                "Missing": 0,
            }

            for field_name, field_value in extracted_fields.items():
                confidence_counts[field_confidence(field_name, field_value)] += 1

            st.success(
                f"Found {confidence_counts['High']} high-confidence fields. "
                f"{confidence_counts['Needs Review']} need review. "
                f"{confidence_counts['Missing']} are missing."
            )

            st.caption(
                "Only populated fields are shown by default. Blank fields are still checked behind the scenes, "
                "but hidden so the broker flow stays clean."
            )

            confirmed_matches = []

            preferred_order = [
                "Business Name",
                "Owner Name",
                "Business Address",
                "Phone",
                "Email",
                "EIN",
                "Business Start Date",
                "Years in Business",
                "Monthly Revenue",
                "Credit Score",
                "Industry",
                "Requested Funding Amount",
            ]

            populated_fields = [
                field_name for field_name in preferred_order
                if (
                    field_name in extracted_fields
                    and clean_value(extracted_fields.get(field_name, ""))
                    and field_confidence(field_name, extracted_fields.get(field_name, "")) != "Missing"
                )
            ]

            missing_fields = [
                field_name for field_name in preferred_order
                if (
                    field_name in extracted_fields
                    and (
                        not clean_value(extracted_fields.get(field_name, ""))
                        or field_confidence(field_name, extracted_fields.get(field_name, "")) == "Missing"
                    )
                )
            ]

            if populated_fields:
                with st.expander("Review suggested matches", expanded=True):
                    for field_name in populated_fields:
                        confirmed_matches.append(
                            render_transfer_match_row(
                                field_name,
                                extracted_fields.get(field_name, ""),
                                destination_options,
                            )
                        )
            else:
                st.warning("No populated fields were found. Try a clearer completed application PDF.")

            if missing_fields:
                with st.expander(f"Hidden missing fields ({len(missing_fields)})", expanded=False):
                    st.caption(
                        "These fields were not found in the completed app or were blank. "
                        "They are hidden from the main review to keep the transfer workflow clean."
                    )
                    for field_name in missing_fields:
                        st.write(f"❌ {field_name}")

            st.markdown("---")


            if st.button("Confirm Matches", key="confirm_matches_beta_strict"):
                usable_matches = [
                    match for match in confirmed_matches
                    if match["use"]
                    and match["destination"] != "Do Not Transfer"
                    and clean_value(match["value"])
                ]

                st.success(f"{len(usable_matches)} fields confirmed for transfer.")

                with st.expander("View confirmed transfer map", expanded=True):
                    for match in usable_matches:
                        st.write(
                            f"**{match['field']}** → **{match['destination']}**: "
                            f"{match['value']} "
                            f"({match['confidence']})"
                        )


                st.caption(
                    "Next phase: use these confirmed mappings to write values into exact boxes on the blank PDF."
                )

        else:
            st.warning("No fields were extracted. Try a clearer completed application PDF.")

    else:
        st.caption(
            "Waiting for both files. Upload the completed app first, then the blank app you want to transfer into."
        )
