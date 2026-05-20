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
# TAB 2 — DOCUMENT TRANSFER BETA
# =========================

with tab2:

    st.subheader("Transfer App Data (Beta)")
    st.caption(
        "Upload a completed MCA app and a blank lender/broker app. FundLock will extract the key values, suggest matches, let you confirm them, and generate a draft completed PDF."
    )

    # -------------------------
    # TAB 2 ONLY — helper logic
    # Kept inside Tab 2 so Tab 1 remains untouched.
    # -------------------------

    TRANSFER_FIELDS = [
        {
            "field": "Business Name",
            "key": "business_name",
            "source_labels": ["Business Name", "Legal Business Name", "Company Name", "DBA", "Merchant Name"],
            "target_keywords": ["business name", "legal business name", "company name", "dba", "merchant name"],
        },
        {
            "field": "Owner Name",
            "key": "owner_name",
            "source_labels": ["Owner Name", "Principal Name", "Applicant Name", "Contact Name", "Owner/Officer"],
            "target_keywords": ["owner name", "principal", "applicant", "contact name", "owner/officer", "authorized signer"],
        },
        {
            "field": "Business Address",
            "key": "business_address",
            "source_labels": ["Business Address", "Physical Address", "Company Address", "Merchant Address", "Street Address"],
            "target_keywords": ["business address", "physical address", "company address", "merchant address", "street address"],
        },
        {
            "field": "Phone",
            "key": "phone",
            "source_labels": ["Phone", "Business Phone", "Cell Phone", "Mobile Phone", "Contact Phone"],
            "target_keywords": ["phone", "business phone", "cell", "mobile", "contact phone"],
        },
        {
            "field": "Email",
            "key": "email",
            "source_labels": ["Email", "Business Email", "Owner Email", "Contact Email"],
            "target_keywords": ["email", "business email", "owner email", "contact email"],
        },
        {
            "field": "Credit Score",
            "key": "credit_score",
            "source_labels": ["Credit Score", "FICO", "Personal Credit Score", "Owner FICO"],
            "target_keywords": ["credit score", "fico", "personal credit", "owner fico"],
        },
        {
            "field": "Monthly Revenue",
            "key": "monthly_revenue",
            "source_labels": ["Monthly Revenue", "Monthly Sales", "Monthly Total Sales", "Gross Monthly Sales", "Average Monthly Revenue"],
            "target_keywords": ["monthly revenue", "monthly sales", "gross monthly", "average monthly", "monthly total"],
        },
        {
            "field": "Business Start Date",
            "key": "business_start_date",
            "source_labels": ["Business Start Date", "Date/Year Started", "Date Established", "In Business Since", "Start Date"],
            "target_keywords": ["business start", "date/year started", "date established", "in business since", "start date"],
        },
        {
            "field": "Years in Business",
            "key": "years_in_business",
            "source_labels": ["Years in Business", "Time in Business", "Yrs in Business"],
            "target_keywords": ["years in business", "time in business", "yrs in business"],
        },
        {
            "field": "EIN",
            "key": "ein",
            "source_labels": ["EIN", "Tax ID", "Federal Tax ID", "FEIN"],
            "target_keywords": ["ein", "tax id", "federal tax", "fein"],
        },
        {
            "field": "Requested Amount",
            "key": "requested_amount",
            "source_labels": ["Requested Amount", "Funding Amount", "Amount Requested", "Amount Needed"],
            "target_keywords": ["requested amount", "funding amount", "amount requested", "amount needed"],
        },
    ]

    def beta_clean_value(value: str) -> str:
        if not value:
            return ""
        value = value.replace("\xa0", " ")
        value = re.sub(r"\s+", " ", value)
        return value.strip(" :-|")

    def beta_open_pdf(pdf_bytes: bytes):
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp_file.write(pdf_bytes)
        tmp_file.close()
        return fitz.open(tmp_file.name), tmp_file.name

    def beta_get_page_lines_with_positions(pdf_bytes: bytes):
        doc, _ = beta_open_pdf(pdf_bytes)
        rows = []

        for page_num, page in enumerate(doc):
            words = page.get_text("words", sort=True)
            words = sorted(words, key=lambda w: (page_num, w[1], w[0]))

            current = []
            current_y = None

            for word in words:
                x0, y0, x1, y1, word_text, *_ = word

                if current_y is None:
                    current_y = y0
                    current = [(x0, y0, x1, y1, word_text)]
                    continue

                if abs(y0 - current_y) <= 3:
                    current.append((x0, y0, x1, y1, word_text))
                    current_y = (current_y + y0) / 2
                else:
                    text_line = " ".join(w[4] for w in sorted(current, key=lambda x: x[0]))
                    rect = fitz.Rect(
                        min(w[0] for w in current),
                        min(w[1] for w in current),
                        max(w[2] for w in current),
                        max(w[3] for w in current),
                    )
                    cleaned = beta_clean_value(text_line)
                    if cleaned:
                        rows.append({
                            "page": page_num,
                            "text": cleaned,
                            "rect": rect,
                        })

                    current = [(x0, y0, x1, y1, word_text)]
                    current_y = y0

            if current:
                text_line = " ".join(w[4] for w in sorted(current, key=lambda x: x[0]))
                rect = fitz.Rect(
                    min(w[0] for w in current),
                    min(w[1] for w in current),
                    max(w[2] for w in current),
                    max(w[3] for w in current),
                )
                cleaned = beta_clean_value(text_line)
                if cleaned:
                    rows.append({
                        "page": page_num,
                        "text": cleaned,
                        "rect": rect,
                    })

        doc.close()
        return rows

    def beta_find_value_near_label(lines, labels, max_lookahead=3):
        for i, row in enumerate(lines):
            row_text = row["text"]
            row_lower = row_text.lower()

            for label in labels:
                label_lower = label.lower()

                if label_lower in row_lower:
                    # Case 1: label and value are on same line.
                    after_label = row_text.lower().split(label_lower, 1)
                    if len(after_label) > 1:
                        original_after = row_text[len(row_text) - len(after_label[1]):]
                        original_after = beta_clean_value(original_after)
                        if original_after and original_after.lower() != label_lower:
                            return original_after

                    # Case 2: value appears on next few lines.
                    for j in range(i + 1, min(len(lines), i + 1 + max_lookahead)):
                        candidate = beta_clean_value(lines[j]["text"])
                        if not candidate:
                            continue
                        if len(candidate) > 80:
                            continue
                        if any(other.lower() in candidate.lower() for other in labels):
                            continue
                        return candidate

        return ""

    def beta_extract_first_money_value(text: str):
        matches = re.findall(r"\$?\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\$?\s?\d{4,9}", text)
        if not matches:
            return ""
        return format_currency(matches[0])

    def beta_extract_transfer_fields(pdf_bytes: bytes):
        text = extract_pdf_text(pdf_bytes)
        lines = beta_get_page_lines_with_positions(pdf_bytes)

        values = {}

        for field_def in TRANSFER_FIELDS:
            value = beta_find_value_near_label(lines, field_def["source_labels"])
            values[field_def["field"]] = beta_clean_value(value)

        # Stronger fallback extraction for common sensitive fields.
        emails = detect_emails(text)
        phones = detect_phones(text)

        if emails:
            values["Email"] = values.get("Email") or emails[0]

        if phones:
            values["Phone"] = values.get("Phone") or phones[0]

        # Reuse your existing conservative extraction for credit score/start date/monthly sales.
        try:
            facts = extract_deal_facts_from_pdf(pdf_bytes)

            if facts.get("credit_score"):
                values["Credit Score"] = facts["credit_score"]

            if facts.get("monthly_sales"):
                values["Monthly Revenue"] = format_currency(facts["monthly_sales"])

            if facts.get("business_start_date"):
                values["Business Start Date"] = facts["business_start_date"]

        except Exception:
            pass

        # Money fallback for requested amount if label extraction catches a messy line.
        if values.get("Requested Amount"):
            money = beta_extract_first_money_value(values["Requested Amount"])
            if money:
                values["Requested Amount"] = money

        return values

    def beta_detect_target_fields(pdf_bytes: bytes):
        lines = beta_get_page_lines_with_positions(pdf_bytes)
        candidates = []

        keyword_bank = []
        for field_def in TRANSFER_FIELDS:
            keyword_bank.extend(field_def["target_keywords"])
            keyword_bank.extend([field_def["field"].lower()])

        for row in lines:
            text_line = beta_clean_value(row["text"])
            lower_line = text_line.lower()

            if len(text_line) > 90:
                continue

            if any(keyword in lower_line for keyword in keyword_bank):
                candidates.append({
                    "label": text_line,
                    "page": row["page"],
                    "rect": row["rect"],
                })

        # Deduplicate labels while preserving first location.
        seen = set()
        unique = []

        for item in candidates:
            key = item["label"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

        return unique

    def beta_best_target_match(field_def, target_fields):
        best_label = ""
        best_score = 0

        for target in target_fields:
            target_label = target["label"]
            target_lower = target_label.lower()
            score = 0

            for keyword in field_def["target_keywords"]:
                if keyword in target_lower:
                    score += 3

            for token in field_def["field"].lower().split():
                if token in target_lower:
                    score += 1

            if score > best_score:
                best_score = score
                best_label = target_label

        if best_score >= 4:
            confidence = "High"
        elif best_score >= 2:
            confidence = "Review"
        else:
            confidence = "Missing"

        return best_label, confidence

    def beta_build_mapping_rows(extracted_values, target_fields):
        rows = []

        for field_def in TRANSFER_FIELDS:
            field_name = field_def["field"]
            source_value = extracted_values.get(field_name, "")
            target_label, confidence = beta_best_target_match(field_def, target_fields)

            if not source_value:
                confidence = "Missing"

            rows.append({
                "Use": bool(source_value and target_label),
                "Field": field_name,
                "Source Value": source_value,
                "Destination Match": target_label,
                "Confidence": confidence,
            })

        return rows

    def beta_confidence_score(rows):
        if not rows:
            return 0

        usable = [row for row in rows if row.get("Source Value")]
        if not usable:
            return 0

        high = sum(1 for row in usable if row.get("Confidence") == "High")
        review = sum(1 for row in usable if row.get("Confidence") == "Review")

        score = int(((high * 1.0) + (review * 0.55)) / len(usable) * 100)
        return max(0, min(score, 100))

    def beta_find_target_meta(target_fields, label):
        for target in target_fields:
            if target["label"] == label:
                return target
        return None

    def beta_insert_value_near_label(page, label_rect, value):
        # Simple beta placement:
        # first try right of label, then below label if too close to page edge.
        value = str(value or "").strip()
        if not value:
            return

        fontsize = 9
        x = label_rect.x1 + 12
        y = label_rect.y1 - 2

        if x > page.rect.width - 180:
            x = label_rect.x0
            y = label_rect.y1 + 16

        max_width = page.rect.width - x - 24
        if max_width < 120:
            max_width = 120

        page.insert_textbox(
            fitz.Rect(x, y - 12, min(page.rect.width - 18, x + max_width), y + 18),
            value,
            fontsize=fontsize,
            color=(0, 0, 0),
            overlay=True,
        )

    def beta_generate_completed_pdf(target_pdf_bytes, confirmed_rows, target_fields):
        doc, temp_path = beta_open_pdf(target_pdf_bytes)

        for row in confirmed_rows:
            if not row.get("Use"):
                continue

            source_value = row.get("Source Value", "")
            destination_label = row.get("Destination Match", "")

            if not source_value or not destination_label:
                continue

            target_meta = beta_find_target_meta(target_fields, destination_label)
            if not target_meta:
                continue

            page = doc[target_meta["page"]]
            beta_insert_value_near_label(page, target_meta["rect"], source_value)

        output_path = temp_path.replace(".pdf", "_fundlock_completed_beta.pdf")
        doc.save(output_path)
        doc.close()
        return output_path

    # -------------------------
    # Broker-friendly UI
    # -------------------------

    st.markdown("### 1. Upload documents")

    col_upload_1, col_upload_2 = st.columns(2)

    with col_upload_1:
        source_app = st.file_uploader(
            "Completed app",
            type=["pdf"],
            key="source_app_beta_v2",
            help="Upload the app that already has the merchant info."
        )

    with col_upload_2:
        target_template = st.file_uploader(
            "Blank app template",
            type=["pdf"],
            key="target_template_beta_v2",
            help="Upload the blank lender/broker app you want to transfer the info into."
        )

    template_name = st.text_input(
        "Template nickname",
        placeholder="e.g., Everest Funding App, Lender A, My Broker App",
        key="template_name_beta_v2"
    )

    if source_app:
        st.success("Completed app uploaded.")

    if target_template:
        st.success("Blank app uploaded.")

    if not source_app or not target_template:
        st.info("Upload both PDFs to start the transfer review.")
        st.stop()

    source_pdf_bytes = source_app.getvalue()
    target_pdf_bytes = target_template.getvalue()

    with st.spinner("Extracting fields and finding matching spots on the blank app..."):
        try:
            extracted_values = beta_extract_transfer_fields(source_pdf_bytes)
            target_fields = beta_detect_target_fields(target_pdf_bytes)
            mapping_rows = beta_build_mapping_rows(extracted_values, target_fields)
        except Exception as e:
            st.error(f"Could not process the PDFs: {e}")
            st.stop()

    score = beta_confidence_score(mapping_rows)
    high_count = sum(1 for row in mapping_rows if row["Confidence"] == "High" and row["Source Value"])
    review_count = sum(1 for row in mapping_rows if row["Confidence"] == "Review" and row["Source Value"])
    missing_count = sum(1 for row in mapping_rows if row["Confidence"] == "Missing" or not row["Source Value"])

    st.markdown("---")
    st.markdown("### 2. Review transfer summary")

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Confidence", f"{score}%")
    metric_2.metric("High", high_count)
    metric_3.metric("Review", review_count)
    metric_4.metric("Missing", missing_count)

    if score >= 85:
        st.success("Most fields look ready. Review anything yellow before generating the completed PDF.")
    elif score >= 60:
        st.warning("Some fields need review. Confirm the destination matches before generating the completed PDF.")
    else:
        st.error("This template needs manual review. FundLock found limited confident matches.")

    st.markdown("### 3. Confirm field matches")
    st.caption("Edit values if needed. Choose the destination field on the blank app. Uncheck anything you do not want transferred.")

    target_options = [""] + [target["label"] for target in target_fields]

    confirmed_rows = []
    fields_needing_review = [
        row for row in mapping_rows
        if row["Confidence"] != "High" or not row["Source Value"] or not row["Destination Match"]
    ]

    if fields_needing_review:
        st.warning(f"{len(fields_needing_review)} fields need review before this is dummy-proof.")

    with st.expander("Review suggested matches", expanded=True):
        for idx, row in enumerate(mapping_rows):
            field_name = row["Field"]
            confidence = row["Confidence"]

            if confidence == "High":
                badge = "✅ High"
            elif confidence == "Review":
                badge = "⚠️ Review"
            else:
                badge = "❌ Missing"

            st.markdown(f"**{field_name}** — {badge}")

            col_use, col_value, col_dest = st.columns([1, 3, 3])

            with col_use:
                use_field = st.checkbox(
                    "Use",
                    value=bool(row["Use"]),
                    key=f"beta_use_{idx}_{field_name}"
                )

            with col_value:
                source_value = st.text_input(
                    "Source value",
                    value=row["Source Value"],
                    key=f"beta_value_{idx}_{field_name}"
                )

            with col_dest:
                default_index = 0
                if row["Destination Match"] in target_options:
                    default_index = target_options.index(row["Destination Match"])

                destination_match = st.selectbox(
                    "Destination on blank app",
                    options=target_options,
                    index=default_index,
                    key=f"beta_dest_{idx}_{field_name}"
                )

            confirmed_rows.append({
                "Use": use_field,
                "Field": field_name,
                "Source Value": source_value,
                "Destination Match": destination_match,
                "Confidence": confidence,
            })

            st.markdown("---")

    with st.expander("Detected destination labels on blank app"):
        if target_fields:
            for target in target_fields:
                st.write(f"- Page {target['page'] + 1}: {target['label']}")
        else:
            st.write("No obvious labels detected. You may need a cleaner blank PDF template.")

    st.markdown("### 4. Generate draft completed app")

    st.caption(
        "Beta note: this writes values near the detected labels on the blank PDF. Some templates may need manual adjustment if their layout is unusual or scanned."
    )

    ready_count = sum(
        1 for row in confirmed_rows
        if row["Use"] and row["Source Value"] and row["Destination Match"]
    )

    st.write(f"Ready to transfer **{ready_count}** fields.")

    if st.button("Generate Completed App PDF", key="generate_completed_transfer_beta"):
        try:
            output_path = beta_generate_completed_pdf(
                target_pdf_bytes,
                confirmed_rows,
                target_fields
            )

            download_base = template_name.strip() or "completed_app"
            download_name = re.sub(r"[^A-Za-z0-9_-]+", "_", download_base).strip("_")
            if not download_name:
                download_name = "completed_app"

            st.success("Draft completed app generated.")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download Completed App PDF",
                    data=f,
                    file_name=f"{download_name}_fundlock_completed_beta.pdf",
                    mime="application/pdf",
                )

        except Exception as e:
            st.error(f"Could not generate completed PDF: {e}")

    st.caption(
        "Recommended beta workflow: review yellow/red fields only, generate the draft, then visually check the completed PDF before sending anywhere."
    )
