# ONLY showing modified sections to keep this readable
# (Everything else in your file stays EXACTLY the same)

# -------------------------
# ✅ ADD THIS under uploader
# -------------------------
uploaded_file = st.file_uploader(
    "Upload your MCA Application",
    type=["pdf"]
)

st.caption("Documents are processed in real time and never stored.")

# -------------------------
# ✅ CREDIT SCORE FIX
# Replace ONLY this function
# -------------------------
def find_value_below_label(lines, label_phrases, pattern, lookahead=3, validator=None):
    idx = find_line_index(lines, label_phrases)
    if idx is None:
        return ""

    # 🔥 NEW: check SAME LINE first (key fix)
    line = lines[idx]
    matches = re.findall(pattern, line)
    for match in matches:
        candidate = clean_value(match if isinstance(match, str) else match[0])
        if validator and not validator(candidate):
            continue
        return candidate

    # fallback (below lines)
    end = min(len(lines), idx + 1 + lookahead)
    for j in range(idx + 1, end):
        line = lines[j]
        matches = re.findall(pattern, line)
        for match in matches:
            candidate = clean_value(match if isinstance(match, str) else match[0])
            if validator and not validator(candidate):
                continue
            return candidate

    return ""

# -------------------------
# ✅ WATERMARK FIX (center + responsive)
# Replace ONLY this block inside protect_pdf
# -------------------------

        if watermark:
            text = watermark.upper()

            # 🔥 dynamic font size
            base_size = 60
            if len(text) > 25:
                base_size = 45
            if len(text) > 40:
                base_size = 35

            text_width = fitz.get_text_length(text, fontsize=base_size)

            # 🔥 scale down further if still too wide
            while text_width > (rect.width - 40) and base_size > 20:
                base_size -= 2
                text_width = fitz.get_text_length(text, fontsize=base_size)

            x = (rect.width - text_width) / 2
            y = rect.height / 2

            page.insert_text(
                (x, y),
                text,
                fontsize=base_size,
                color=(0.88, 0.88, 0.88),
                overlay=True,
            )
