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

    st.subheader("Document Transfer (Beta)")
    st.caption(
        "Upload a completed application and a blank template to prepare field transfer."
    )

    st.info(
        "Beta workflow: extract fields, confirm values, then eventually populate a saved broker or lender template."
    )

    source_app = st.file_uploader(
        "Upload completed application",
        type=["pdf"],
        key="source_app_beta"
    )

    target_template = st.file_uploader(
        "Upload blank broker / lender template",
        type=["pdf"],
        key="target_template_beta"
    )

    template_name = st.text_input(
        "Template name",
        placeholder="e.g., Everest Funding App, My Broker App, Lender A",
        key="template_name_beta"
    )

    if source_app:
        st.success("Completed application uploaded.")

    if target_template:
        st.success("Blank template uploaded.")

    if source_app and target_template:

        st.markdown("---")
        st.subheader("Field Confirmation")

        st.caption(
            "Review or manually enter values below. In the next phase, FundLock can use these confirmed values to populate the selected template."
        )

        source_pdf_bytes = source_app.getvalue()

        try:
            beta_facts = extract_deal_facts_from_pdf(source_pdf_bytes)
        except Exception:
            beta_facts = {
                "business_start_date": "",
                "credit_score": "",
                "monthly_sales": "",
            }

        try:
            source_text = extract_pdf_text(source_pdf_bytes)
            detected_emails_beta = detect_emails(source_text)
            detected_phones_beta = detect_phones(source_text)
        except Exception:
            detected_emails_beta = []
            detected_phones_beta = []

        confirmed_business_name = st.text_input(
            "Business Name",
            value="",
            key="transfer_business_name"
        )

        confirmed_owner_name = st.text_input(
            "Owner Name",
            value="",
            key="transfer_owner_name"
        )

        confirmed_business_address = st.text_input(
            "Business Address",
            value="",
            key="transfer_business_address"
        )

        confirmed_phone = st.text_input(
            "Phone",
            value=", ".join(detected_phones_beta),
            key="transfer_phone"
        )

        confirmed_email = st.text_input(
            "Email",
            value=", ".join(detected_emails_beta),
            key="transfer_email"
        )

        confirmed_credit_score = st.text_input(
            "Credit Score",
            value=beta_facts.get("credit_score", ""),
            key="transfer_credit_score"
        )

        confirmed_monthly_revenue = st.text_input(
            "Monthly Revenue",
            value=format_currency(beta_facts.get("monthly_sales", "")) if beta_facts.get("monthly_sales") else "",
            key="transfer_monthly_revenue"
        )

        confirmed_business_start_date = st.text_input(
            "Business Start Date",
            value=beta_facts.get("business_start_date", ""),
            key="transfer_business_start_date"
        )

        confirmed_years_in_business = st.text_input(
            "Years in Business",
            value="",
            key="transfer_years_in_business"
        )

        confirmed_ein = st.text_input(
            "EIN",
            value="",
            key="transfer_ein"
        )

        st.markdown("---")

        save_template = st.checkbox(
            "Save this as a reusable template later",
            key="save_template_beta"
        )

        if st.button("Confirm Fields", key="confirm_fields_beta"):

            confirmed_fields = {
                "Business Name": confirmed_business_name,
                "Owner Name": confirmed_owner_name,
                "Business Address": confirmed_business_address,
                "Phone": confirmed_phone,
                "Email": confirmed_email,
                "Credit Score": confirmed_credit_score,
                "Monthly Revenue": confirmed_monthly_revenue,
                "Business Start Date": confirmed_business_start_date,
                "Years in Business": confirmed_years_in_business,
                "EIN": confirmed_ein,
            }

            st.success("Fields confirmed for beta transfer workflow.")

            with st.expander("View confirmed fields"):
                for field_name, field_value in confirmed_fields.items():
                    st.write(f"**{field_name}:** {field_value or '—'}")

            if save_template:
                st.info(
                    f"Template '{template_name or 'Unnamed Template'}' marked for future reusable template support."
                )

        st.caption(
            "Next phase: map these confirmed fields to exact locations on the blank template and generate a completed PDF."
        )
