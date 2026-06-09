import streamlit as st
import fitz  # PyMuPDF
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import re


st.set_page_config(page_title="FundLock", layout="centered")

st.title("MCA FundLock 🔒")

st.write(
    "Protect sensitive merchant data. Seamless Application Transfer. Deliver lender-ready packages."
)

st.markdown(
    "*Built for MCA Brokers, Processors and ISO Teams.*"
)

tab1, tab2, tab3 = st.tabs([
    "Protect Documents",
    "Document Transfer (Beta)",
    "MCA Calculator"
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


def find_smart_watermark_y(page) -> float:
    """
    Pick a near-center watermark position with the least text underneath it.

    Surgical Tab 1 behavior:
    - Keep the watermark close to center.
    - Allow only controlled up/down movement.
    - Avoid sensitive ID rows like EIN, Tax ID, SSN, and Federal Tax ID.
    """
    rect = page.rect
    words = page.get_text("words", sort=True)

    # Keep watermark near center only: roughly +/- 15% from center.
    candidate_positions = [0.35, 0.425, 0.50, 0.575, 0.65]

    # If the PDF has no readable text layer, use a slightly-lower center fallback.
    # This avoids the top form header without pushing the watermark too low.
    if not words:
        return rect.height * 0.60

    sensitive_terms = {
        "ein",
        "tax",
        "taxid",
        "federal",
        "ssn",
        "social",
        "security",
    }

    # Build sensitive y-zones from rows containing EIN / Tax ID / SSN style labels.
    # These zones are heavily penalized so the watermark does not cross key ID fields.
    sensitive_zones = []
    for word in words:
        x0, y0, x1, y1, word_text, *_ = word
        normalized_word = re.sub(r"[^a-z0-9]", "", str(word_text).lower())
        if normalized_word in sensitive_terms:
            sensitive_zones.append((max(0, y0 - 55), min(rect.height, y1 + 55)))

    best_pct = 0.50
    lowest_score = None

    for pct in candidate_positions:
        candidate_y = rect.height * pct

        # Score text density around the watermark baseline.
        # Bigger window catches rows the large watermark would visually cross.
        band_top = candidate_y - 45
        band_bottom = candidate_y + 45

        score = 0

        for word in words:
            x0, y0, x1, y1, word_text, *_ = word
            if y1 >= band_top and y0 <= band_bottom:
                score += 1

        # Heavy penalty when the watermark band crosses sensitive ID rows.
        for zone_top, zone_bottom in sensitive_zones:
            if band_bottom >= zone_top and band_top <= zone_bottom:
                score += 1000

        # Gentle preference to move a little higher rather than lower when scores are close.
        # This helps avoid lower-middle EIN/SSN rows on MCA apps.
        if pct > 0.50:
            score += 2

        # Prefer lower text overlap. If tied, stay close to center; if still tied, prefer higher.
        if (
            lowest_score is None
            or score < lowest_score
            or (
                score == lowest_score
                and (abs(pct - 0.50), pct) < (abs(best_pct - 0.50), best_pct)
            )
        ):
            lowest_score = score
            best_pct = pct

    return rect.height * best_pct


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
    y = find_smart_watermark_y(page)

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



def normalize_business_address_output(street: str, city_state_zip: str, recovered_zip: str = "") -> str:
    street = clean_address_value(street)
    city_state_zip = clean_address_value(city_state_zip)
    recovered_zip = normalize_zip(recovered_zip)

    # Restore common street suffix if OCR separated/dropped it.
    if re.search(r"\bMain\b", street, flags=re.IGNORECASE) and not re.search(
        r"\b(street|st\.|road|rd\.|avenue|ave\.|blvd|drive|dr\.|lane|ln\.|way|court|ct\.)\b",
        street,
        flags=re.IGNORECASE,
    ):
        street = re.sub(r"\bMain\b", "Main Street", street, flags=re.IGNORECASE)

    zip_match = re.search(r"\b\d{5}\b", city_state_zip)

    if zip_match:
        # Strict rule: keep the first valid 5-digit ZIP and cut everything after it.
        city_state_zip = city_state_zip[:zip_match.end()]
    else:
        # Repair common OCR issue: 5-digit ZIP is visually present, but text extracted only 4 digits.
        broken_zip_match = re.search(r"\b(\d{4})\b", city_state_zip)

        if broken_zip_match and recovered_zip and recovered_zip.startswith(broken_zip_match.group(1)):
            city_state_zip = (
                city_state_zip[:broken_zip_match.start()]
                + recovered_zip
            )
        elif recovered_zip:
            # If no valid ZIP in the line but the PDF has one nearby, append the recovered strict ZIP.
            city_state_zip = clean_value(f"{re.sub(r'\\b\\d{4}\\b.*$', '', city_state_zip).strip(' ,:-')} {recovered_zip}")
        else:
            # If we cannot repair the ZIP, remove the broken 4-digit fragment entirely.
            city_state_zip = re.sub(r"\b\d{4}\b.*$", "", city_state_zip).strip(" ,:-")

    # Remove obvious OCR leftovers.
    city_state_zip = re.sub(r"\s+", " ", city_state_zip).strip(" ,:-")
    city_state_zip = re.sub(r",\s*[A-Za-z]{1,2}$", "", city_state_zip).strip(" ,:-")

    combined = clean_value(f"{street}, {city_state_zip}") if city_state_zip else street

    # Final hard safety: if a 5-digit ZIP exists, cut everything after it.
    zip_match_combined = re.search(r"\b\d{5}\b", combined)
    if zip_match_combined:
        combined = combined[:zip_match_combined.end()]

    # If only a 4-digit ZIP remains, remove it. Never display partial ZIPs.
    if not re.search(r"\b\d{5}\b", combined):
        combined = re.sub(r"\b\d{4}\b.*$", "", combined).strip(" ,:-")

    return clean_value(combined).strip(" ,:-")

def extract_business_address_strict(lines: list[str], full_text: str) -> str:
    """
    MCA apps often split business address across:
    Street | City | State | Zip

    ZIP rules:
    - Only trust 5-digit ZIPs.
    - If OCR extracts 4 digits like 7310 but full text contains 73102, repair to 73102.
    - Never display a 4-digit ZIP fragment.
    """
    street = ""
    city_state_zip = ""
    recovered_zip = ""

    idx = find_line_index(lines, ["Business Address: Street", "Business Address Street"])
    if idx is None:
        idx = find_line_index(lines, ["Business Address", "Street"])

    nearby_lines = []

    if idx is not None:
        nearby_lines = lines[idx + 1:min(len(lines), idx + 10)]

        # Try to recover a strict 5-digit ZIP from nearby business-address lines.
        for line in nearby_lines:
            z = find_first_valid_zip(line)
            if z:
                recovered_zip = z
                break

        for candidate_line in nearby_lines:
            candidate = clean_address_value(candidate_line)
            if not candidate or is_probably_label_text(candidate):
                continue

            if not street and looks_like_street_address(candidate):
                street = candidate
                continue

            if street and not city_state_zip:
                # Prefer a line with city/state/ZIP info.
                if find_first_valid_zip(candidate):
                    city_state_zip = candidate
                    break

                # Keep a line with a broken ZIP fragment so normalizer can repair it later.
                if re.search(r"\b\d{4}\b", candidate):
                    city_state_zip = candidate
                    continue

                # If line looks like city/state but has no ZIP, keep it as fallback.
                if re.search(r"[A-Za-z]{3,}", candidate) and not looks_like_street_address(candidate):
                    city_state_zip = candidate

    # Fallback: locate a street-looking line anywhere, then look nearby for ZIP line.
    if not street:
        for i, line in enumerate(lines):
            candidate = clean_address_value(line)
            if looks_like_street_address(candidate):
                street = candidate
                fallback_window = lines[i + 1:min(len(lines), i + 7)]

                for possible_zip_line in fallback_window:
                    z = find_first_valid_zip(possible_zip_line)
                    if z:
                        recovered_zip = z
                        city_state_zip = clean_address_value(possible_zip_line)
                        break

                if not city_state_zip:
                    for possible_city_line in fallback_window:
                        possible_city_line = clean_address_value(possible_city_line)
                        if re.search(r"\b\d{4}\b", possible_city_line) or re.search(r"[A-Za-z]{3,}", possible_city_line):
                            city_state_zip = possible_city_line
                            break

                break

    if not street:
        return ""

    # Last resort: recover ZIP from full text.
    if not recovered_zip:
        # Prefer ZIPs that appear near business address in the full text if possible.
        business_area = full_text
        business_match = re.search(
            r"Business Address.{0,500}",
            full_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if business_match:
            business_area = business_match.group(0)

        recovered_zip = find_first_valid_zip(business_area)

    return normalize_business_address_output(street, city_state_zip, recovered_zip)

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
        has_zip = bool(re.search(r"\b\d{5}\b", value))
        has_partial_zip = bool(re.search(r"\b\d{4}\b", value)) and not has_zip

        if has_street and has_zip and not has_partial_zip:
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
# TAB 2 — TMT PDF FILLING HELPERS
# Surgical next step only: extraction/review/mapping above remains unchanged.
# This section converts reviewed generic fields into exact TMT boxes.
# =========================

# TMT coordinate profile for the uploaded TMT blank application.
# PyMuPDF coordinates use PDF points: x moves left→right, y moves top→bottom.
# These coordinates intentionally write only into supported TMT boxes so values do not drift
# into the wrong fields like City, Business Address, or Services Sold.
TMT_FIELD_COORDS = {
    # Basic Information
    "business_name": {"page": 0, "rect": (82, 112, 250, 133), "fontsize": 8, "min_fontsize": 5.5},
    "first_name": {"page": 0, "rect": (58, 166, 190, 187), "fontsize": 8, "min_fontsize": 5.5},
    "last_name": {"page": 0, "rect": (210, 166, 345, 187), "fontsize": 8, "min_fontsize": 5.5},
    "phone": {"page": 0, "rect": (390, 141, 555, 158), "fontsize": 8, "min_fontsize": 5.5},
    "email": {"page": 0, "rect": (388, 171, 556, 188), "fontsize": 7, "min_fontsize": 5.0},

    # Business Information section
    "services_sold": {"page": 0, "rect": (360, 322, 552, 339), "fontsize": 8, "min_fontsize": 5.5},
    "business_street": {"page": 0, "rect": (58, 356, 270, 377), "fontsize": 7, "min_fontsize": 5.0},
    "business_state": {"page": 0, "rect": (287, 356, 345, 377), "fontsize": 8, "min_fontsize": 5.5},
    "business_city": {"page": 0, "rect": (365, 356, 466, 377), "fontsize": 7, "min_fontsize": 5.0},
    "business_zip": {"page": 0, "rect": (482, 356, 552, 377), "fontsize": 8, "min_fontsize": 5.5},
    "business_start_date": {"page": 0, "rect": (58, 406, 190, 428), "fontsize": 8, "min_fontsize": 5.5},

    # Ownership Verification section
    "ein": {"page": 0, "rect": (327, 506, 438, 527), "fontsize": 7, "min_fontsize": 5.0},
}

# Tiny visual nudges by TMT box.
# Positive x moves text right. Positive y moves text down.
TMT_FIELD_OFFSETS = {
    "business_name": (4, 2),
    "first_name": (4, 2),
    "last_name": (4, 2),
    "phone": (4, 1),
    "email": (4, 1),
    "services_sold": (4, 1),
    "business_street": (8, 2),
    "business_state": (4, 2),
    "business_city": (4, 2),
    "business_zip": (4, 2),
    "business_start_date": (4, 2),
    "ein": (4, 2),
}

# Minimal ZIP rescue for common TMT test output where OCR returns
# "Oklahoma 73102" without the state abbreviation.
ZIP_CITY_STATE_FALLBACKS = {
    "73102": {"city": "Oklahoma City", "state": "OK"},
}


def split_owner_name_for_tmt(owner_name: str) -> tuple[str, str]:
    """Split a reviewed owner name into TMT First Name / Last Name boxes."""
    owner_name = clean_value(owner_name)
    if not owner_name:
        return "", ""

    # Remove accidental extra data if it was pasted into the owner field.
    owner_name = re.sub(r"\b\d{3}\b", " ", owner_name)
    owner_name = re.sub(r"\b\d{2}-\d{7}\b", " ", owner_name)
    owner_name = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", owner_name)
    owner_name = clean_value(owner_name)

    parts = owner_name.split()
    if len(parts) == 1:
        return parts[0], ""

    first_name = parts[0]
    last_name = " ".join(parts[1:])
    return first_name, last_name


def parse_business_address_for_tmt(address: str) -> dict:
    """
    Split reviewed Business Address into TMT street/state/city/zip boxes.

    Handles common formats:
    - 629 W Main Street Oklahoma 73102
    - 629 W Main Street, Oklahoma City, OK 73102
    - 629 W Main Street Oklahoma City OK 73102

    Visual-safe rule: if city/state cannot be trusted, keep uncertain text out of
    the wrong TMT boxes instead of forcing it into City or State.
    """
    address = clean_value(address)

    result = {
        "street": "",
        "city": "",
        "state": "",
        "zip": "",
    }

    if not address:
        return result

    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", address)
    if zip_match:
        result["zip"] = zip_match.group(1)
        before_zip = clean_value(address[:zip_match.start()].strip(" ,"))
    else:
        before_zip = address

    state_match = re.search(r"\b([A-Z]{2})\b\s*$", before_zip)
    if state_match:
        result["state"] = state_match.group(1)
        before_zip = clean_value(before_zip[:state_match.start()].strip(" ,"))

    # Prefer comma-separated parsing when available.
    parts = [clean_value(p) for p in before_zip.split(",") if clean_value(p)]

    if len(parts) >= 2:
        result["street"] = parts[0]
        city_candidate = parts[-1]
        if not result["state"]:
            state_in_city = re.search(r"\b([A-Z]{2})\b\s*$", city_candidate)
            if state_in_city:
                result["state"] = state_in_city.group(1)
                city_candidate = clean_value(city_candidate[:state_in_city.start()].strip(" ,"))
        result["city"] = city_candidate
    else:
        # Non-comma fallback: split after a street suffix.
        street_suffix_pattern = (
            r"\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|blvd|boulevard|drive|dr\.?|"
            r"lane|ln\.?|way|court|ct\.?|place|pl\.?|parkway|pkwy\.?)\b"
        )
        suffix_matches = list(re.finditer(street_suffix_pattern, before_zip, flags=re.IGNORECASE))

        if suffix_matches:
            last_suffix = suffix_matches[-1]
            result["street"] = clean_value(before_zip[:last_suffix.end()])
            result["city"] = clean_value(before_zip[last_suffix.end():].strip(" ,"))
        else:
            # Last resort: keep the whole value in street so it is visible but never lands in City.
            result["street"] = before_zip

    # ZIP-based cleanup for the observed TMT beta case. This fixes:
    # street = 629 W Main Street, city = Oklahoma, zip = 73102
    # into city = Oklahoma City, state = OK.
    fallback = ZIP_CITY_STATE_FALLBACKS.get(result.get("zip", ""))
    if fallback:
        city_lower = clean_value(result.get("city", "")).lower()
        fallback_city_lower = fallback["city"].lower()

        if not result.get("state"):
            result["state"] = fallback["state"]

        if (
            not result.get("city")
            or city_lower in fallback_city_lower
            or fallback_city_lower in city_lower
        ):
            result["city"] = fallback["city"]

    return result


def normalize_phone_for_tmt(phone: str) -> str:
    phone = clean_value(phone)
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone




def target_template_has_transfer_field(target_pdf_bytes: bytes, field_type: str) -> bool:
    """
    Only allow optional contact transfer when the blank target app actually
    contains a clear phone/email label. This prevents values from being
    written into whitespace on templates that do not support those fields.
    """
    try:
        text = extract_pdf_text(target_pdf_bytes).lower()
    except Exception:
        return False

    if field_type == "phone":
        phone_terms = [
            "phone",
            "business phone",
            "cell phone",
            "mobile",
            "telephone",
        ]
        return any(term in text for term in phone_terms)

    if field_type == "email":
        email_terms = [
            "email",
            "e-mail",
            "email address",
            "e-mail address",
        ]
        return any(term in text for term in email_terms)

    return False


def prepare_tmt_values_from_matches(matches: list[dict], target_pdf_bytes: bytes) -> dict:
    """
    Convert reviewed generic matches into exact TMT fields.
    This prevents unsupported fields like Credit Score, Monthly Revenue, or Years in Business
    from being written into the wrong TMT boxes.
    """
    reviewed = {}

    for match in matches:
        if not match.get("use"):
            continue

        destination = clean_value(match.get("destination", ""))
        value = clean_value(match.get("value", ""))

        if not value or destination == "Do Not Transfer":
            continue

        reviewed[destination] = value

    tmt_values = {}

    if reviewed.get("Business Name"):
        tmt_values["business_name"] = reviewed["Business Name"]

    if reviewed.get("Owner Name"):
        first_name, last_name = split_owner_name_for_tmt(reviewed["Owner Name"])
        if first_name:
            tmt_values["first_name"] = first_name
        if last_name:
            tmt_values["last_name"] = last_name

    if reviewed.get("Business Address"):
        parsed_address = parse_business_address_for_tmt(reviewed["Business Address"])
        if parsed_address.get("street"):
            tmt_values["business_street"] = parsed_address["street"]
        if parsed_address.get("state"):
            tmt_values["business_state"] = parsed_address["state"]
        if parsed_address.get("city"):
            tmt_values["business_city"] = parsed_address["city"]
        if parsed_address.get("zip"):
            tmt_values["business_zip"] = parsed_address["zip"]

    if reviewed.get("Phone") and target_template_has_transfer_field(target_pdf_bytes, "phone"):
        tmt_values["phone"] = normalize_phone_for_tmt(reviewed["Phone"])

    if reviewed.get("Email") and target_template_has_transfer_field(target_pdf_bytes, "email"):
        tmt_values["email"] = reviewed["Email"]

    if reviewed.get("EIN"):
        tmt_values["ein"] = reviewed["EIN"]

    if reviewed.get("Business Start Date"):
        tmt_values["business_start_date"] = reviewed["Business Start Date"]

    # TMT has "Services Sold". The current extraction label is "Industry".
    # Only write it when the reviewed value looks like services/industry text, not an entity type.
    industry_value = reviewed.get("Industry", "")
    if industry_value and industry_value.lower() not in {
        "llc", "l.l.c.", "corporation", "partnership", "sole proprietor", "sole proprietorship", "other"
    }:
        tmt_values["services_sold"] = industry_value

    return tmt_values


def fit_font_size_to_rect(value: str, rect: fitz.Rect, max_fontsize: float = 8, min_fontsize: float = 5) -> float:
    """Auto-shrink text so long values stay inside the TMT field frame."""
    value = clean_value(value)
    if not value:
        return max_fontsize

    available_width = max(rect.width - 4, 1)
    fontsize = float(max_fontsize)

    while fontsize > float(min_fontsize):
        text_width = fitz.get_text_length(value, fontsize=fontsize)
        if text_width <= available_width:
            return fontsize
        fontsize -= 0.5

    return float(min_fontsize)


def draw_text_in_rect(page, rect_tuple, value: str, fontsize: int = 8, field_key: str = "", min_fontsize: float = 5):
    """Insert reviewed transfer value inside a fixed PDF rectangle with visual-safe fitting."""
    value = clean_value(value)
    if not value:
        return

    rect = fitz.Rect(*rect_tuple)

    x_offset, y_offset = TMT_FIELD_OFFSETS.get(field_key, (4, 1))
    adjusted_rect = fitz.Rect(
        rect.x0 + x_offset,
        rect.y0 + y_offset,
        rect.x1 - 2,
        rect.y1 - 1,
    )

    fitted_fontsize = fit_font_size_to_rect(
        value,
        adjusted_rect,
        max_fontsize=fontsize,
        min_fontsize=min_fontsize,
    )

    page.insert_textbox(
        adjusted_rect,
        value,
        fontsize=fitted_fontsize,
        color=(0, 0, 0),
        align=0,
        overlay=True,
    )


def fill_tmt_pdf_with_confirmed_matches(target_pdf_bytes: bytes, matches: list[dict]) -> str:
    """
    Takes the broker-reviewed confirmed matches and writes only supported values
    into exact TMT boxes. Unsupported reviewed fields are intentionally skipped.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(target_pdf_bytes)
        temp_path = tmp_file.name

    doc = fitz.open(temp_path)
    tmt_values = prepare_tmt_values_from_matches(matches, target_pdf_bytes)

    for tmt_key, value in tmt_values.items():
        coord = TMT_FIELD_COORDS.get(tmt_key)
        if not coord:
            continue

        page_index = coord.get("page", 0)
        if 0 <= page_index < len(doc):
            page = doc[page_index]
            draw_text_in_rect(
                page,
                coord["rect"],
                value,
                fontsize=coord.get("fontsize", 8),
                field_key=tmt_key,
                min_fontsize=coord.get("min_fontsize", 5),
            )

    output_path = temp_path.replace(".pdf", "_tmt_filled.pdf")
    doc.save(output_path)
    doc.close()

    return output_path

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

                        if match["field"] == match["destination"]:
                            st.write(
                                f"**{match['destination']}**: "
                                f"{match['value']}"
                            )

                        else:
                            st.write(
                                f"**{match['field']}** → **{match['destination']}**: "
                                f"{match['value']}"
                             )


                st.session_state["tmt_confirmed_transfer_matches"] = usable_matches
                st.session_state["tmt_target_pdf_bytes"] = target_pdf_bytes
                st.session_state["tmt_target_file_name"] = target_template.name

                st.caption(
                    "Confirmed. Next step unlocked below: create the filled application from this reviewed map."
                )

            if st.session_state.get("tmt_confirmed_transfer_matches"):
                st.markdown("---")
                st.subheader("Next Step: Generate Application")
                st.caption(
                    "This uses only the reviewed/confirmed values above and writes them into the selected application template."
                )

                if st.button("Create Filled App", key="create_filled_app_beta_strict"):
                    try:
                        filled_pdf_path = fill_tmt_pdf_with_confirmed_matches(
                            st.session_state["tmt_target_pdf_bytes"],
                            st.session_state["tmt_confirmed_transfer_matches"],
                        )

                        st.success("Filled application created.")

                        download_name = st.session_state.get(
                            "target_file_name",
                            "APP_Blank.pdf",
                        ).replace(".pdf", "_filled.pdf")

                        with open(filled_pdf_path, "rb") as f:
                            st.download_button(
                                label="Download Completed Application",
                                data=f,
                                file_name=download_name,
                                mime="application/pdf",
                            )

                    except Exception as e:
                        st.error(f"Could not create filled TMT application: {e}")

        else:
            st.warning("No fields were extracted. Try a clearer completed application PDF.")

    else:
        st.caption(
            "Waiting for both files. Upload the completed app first, then the blank app you want to transfer into."
        )


# =========================
# TAB 3 — MCA CALCULATOR
# Standalone sticky broker tool.
# No dependency on Tab 1 or Tab 2.
# =========================

def format_money(value: float) -> str:
    """Format numeric calculator output as dollars."""
    try:
        return "${:,.2f}".format(float(value))
    except Exception:
        return "$0.00"


def format_percent(value: float) -> str:
    """Format calculator percentages cleanly."""
    try:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return "0"


with tab3:

    st.subheader("MCA Calculator")
    st.caption(
        "Quick deal math for brokers quoting MCA terms on the phone. Enter the terms, review the snapshot, then copy/paste the deal summary."
    )

    calc_col1, calc_col2 = st.columns(2)

    with calc_col1:
        funding_amount = st.number_input(
            "Funding amount",
            min_value=0.0,
            value=25000.0,
            step=1000.0,
            format="%.2f",
            key="mca_calc_funding_amount",
        )

        factor_rate = st.number_input(
            "Factor rate",
            min_value=1.000,
            max_value=3.000,
            value=1.350,
            step=0.001,
            format="%.3f",
            key="mca_calc_factor_rate",
        )

        payment_frequency = st.selectbox(
            "Payment frequency",
            ["Daily", "Weekly", "Monthly"],
            index=0,
            key="mca_calc_payment_frequency",
        )

    with calc_col2:
        payment_count = st.number_input(
            "Number of payments",
            min_value=1,
            value=64,
            step=1,
            key="mca_calc_payment_count",
        )

        origination_fee_percent = st.number_input(
            "Origination fee %",
            min_value=0.0,
            max_value=25.0,
            value=0.0,
            step=0.5,
            format="%.2f",
            key="mca_calc_origination_fee_percent",
        )

        broker_commission_percent = st.number_input(
            "Broker commission %",
            min_value=0.0,
            max_value=25.0,
            value=10.0,
            step=0.5,
            format="%.2f",
            key="mca_calc_broker_commission_percent",
        )

    payback_amount = funding_amount * factor_rate
    payment_amount = payback_amount / payment_count if payment_count else 0
    origination_fee_amount = funding_amount * (origination_fee_percent / 100)
    net_to_merchant = max(funding_amount - origination_fee_amount, 0)
    broker_commission_amount = funding_amount * (broker_commission_percent / 100)
    broker_revenue = broker_commission_amount + origination_fee_amount

    st.markdown("---")
    st.subheader("Quote Snapshot")

    hero_col1, hero_col2 = st.columns(2)
    with hero_col1:
        st.metric("Net to merchant", format_money(net_to_merchant))
    with hero_col2:
        st.metric(f"{payment_count} {payment_frequency} payments", format_money(payment_amount))

    metric_col1, metric_col2 = st.columns(2)

    with metric_col1:
        st.metric("Payback amount", format_money(payback_amount))

    with metric_col2:
        st.metric("Broker revenue", format_money(broker_revenue))

    st.caption(
        f"Broker revenue includes {format_money(broker_commission_amount)} commission "
        f"and {format_money(origination_fee_amount)} origination fee."
    )

    st.markdown("---")
    st.subheader("Copy/Paste Deal Terms")

    payment_word = payment_frequency.lower()
    summary_text = (
        f"Funding amount: {format_money(funding_amount)}\n"
        f"Factor rate: {format_percent(factor_rate)}\n"
        f"Payback amount: {format_money(payback_amount)}\n"
        f"{payment_count} {payment_word} payments of {format_money(payment_amount)}\n"
        f"Net to merchant: {format_money(net_to_merchant)}\n"
    )

    summary_key = (
        f"mca_calc_summary_"
        f"{funding_amount}_{factor_rate}_{payment_frequency}_{payment_count}_"
        f"{origination_fee_percent}_{broker_commission_percent}"
    )

    st.text_area(
        "Copy/paste summary",
        value=summary_text,
        height=150,
        key=summary_key,
    )
