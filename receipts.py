# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms. Removing or altering this header is prohibited.
"""Receipt extraction core — LLM-backed parsing of receipts from email, PDF, image.

The flagship feature. Composes Gmail (find) + LLM (extract) + Sheets (log) +
Drive (archive PDFs) + optional QB CSV export.

Three extraction surfaces:
  - extract_from_text(body)         → for HTML / plain-text email bodies
  - extract_from_pdf(pdf_bytes)     → uses Claude Vision (multi-page PDFs OK)
  - extract_from_image(image_bytes) → uses Claude Vision (forwarded photos)

All return an `ExtractedReceipt` (Pydantic model). The LLM is prompted to
output strict JSON; we validate it via Pydantic so malformed responses fail
loud instead of silently corrupting the Sheet.

Cost reference (Claude Haiku 4.5, ~$1/M input + $5/M output):
  - Text body: ~$0.0005 per receipt
  - PDF (1-page, vision): ~$0.005 per receipt
  - PDF (multi-page): ~$0.01 per receipt
  - Image: ~$0.005 per receipt

Privacy posture:
  - We extract `last_4` of card if visible, NEVER the full PAN
  - When `receipts_redact_payment_details` config is True (default), even
    last_4 is dropped before persisting to Sheet/Drive
  - Receipts NEVER auto-flow to telemetry reports (recent_actions log
    redacts target_id/summary; receipt details aren't included)
"""

from __future__ import annotations

import base64 as _b64
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #

# QuickBooks Online standard Chart of Accounts as the baseline taxonomy.
# Aligns directly with QBO's default expense accounts so the QB CSV export is
# a 1:1 identity mapping (no translation layer needed). Add sub-accounts in
# QB itself if you want finer granularity (e.g. 'Travel:Airfare').
DEFAULT_CATEGORIES: list[str] = [
    "Advertising",
    "Auto Expense",
    "Bank Service Charges",
    "Computer & Equipment",
    "Contract Labor",
    "Dues & Subscriptions",
    "Insurance",
    "Legal & Professional Fees",
    "Meals",
    "Office Supplies",
    "Postage & Delivery",
    "Printing & Reproduction",
    "Rent Expense",
    "Repairs & Maintenance",
    "Software Subscriptions",
    "Taxes & Licenses",
    "Telephone Expense",
    "Travel",
    "Utilities",
    "Miscellaneous Expense",
]


# Migration map: legacy internal taxonomy → QBO Chart of Accounts. Used by
# both the runtime (in case old data shows up via the prompt or cache) and
# by the one-shot migration script. Each entry should ALWAYS resolve to a
# value present in DEFAULT_CATEGORIES.
LEGACY_CATEGORY_MAP: dict[str, str] = {
    "Travel — Airfare": "Travel",
    "Travel — Hotels": "Travel",
    "Travel — Rideshare": "Travel",
    "Travel — Car Rental": "Travel",
    "Travel — Parking & Tolls": "Travel",
    "Travel — Mileage": "Auto Expense",
    "Travel — Fuel": "Auto Expense",
    "Meals — Restaurants": "Meals",
    "Meals — Catering": "Meals",
    "Meals — Groceries (business)": "Meals",
    "Office — Software & SaaS": "Software Subscriptions",
    "Office — Hardware": "Computer & Equipment",
    "Office — Supplies": "Office Supplies",
    "Office — Subscriptions": "Software Subscriptions",
    "Communications — Phone & Internet": "Telephone Expense",
    "Communications — Mobile Data": "Telephone Expense",
    "Professional — Legal": "Legal & Professional Fees",
    "Professional — Accounting": "Legal & Professional Fees",
    "Professional — Consulting": "Legal & Professional Fees",
    "Marketing — Advertising": "Advertising",
    "Marketing — Sponsorships": "Advertising",
    "Marketing — Events": "Advertising",
    "Misc — Uncategorized": "Miscellaneous Expense",
}


def normalize_category(cat: str | None) -> str:
    """Coerce a category to its QBO equivalent.

    - If `cat` is already in DEFAULT_CATEGORIES, returns it unchanged.
    - If it matches a legacy entry, returns the QBO equivalent.
    - Otherwise returns the catch-all 'Miscellaneous Expense'.
    """
    if not cat:
        return "Miscellaneous Expense"
    if cat in DEFAULT_CATEGORIES:
        return cat
    if cat in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[cat]
    return "Miscellaneous Expense"


# Heuristic merchant → category mapping. LLM still suggests, but we use this
# as a fast deterministic fallback + post-processing override when the LLM
# guesses something obviously off.
_MERCHANT_KEYWORDS: list[tuple[list[str], str]] = [
    (["uber", "lyft", "via", "curb"], "Travel"),
    (["delta", "united", "american airlines", "southwest", "alaska air",
      "jetblue", "spirit", "frontier", "airfrance", "ba ", "british airways"],
     "Travel"),
    (["marriott", "hilton", "hyatt", "ihg", "holiday inn", "hampton",
      "courtyard", "westin", "sheraton", "ritz", "airbnb", "vrbo"],
     "Travel"),
    (["hertz", "avis", "enterprise", "budget", "national car",
      "alamo", "sixt", "turo", "zipcar"],
     "Travel"),
    (["parking", "spothero", "premiumparking", "abm parking",
      "interpark", "lanier"],
     "Travel"),
    # Major US gas chains. Auto Expense covers fuel + vehicle service.
    (["chevron", "shell", "exxon", "exxonmobil", "mobil", "bp ",
      "conoco", "phillips 66", "76 station", "valero", "sunoco",
      "arco", "marathon", "speedway", "circle k", "wawa",
      "costco gas", "sam's club gas", "racetrac", "quiktrip", "qt ",
      "love's travel", "pilot flying", "flying j",
      "gas station", "fuel"],
     "Auto Expense"),
    (["doordash", "grubhub", "ubereats", "uber eats", "caviar",
      "seamless", "postmates", "instacart"],
     "Meals"),
    (["starbucks", "blue bottle", "philz", "peet", "dunkin",
      "panera", "chipotle", "sweetgreen"],
     "Meals"),
    (["whole foods", "trader joe", "safeway", "kroger", "albertsons"],
     "Meals"),
    (["aws", "amazon web services", "google cloud", "gcp", "google workspace",
      "azure", "digitalocean", "heroku", "vercel", "netlify", "cloudflare",
      "stripe", "twilio", "sendgrid", "mailgun", "postmark"],
     "Software Subscriptions"),
    (["github", "gitlab", "bitbucket", "atlassian", "jira", "linear",
      "notion", "asana", "monday.com", "clickup", "trello"],
     "Software Subscriptions"),
    (["openai", "anthropic", "claude", "perplexity", "midjourney"],
     "Software Subscriptions"),
    (["adobe", "figma", "sketch", "framer", "canva", "miro"],
     "Software Subscriptions"),
    (["slack", "zoom", "google meet", "webex"],
     "Software Subscriptions"),
    (["apple", "microsoft store", "best buy", "newegg", "b&h photo"],
     "Computer & Equipment"),
    (["staples", "office depot", "amazon supplies"],
     "Office Supplies"),
    (["verizon", "att", "at&t", "t-mobile", "comcast", "spectrum",
      "centurylink"],
     "Telephone Expense"),
    (["facebook ads", "meta ads", "google ads", "linkedin ads",
      "twitter ads", "x ads", "tiktok ads"],
     "Advertising"),
]


def categorize_merchant(merchant: str | None) -> str:
    """Heuristic merchant → category. Returns the catch-all if no match."""
    if not merchant:
        return "Miscellaneous Expense"
    m = merchant.lower()
    for keywords, category in _MERCHANT_KEYWORDS:
        for kw in keywords:
            if kw in m:
                return category
    return "Miscellaneous Expense"


# --------------------------------------------------------------------------- #
# Pydantic model — strict shape for downstream Sheet writing
# --------------------------------------------------------------------------- #


class ReceiptLineItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    name: str = Field(default="")
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None


class ExtractedReceipt(BaseModel):
    """The structured shape we coerce LLM output into. Strict but tolerant."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # Required-ish (LLM should always produce these; set to None if it can't)
    date: Optional[str] = Field(
        default=None,
        description="Receipt date as YYYY-MM-DD. None if unparseable.",
    )
    merchant: Optional[str] = Field(
        default=None, description="Vendor name as printed on receipt.",
    )
    total: Optional[float] = Field(
        default=None,
        description="Grand total in receipt currency. None if missing.",
    )
    currency: str = Field(default="USD", description="ISO 4217 code.")

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency_none(cls, v):
        """LLM returns null on sparse receipts where currency isn't shown.
        Treat null/empty as the field default rather than a validation error."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return "USD"
        return v

    # Common fields
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    tip: Optional[float] = None
    payment_method_kind: Optional[str] = Field(
        default=None,
        description="'Visa', 'Mastercard', 'Amex', 'PayPal', 'Cash', etc. "
                    "Never a full card number.",
    )
    last_4: Optional[str] = Field(
        default=None,
        description="Last 4 digits of card if printed. Always exactly 4 digits.",
    )

    @field_validator("last_4")
    @classmethod
    def _validate_last_4(cls, v):
        """Defensive: NEVER accept more than 4 chars. If LLM emits a longer
        string, we keep only the trailing 4 digits to prevent PAN leakage.
        Empty / None passes through."""
        if v is None or v == "":
            return None
        s = str(v).strip().replace(" ", "").replace("-", "")
        # Take only digits
        digits = "".join(c for c in s if c.isdigit())
        if not digits:
            return None
        # Always coerce to last 4 only — defensive, never trust upstream
        return digits[-4:]
    location: Optional[str] = Field(
        default=None, description="City + state if printed; full address if available.",
    )
    line_items: list[ReceiptLineItem] = Field(default_factory=list)

    # Categorization (LLM suggests, post-processing may override)
    category: str = Field(default="Miscellaneous Expense")

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v):
        """LLM may return null, an empty string, or a legacy taxonomy name.
        Coerce to QBO standard via normalize_category, falling back to
        'Miscellaneous Expense' for anything we don't recognize."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return "Miscellaneous Expense"
        # Pass through QBO names; map legacy → QBO; otherwise drop to Misc.
        return normalize_category(v.strip() if isinstance(v, str) else v)

    # Provenance
    source_kind: str = Field(
        default="email_text",
        description="'email_text', 'email_pdf', 'email_image', 'drive_pdf', 'drive_image'.",
    )
    source_id: Optional[str] = Field(
        default=None,
        description="Gmail message_id or Drive file_id — for dedup + audit.",
    )
    notes: Optional[str] = Field(default=None)
    confidence: float = Field(
        default=0.5, ge=0, le=1,
        description="LLM's self-reported confidence 0-1.",
    )


# --------------------------------------------------------------------------- #
# LLM prompts
# --------------------------------------------------------------------------- #

_EXTRACT_PROMPT = """You are a precise receipt parser. Extract structured data \
from this receipt and return ONLY valid JSON matching this schema:

{{
  "date": "YYYY-MM-DD or null",
  "merchant": "vendor name or null",
  "total": number or null,
  "currency": "USD" (or other ISO 4217),
  "subtotal": number or null,
  "tax": number or null,
  "tip": number or null,
  "payment_method_kind": "Visa | Mastercard | Amex | Discover | PayPal | Cash | Other | null",
  "last_4": "exactly 4 digits or null",
  "location": "city, state or full address or null",
  "line_items": [
    {{"name": "...", "quantity": number_or_null, "unit_price": number_or_null, \
"line_total": number_or_null}}
  ],
  "category": "best match from this list: {categories}",
  "notes": "anything unusual the user should know, or null",
  "confidence": 0.0-1.0
}}

Rules:
- Output ONLY the JSON object. No prose, no markdown fences, no explanation.
- If a field is genuinely absent, use null (NOT empty strings).
- "total" should always be the GRAND total including tax + tip.
- For "last_4", only extract if visibly printed; never guess.
- Set confidence honestly: 0.9+ for clear printed receipts, 0.5-0.7 for OCR'd
  or low-quality, 0.2-0.4 for ambiguous, 0.0 if you can't extract anything useful.

Receipt content:
{content}"""


# --------------------------------------------------------------------------- #
# Extraction surfaces
# --------------------------------------------------------------------------- #


def _parse_llm_json(text: str) -> dict:
    """Strip code fences if present, parse JSON. Raises ValueError on bad input."""
    s = text.strip()
    # Sometimes the LLM ignores instructions and wraps in ```json blocks.
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
        s = s.strip()
    return json.loads(s)


def extract_from_text(
    body: str, *, source_id: Optional[str] = None,
    source_kind: str = "email_text",
    submitted_by: Optional[str] = None,
) -> ExtractedReceipt:
    """Extract receipt fields from a plain-text or HTML body. Returns
    ExtractedReceipt with confidence proportional to how well it parsed.

    Raises RuntimeError if the LLM is unavailable.
    """
    import llm as _llm
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(DEFAULT_CATEGORIES),
        content=body[:15000],  # cap to avoid runaway token costs
    )
    result = _llm.call_simple(prompt, model="claude-haiku-4-5", max_tokens=1500)
    try:
        data = _parse_llm_json(result["text"])
    except Exception as e:
        # Return a low-confidence empty record with the parse error noted.
        rec = ExtractedReceipt(
            source_kind=source_kind, source_id=source_id,
            notes=f"LLM JSON parse failed: {e}; raw[:200]={result['text'][:200]}",
            confidence=0.0,
        )
        apply_capture_metadata(rec, submitted_by=submitted_by)
        return rec
    rec = ExtractedReceipt.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    # Heuristic categorization override if LLM picked a generic catchall.
    if rec.category == "Miscellaneous Expense" and rec.merchant:
        guess = categorize_merchant(rec.merchant)
        if guess != "Miscellaneous Expense":
            rec.category = guess
    # Tier 4: sender attribution (no EXIF for text — there's no image).
    apply_capture_metadata(rec, submitted_by=submitted_by)
    return rec


def extract_from_pdf(
    pdf_bytes: bytes, *, source_id: Optional[str] = None,
    source_kind: str = "email_pdf",
    submitted_by: Optional[str] = None,
) -> ExtractedReceipt:
    """Send a PDF to Claude Vision for extraction.

    Anthropic's API supports PDFs natively (multi-page) via the document
    content block. Vision-capable models only (Haiku 4.5 supports it).
    """
    import llm as _llm
    client = _llm.get_client()
    pdf_b64 = _b64.b64encode(pdf_bytes).decode("ascii")
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(DEFAULT_CATEGORIES),
        content="(see attached PDF)",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        temperature=0.0,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()
    try:
        data = _parse_llm_json(text)
    except Exception as e:
        rec = ExtractedReceipt(
            source_kind=source_kind, source_id=source_id,
            notes=f"Vision JSON parse failed: {e}; raw[:200]={text[:200]}",
            confidence=0.0,
        )
        apply_capture_metadata(rec, submitted_by=submitted_by)
        return rec
    rec = ExtractedReceipt.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    if rec.category == "Miscellaneous Expense" and rec.merchant:
        guess = categorize_merchant(rec.merchant)
        if guess != "Miscellaneous Expense":
            rec.category = guess
    # Tier 4: sender attribution (PDFs don't carry EXIF the same way photos
    # do — sender is the only metadata we add for this path).
    apply_capture_metadata(rec, submitted_by=submitted_by)
    return rec


# Anthropic's API rejects images > 5 MB after base64-encoding. Phone photos
# routinely run 6–18 MB. Cap a bit below the hard limit to leave headroom
# for the base64 expansion (~33% larger than raw bytes).
_VISION_MAX_BYTES = 4 * 1024 * 1024  # 4 MB raw → ~5.3 MB base64


def _read_image_metadata(image_bytes: bytes) -> dict:
    """Read EXIF date + GPS coords from an image. Returns dict with keys
    `date_taken` (ISO date), `time_taken` (ISO datetime), `lat`, `lng`.
    Missing fields → None. Returns empty dict on any error.

    Used by Tier 4 enrichment: receipt date often gets misread off faded
    photos, but the camera knows when/where the photo was taken — that's
    a free fallback for low-confidence extractions.
    """
    try:
        from PIL import Image
        import io as _io
    except Exception:
        return {}

    try:
        img = Image.open(_io.BytesIO(image_bytes))
        exif = img._getexif() or {}
    except Exception:
        return {}

    if not exif:
        return {}

    out: dict = {
        "date_taken": None,
        "time_taken": None,
        "lat": None,
        "lng": None,
    }

    # DateTimeOriginal (36867) format: "YYYY:MM:DD HH:MM:SS"; fallback to DateTime (306).
    raw_dt = exif.get(36867) or exif.get(306)
    if raw_dt:
        try:
            from datetime import datetime
            dt = datetime.strptime(raw_dt, "%Y:%m:%d %H:%M:%S")
            out["date_taken"] = dt.date().isoformat()
            out["time_taken"] = dt.isoformat(timespec="seconds")
        except Exception:
            pass

    # GPSInfo (34853) sub-IFD with rationals for lat/lng.
    gps = exif.get(34853) or {}
    if gps:
        try:
            def to_decimal(val):
                d, m, s = val
                return float(d) + float(m) / 60 + float(s) / 3600
            lat_ref = gps.get(1)
            lat_val = gps.get(2)
            lng_ref = gps.get(3)
            lng_val = gps.get(4)
            if lat_val is not None and lng_val is not None:
                lat = to_decimal(lat_val)
                lng = to_decimal(lng_val)
                if lat_ref == "S":
                    lat = -lat
                if lng_ref == "W":
                    lng = -lng
                out["lat"] = round(lat, 6)
                out["lng"] = round(lng, 6)
        except Exception:
            pass

    return out


def _date_diff_months(d1: Optional[str], d2: Optional[str]) -> int:
    """Absolute month-difference between two ISO date strings. 0 on bad input."""
    if not d1 or not d2:
        return 0
    try:
        from datetime import date
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return abs((a - b).days) // 30
    except Exception:
        return 0


def apply_capture_metadata(
    rec: ExtractedReceipt,
    *,
    exif: Optional[dict] = None,
    submitted_by: Optional[str] = None,
) -> ExtractedReceipt:
    """Tier 4: layer EXIF + sender attribution onto an extracted receipt.

      - Always appends a [Metadata] block to notes when there's anything to
        report.
      - If LLM confidence is low (<0.6) OR the LLM date is off from EXIF by
        more than 12 months, prefers the EXIF date (with a 'corrected' note
        in the metadata block).
      - GPS coords go in notes; reverse-geocoding can be done as a separate
        enrichment step if location is empty (deferred for now).

    Mutates and returns the input. No-op if both inputs are empty.
    """
    parts: list[str] = []
    exif = exif or {}
    if submitted_by:
        parts.append(f"Submitted by: {submitted_by}")
    if exif.get("time_taken"):
        parts.append(f"Photo taken: {exif['time_taken']}")
    elif exif.get("date_taken"):
        parts.append(f"Photo taken: {exif['date_taken']}")
    if exif.get("lat") is not None and exif.get("lng") is not None:
        parts.append(f"GPS: {exif['lat']:.6f},{exif['lng']:.6f}")

    # Date reconciliation: only override LLM when its result is suspect.
    exif_date = exif.get("date_taken")
    if exif_date:
        suspect = (
            rec.confidence < _ENRICHMENT_THRESHOLD
            or _date_diff_months(rec.date, exif_date) > 12
        )
        if suspect and rec.date != exif_date:
            old = rec.date
            rec.date = exif_date
            parts.append(
                f"Date corrected from EXIF (LLM said {old or 'null'}, "
                f"camera says {exif_date})"
            )

    if parts:
        block = "\n[Metadata]\n  - " + "\n  - ".join(parts)
        rec.notes = (rec.notes or "") + block

    return rec


def _shrink_image_for_vision(
    image_bytes: bytes, mime_type: str,
) -> tuple[bytes, str]:
    """Iteratively downscale a JPEG/PNG until it's under the API limit.

    Returns (new_bytes, new_mime). On any failure (Pillow missing, bad
    image, etc.) returns the original bytes unchanged so the caller can
    surface a clearer error from the API itself.
    """
    if len(image_bytes) <= _VISION_MAX_BYTES:
        return image_bytes, mime_type
    try:
        from PIL import Image
        import io
    except Exception:
        return image_bytes, mime_type

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        # Convert palette / RGBA → RGB so we can re-save as JPEG.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Try progressively smaller dimensions + quality until under cap.
        max_dim = max(img.size)
        scale = 1.0
        for _ in range(8):  # at most 8 attempts
            w, h = int(img.size[0] * scale), int(img.size[1] * scale)
            if min(w, h) < 200:
                break  # don't shrink past the point of usefulness
            resized = img.resize((max(w, 1), max(h, 1)), Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=85, optimize=True)
            data = buf.getvalue()
            if len(data) <= _VISION_MAX_BYTES:
                return data, "image/jpeg"
            # Each round: shrink dim by ~30% (squared area shrinks by ~50%).
            scale *= 0.7
        # Last try: lower quality at the smallest size we attempted.
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=60, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, mime_type


def extract_from_image(
    image_bytes: bytes, *, mime_type: str = "image/jpeg",
    source_id: Optional[str] = None, source_kind: str = "email_image",
    submitted_by: Optional[str] = None,
) -> ExtractedReceipt:
    """Send a photo of a receipt to Claude Vision for extraction.

    Reads EXIF metadata (capture timestamp + GPS) BEFORE downscaling, since
    the resize path re-encodes the JPEG and can drop EXIF chunks. Layer it
    onto the receipt via apply_capture_metadata before returning.
    """
    import llm as _llm
    client = _llm.get_client()
    # Read EXIF off the raw bytes — Pillow's resize re-encodes and may strip it.
    exif_meta = _read_image_metadata(image_bytes)
    image_bytes, mime_type = _shrink_image_for_vision(image_bytes, mime_type)
    img_b64 = _b64.b64encode(image_bytes).decode("ascii")
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(DEFAULT_CATEGORIES),
        content="(see attached image)",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        temperature=0.0,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": img_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()
    try:
        data = _parse_llm_json(text)
    except Exception as e:
        return ExtractedReceipt(
            source_kind=source_kind, source_id=source_id,
            notes=f"Vision JSON parse failed: {e}; raw[:200]={text[:200]}",
            confidence=0.0,
        )
    rec = ExtractedReceipt.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    if rec.category == "Miscellaneous Expense" and rec.merchant:
        guess = categorize_merchant(rec.merchant)
        if guess != "Miscellaneous Expense":
            rec.category = guess
    # Tier 4: layer EXIF + sender attribution onto the receipt notes.
    apply_capture_metadata(rec, exif=exif_meta, submitted_by=submitted_by)
    return rec


# --------------------------------------------------------------------------- #
# Low-confidence receipt enrichment (3-tier ladder)
# --------------------------------------------------------------------------- #
# Tier 1 = LLM extract (already done above).
# Tier 2 = Google Maps Places lookup at the receipt's location.
# Tier 3 = Anthropic web_search to identify merchant type.
# Receipts staying < 0.6 after both tiers are kept but flagged for review.

_ENRICHMENT_THRESHOLD = 0.6
_MAPS_BOOST = 0.20
_WEBSEARCH_BOOST = 0.15


_NAME_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NAME_TRAILING_SUFFIX = re.compile(
    r"[,\-\s]+(inc|llc|ltd|co|corp|company|pbc|gmbh|sa|sas|sarl)\.?\s*$",
    re.IGNORECASE,
)
_NAME_WS = re.compile(r"\s+")


def content_key(
    merchant: Optional[str],
    date: Optional[str],
    total: Optional[float],
    last_4: Optional[str] = None,
) -> Optional[str]:
    """Stable content-dedup key for a receipt.

    Returns a string of the form 'merchant|date|total_cents|last_4', where
    `merchant` is normalized (lowercased, suffix-stripped via the same
    routine used by the merchant cache), `total_cents` is the integer cents,
    and `last_4` is included so two same-amount-same-day receipts on different
    cards are kept separate.

    Returns None when either merchant or total is missing — those rows can't
    be safely identified and fall back to source_id dedup only.
    """
    if not merchant or total is None:
        return None
    norm_merchant = _normalize_merchant_name(merchant)
    if not norm_merchant:
        return None
    date_str = (date or "unknown").strip()
    try:
        total_cents = int(round(float(total) * 100))
    except (TypeError, ValueError):
        return None
    last_4_str = (last_4 or "").strip()
    return f"{norm_merchant}|{date_str}|{total_cents}|{last_4_str}"


def _normalize_merchant_name(s: str) -> str:
    """Strip leading articles + trailing business suffixes + collapse whitespace."""
    s = _NAME_LEADING_ARTICLE.sub("", s.strip().casefold())
    s = _NAME_TRAILING_SUFFIX.sub("", s)
    return _NAME_WS.sub(" ", s).strip()


def _name_match(a: str, b: str) -> bool:
    """Loose merchant-name comparison for cross-checking LLM output against
    Maps results. Tolerates 'Inc'/'LLC'/'PBC' suffixes, leading 'The', and
    minor padding so 'Anthropic, PBC' matches 'Anthropic' and 'The Mobile-First
    Company' matches 'Mobile-First Company - Inc'."""
    if not a or not b:
        return False
    a_n = _normalize_merchant_name(a)
    b_n = _normalize_merchant_name(b)
    if a_n == b_n:
        return True
    # Substring fallback — only when both sides are long enough that the
    # match isn't accidental ('ab' inside 'cab' shouldn't count).
    if len(a_n) >= 4 and a_n in b_n:
        return True
    if len(b_n) >= 4 and b_n in a_n:
        return True
    return False


# Map Google Place "types" to our internal expense category. Types are
# documented at developers.google.com/maps/documentation/places/web-service/supported_types.
# IMPORTANT: every value here MUST appear in DEFAULT_CATEGORIES — there's a
# unit test that enforces this so a mistyped category never reaches the cache.
_PLACE_TYPE_TO_CATEGORY: dict[str, str] = {
    "restaurant": "Meals",
    "cafe": "Meals",
    "bar": "Meals",
    "meal_takeaway": "Meals",
    "meal_delivery": "Meals",
    "grocery_or_supermarket": "Meals",
    "supermarket": "Meals",
    "convenience_store": "Office Supplies",
    "gas_station": "Auto Expense",
    "lodging": "Travel",
    "airport": "Travel",
    "car_rental": "Travel",
    "parking": "Travel",
    "taxi_stand": "Travel",
    "transit_station": "Travel",
    "subway_station": "Travel",
    "train_station": "Travel",
    "store": "Office Supplies",
    "shopping_mall": "Office Supplies",
    "post_office": "Postage & Delivery",
    "office": "Software Subscriptions",
}


def _types_to_category(place_types: list[str]) -> Optional[str]:
    """Pick the best category from a Place's `types` list. Earlier types
    are more specific; first hit wins."""
    for t in place_types or []:
        if t in _PLACE_TYPE_TO_CATEGORY:
            return _PLACE_TYPE_TO_CATEGORY[t]
    return None


def _enrich_with_maps(rec: ExtractedReceipt) -> dict:
    """Tier 2: cross-check the LLM's extraction against Google Maps Places.

    Outcome shape:
      {applied: bool, reason: str, confidence_delta: float = 0,
       merchant_proposal: str | None, category_proposal: str | None,
       maps_name: str | None}

    Behavior matrix:
      - rec.merchant set + Maps confirms it ⇒ confidence boost
      - rec.merchant null + Maps finds a single business ⇒ propose merchant
      - rec.merchant set + Maps disagrees ⇒ no boost; note recorded
      - location missing or geocoding fails ⇒ no-op
    """
    if not rec.location:
        return {"applied": False, "reason": "no_location"}
    try:
        import gservices
        client = gservices.maps()
    except Exception as e:
        return {"applied": False, "reason": f"maps_unavailable:{e}"}

    try:
        # Prefer text-search anchored on the merchant + location. If the LLM
        # didn't get a merchant, just search the address itself.
        if rec.merchant:
            query = f"{rec.merchant} {rec.location}"
        else:
            query = rec.location
        results = client.places(query=query)
        candidates = results.get("results", []) or []
    except Exception as e:
        return {"applied": False, "reason": f"maps_error:{e}"}

    if not candidates:
        return {"applied": False, "reason": "no_match"}

    top = candidates[0]
    top_name = top.get("name", "") or ""
    place_types = top.get("types", []) or []
    category_proposal = _types_to_category(place_types)

    if rec.merchant:
        if _name_match(rec.merchant, top_name):
            return {
                "applied": True,
                "reason": f"verified={top_name}",
                "confidence_delta": _MAPS_BOOST,
                "merchant_proposal": None,
                "category_proposal": category_proposal,
                "maps_name": top_name,
            }
        return {
            "applied": False,
            "reason": f"merchant_mismatch={top_name}",
            "maps_name": top_name,
        }

    # No LLM merchant — Maps gives us a candidate.
    return {
        "applied": True,
        "reason": f"proposed={top_name}",
        "confidence_delta": _MAPS_BOOST / 2,  # weaker than verification
        "merchant_proposal": top_name,
        "category_proposal": category_proposal,
        "maps_name": top_name,
    }


def _enrich_with_web_search(rec: ExtractedReceipt) -> dict:
    """Tier 3: ask Claude to web-search what kind of business the merchant is.

    Last-resort enrichment, only fires when Maps didn't help. Returns the
    same outcome shape as `_enrich_with_maps`.
    """
    if not rec.merchant:
        return {"applied": False, "reason": "no_merchant_to_search"}

    try:
        import llm
    except Exception as e:
        return {"applied": False, "reason": f"llm_import_failed:{e}"}
    ok, _why = llm.is_available()
    if not ok:
        return {"applied": False, "reason": "llm_unavailable"}

    cat_list = "; ".join(DEFAULT_CATEGORIES)
    prompt = (
        "Identify what type of business this merchant is. Use web search if "
        "needed. Return ONLY a JSON object — no prose, no code fences.\n\n"
        f"Merchant: {rec.merchant!r}\n"
        f"Location: {rec.location or 'unknown'}\n\n"
        "JSON schema:\n"
        '{\n'
        '  "business_type": "<one or two words>",\n'
        f'  "expense_category": "<exactly one of: {cat_list}>",\n'
        '  "confidence": <0.0-1.0>,\n'
        '  "summary": "<one sentence>"\n'
        '}'
    )
    try:
        resp = llm.call_with_web_search(prompt, max_searches=2, max_tokens=512)
        data = _parse_llm_json(resp["text"])
    except Exception as e:
        return {"applied": False, "reason": f"web_search_error:{e}"}

    cat = data.get("expense_category") or ""
    # Only accept if the proposed category is one we recognize.
    if cat not in DEFAULT_CATEGORIES:
        cat = None
    return {
        "applied": True,
        "reason": (
            f"web_type={data.get('business_type', '?')}"
            f"; summary={(data.get('summary') or '')[:120]}"
        ),
        "confidence_delta": _WEBSEARCH_BOOST,
        "merchant_proposal": None,
        "category_proposal": cat,
        "search_count": resp.get("search_count"),
    }


def enrich_low_confidence_receipt(rec: ExtractedReceipt) -> ExtractedReceipt:
    """Run the 4-tier enrichment ladder on a low-confidence receipt.

      Tier 0: persistent merchant cache (free).
      Tier 2: Google Maps Places verification at receipt's location.
      Tier 3: Anthropic web_search for merchant type.

    Tier 0 is consulted FIRST — if the merchant is already known from a
    prior successful enrichment, we apply the cached data and skip the paid
    tiers entirely. Tier 2/3 successes write back to the cache so the next
    receipt from the same merchant pays nothing to enrich.

    Mutates and returns the input. High-confidence receipts (>= 0.6) are
    returned unchanged. Each tier that fires appends a one-line annotation
    to `rec.notes`. If conf stays < 0.6 after the full ladder, `[needs_review]`
    is prepended.
    """
    if rec.confidence >= _ENRICHMENT_THRESHOLD:
        return rec

    enrichment_log: list[str] = []

    # --- Tier 0: persistent merchant cache ---------------------------- #
    # Cheapest path — pure local lookup, $0. Skip if the LLM didn't get a
    # merchant name (cache key would be empty).
    if rec.merchant:
        try:
            import merchant_cache as _mc
            cached = _mc.lookup(rec.merchant)
        except Exception:
            cached = None
        if cached:
            boost = _mc.boost_for(cached)
            rec.confidence = min(0.95, rec.confidence + boost)
            if (
                cached.get("category")
                and rec.category == "Miscellaneous Expense"
            ):
                rec.category = cached["category"]
            try:
                _mc.record_hit(rec.merchant)
            except Exception:
                pass
            enrichment_log.append(
                f"Cache: matched {cached.get('display_name', rec.merchant)} "
                f"(source={cached.get('source')}, hits={cached.get('hit_count', 0) + 1})"
            )

    # --- Tier 2: Maps -------------------------------------------------- #
    maps_out = {}
    if rec.confidence < _ENRICHMENT_THRESHOLD:
        maps_out = _enrich_with_maps(rec)
        if maps_out.get("applied"):
            enrichment_log.append(f"Maps: {maps_out['reason']}")
            rec.confidence = min(
                0.95, rec.confidence + maps_out["confidence_delta"],
            )
            if maps_out.get("merchant_proposal") and not rec.merchant:
                rec.merchant = maps_out["merchant_proposal"]
            if (
                maps_out.get("category_proposal")
                and rec.category == "Miscellaneous Expense"
            ):
                rec.category = maps_out["category_proposal"]
            # Write the verification to the cache so we don't re-pay next time.
            try:
                import merchant_cache as _mc
                _mc.update(
                    rec.merchant or maps_out.get("maps_name") or "",
                    display_name=maps_out.get("maps_name"),
                    category=maps_out.get("category_proposal") or rec.category,
                    source="maps",
                    confidence=rec.confidence,
                    location=rec.location,
                )
            except Exception:
                pass  # cache write failures must never break the pipeline
        elif maps_out.get("reason") and "mismatch" in maps_out["reason"]:
            enrichment_log.append(f"Maps: {maps_out['reason']} (no boost)")

    # --- Tier 3: web search ------------------------------------------- #
    if rec.confidence < _ENRICHMENT_THRESHOLD:
        web_out = _enrich_with_web_search(rec)
        if web_out.get("applied"):
            enrichment_log.append(f"Web: {web_out['reason']}")
            rec.confidence = min(
                0.85, rec.confidence + web_out["confidence_delta"],
            )
            if (
                web_out.get("category_proposal")
                and rec.category == "Miscellaneous Expense"
            ):
                rec.category = web_out["category_proposal"]
            # Cache the web search result — this is the highest-value cache
            # write since web search is the most expensive tier ($0.015/call).
            try:
                import merchant_cache as _mc
                # Pull business_type out of the reason string.
                bt = None
                reason = web_out.get("reason", "")
                if reason.startswith("web_type="):
                    bt = reason.split("=", 1)[1].split(";", 1)[0].strip()
                _mc.update(
                    rec.merchant or "",
                    business_type=bt,
                    category=web_out.get("category_proposal") or rec.category,
                    source="web_search",
                    confidence=rec.confidence,
                    location=rec.location,
                )
            except Exception:
                pass

    # --- Annotate notes ----------------------------------------------- #
    if enrichment_log:
        block = "\n[Enrichment]\n  - " + "\n  - ".join(enrichment_log)
        rec.notes = (rec.notes or "") + block

    if rec.confidence < _ENRICHMENT_THRESHOLD:
        flag = "[needs_review] "
        rec.notes = flag + (rec.notes or "")

    return rec


# --------------------------------------------------------------------------- #
# Receipt classification (is this email a receipt?)
# --------------------------------------------------------------------------- #


# Subject keywords that suggest a receipt. Used only in conjunction with a
# money signal in the body — alone they're too noisy ("Confirm your email"
# matches "confirm" but isn't a receipt).
_RECEIPT_SUBJECT_KEYWORDS = re.compile(
    r"\b(receipt|invoice|order(?:ed)?|paid|payment|charge|"
    r"thanks for your purchase|trip receipt|fare|charged)\b",
    flags=re.IGNORECASE,
)

# Hard-NO subject patterns. These look receipt-shaped but are notifications:
# "Your X bill is ready" (announcement of a bill, not a receipt of one),
# "Your invoice is ready", reminders, account/auth notifications, etc.
# Catching these BEFORE positive matchers prevents false positives from
# overlapping subject words ("invoice" appears in both lists).
#
# The phrases use word-boundary fragments instead of full prefixes so vendor
# words in between still match: "Your Google Cloud bill is ready" → match.
_NEGATIVE_SUBJECT_PATTERNS = re.compile(
    r"\b("
    r"(bill|invoice|statement) is (now )?(ready|available)|"
    r"view your (bill|invoice|statement)|"
    r"is ready to view|"
    r"sign[- ]?in attempt|verify your (email|business|account|identity)|"
    r"confirm your email|"
    r"reset your password|password (was )?changed|"
    r"payment method (was )?(updated|added|removed|expir)|"
    r"subscription renews|will renew|will be charged|"
    r"upcoming charge|reminder|action required|"
    r"security alert|new sign[- ]?in|new device|"
    # Account setup / KYC / onboarding mail. Looks transactional but isn't.
    r"activate your (account|stripe|business|card)|"
    r"complete your (account|profile|setup|onboarding|business)|"
    r"finish (setting up|your setup)|"
    r"start accepting payments|get started"
    r")\b",
    flags=re.IGNORECASE,
)

# A money amount adjacent to payment-confirmation language. Much stricter
# than "$X.XX appears anywhere in body + word 'total' anywhere in body" —
# the old rule matched account notifications that mentioned a future total.
# This requires the keyword and the amount to be near each other.
_MONEY_AND_PAYMENT_PATTERN = re.compile(
    r"(?:"
    # Form 1: keyword-then-money. "Total: $45.00" / "Amount paid 250.00 USD"
    r"(?:charged|paid|total|amount(?:\s+(?:due|paid|charged))?|"
    r"grand total|invoice total|order total|trip total|fare|"
    r"payment received|you (?:paid|owe)|subtotal|balance|"
    r"receipt (?:for|from))"
    r"[\s:\-]{0,8}"
    r"(?:\$|£|€|¥|usd|eur|gbp|jpy)?\s*\d+[.,]\d{2}"
    r"|"
    # Form 2: money-then-keyword. Stripe-hosted receipts say "$45.00 Paid".
    # Currency symbol REQUIRED here — otherwise any "12.50 due" passes.
    r"(?:\$|£|€|¥|USD|EUR|GBP|JPY)\s*\d+[.,]\d{2}"
    r"\s+(?:paid|charged|due|received|owed)"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)

# Strong sender domains: transactional-only senders. Match ⇒ receipt.
# Discipline: only domains where 90%+ of mail to a customer is a real receipt.
# Payment processors (stripe, paypal, venmo) are NOT here — they send heavy
# operational mail (KYC, dashboard alerts, account events) alongside receipts.
_STRONG_RECEIPT_DOMAINS = {
    "uber.com", "lyft.com", "doordash.com", "grubhub.com", "ubereats.com",
    "instacart.com",
    "expensify.com", "ramp.com", "brex.com", "divvy.com",
    "marriott.com", "hilton.com", "hyatt.com", "ihg.com", "airbnb.com",
    "delta.com", "united.com", "aa.com", "southwest.com", "jetblue.com",
    "spotify.com", "netflix.com", "openai.com", "anthropic.com",
    "amazon.com",
}

# Broad sender domains: send BOTH receipts AND lots of non-billing mail
# (security alerts, social, account changes). Match ⇒ need body money signal.
# Splitting these out fixes the prior bug where 'Your bill is ready' from
# google.com was auto-classified as a receipt with no body verification.
# Payment processors live here for the same reason — Stripe sent us an
# 'Activate your account' email that slipped through the STRONG path.
_BROAD_RECEIPT_DOMAINS = {
    "apple.com", "google.com", "microsoft.com",
    "github.com", "atlassian.com", "linear.app", "notion.so",
    "stripe.com", "paypal.com", "venmo.com",
}


# Footer marker the receipt extractor stamps on its own outputs (digests,
# expense reports, recategorize confirmations). When the classifier sees this
# string in a body it KNOWS the message was generated by us — so re-scanning
# a channel that contains our own posts won't try to extract a 'receipt'
# from a receipt-summary message we sent earlier.
BOT_FOOTER_MARKER = "sent by CoAssisted Workspace receipt extractor"


def classify_email_as_receipt(
    *, subject: str, sender: str, body_preview: str = "",
) -> tuple[bool, str]:
    """Cheap heuristic — is this email likely a receipt? Returns (is_receipt, reason).

    No LLM call. Used to filter inbox down to receipt candidates before
    spending tokens. Three-tier sender model:
      - STRONG senders (uber, stripe, …) ⇒ accept
      - BROAD senders (google, apple, …) ⇒ accept only if body has a money
        amount adjacent to payment-confirmation language
      - everything else ⇒ accept only if subject AND body both signal
        a transaction
    Negative subject patterns ("Your bill is ready", "Verify your email") are
    rejected first regardless of sender — they look receipt-y but aren't.
    """
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    body_l = (body_preview or "").lower()

    # Hard-NO #1: messages WE generated. The footer marker is stamped on every
    # bot-generated expense report / digest. Re-scanning a channel that
    # contains our own posts must never try to extract from them.
    if BOT_FOOTER_MARKER.lower() in body_l:
        return False, "bot_generated_report"

    # Hard-NO #2: notification/auth/reminder subjects masquerading as receipts.
    if _NEGATIVE_SUBJECT_PATTERNS.search(subject_l):
        return False, "negative_subject_pattern"

    # Extract sender root domain (strip subdomain, e.g. receipts.uber.com → uber.com)
    sender_domain = sender_l.split("@")[-1].split(">")[0].strip()
    parts = sender_domain.split(".")
    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else sender_domain

    # Tier 1: strong senders. These domains' receipt-shaped mail IS a receipt.
    if root_domain in _STRONG_RECEIPT_DOMAINS:
        return True, f"strong_sender={root_domain}"

    has_money = bool(_MONEY_AND_PAYMENT_PATTERN.search(body_preview or ""))

    # Tier 2: broad senders. Require body money confirmation.
    if root_domain in _BROAD_RECEIPT_DOMAINS:
        if has_money:
            return True, f"broad_sender_with_money={root_domain}"
        return False, f"broad_sender_no_money_signal={root_domain}"

    # Tier 3: unknown sender. Subject keyword AND body money both required.
    if _RECEIPT_SUBJECT_KEYWORDS.search(subject_l) and has_money:
        return True, "subject_keyword_with_money"

    # Body alone, with strong money signal, is enough (covers receipts from
    # unknown senders that lack receipt-shaped subjects).
    if has_money:
        return True, "body_money_pattern"

    return False, "no_signal"


# --------------------------------------------------------------------------- #
# Sheet column mapping + QuickBooks export
# --------------------------------------------------------------------------- #

# Sheet column order — keep stable across versions for upgrade-safe append.
SHEET_COLUMNS: list[str] = [
    "logged_at",
    "date",
    "merchant",
    "total",
    "currency",
    "category",
    "subtotal",
    "tax",
    "tip",
    "payment_method_kind",
    "last_4",
    "location",
    "source_kind",
    "source_id",
    "receipt_link",
    "confidence",
    "notes",
]


def receipt_to_sheet_row(
    rec: ExtractedReceipt, *, logged_at: str, receipt_link: str = "",
    redact_payment: bool = True,
) -> list[Any]:
    """Project an ExtractedReceipt into a row matching SHEET_COLUMNS."""
    last_4 = rec.last_4 if (rec.last_4 and not redact_payment) else ""
    pm = rec.payment_method_kind or ""
    if redact_payment and pm in ("Visa", "Mastercard", "Amex", "Discover"):
        # Keep the kind but never the last_4
        pass
    return [
        logged_at,
        rec.date or "",
        rec.merchant or "",
        rec.total if rec.total is not None else "",
        rec.currency,
        rec.category,
        rec.subtotal if rec.subtotal is not None else "",
        rec.tax if rec.tax is not None else "",
        rec.tip if rec.tip is not None else "",
        pm,
        last_4,
        rec.location or "",
        rec.source_kind,
        rec.source_id or "",
        receipt_link,
        round(rec.confidence, 2),
        rec.notes or "",
    ]


# QuickBooks Online's expense import expects columns like:
#   Date, Vendor, Account, Amount, Memo
# We map our richer fields down to QB's flatter shape.
QB_CSV_COLUMNS: list[str] = [
    "Date", "Vendor", "Account", "Amount", "Currency", "Memo",
]


# Identity-mapping QB Chart of Accounts.
#
# Now that DEFAULT_CATEGORIES IS the QBO stock COA (post-refactor), the export
# is a 1:1 lookup. Legacy entries are still present so older sheet rows exported
# before the refactor still resolve to a sensible account.
_DEFAULT_QB_ACCOUNT_MAP: dict[str, str] = {
    # Identity — QBO Chart of Accounts (current).
    **{cat: cat for cat in DEFAULT_CATEGORIES},
    # Legacy → QBO (for back-compat on pre-refactor rows).
    **LEGACY_CATEGORY_MAP,
}


def receipt_to_qb_row(
    rec: ExtractedReceipt,
    *,
    account_map: Optional[dict[str, str]] = None,
) -> list[Any]:
    """Project an ExtractedReceipt into a QuickBooks-importable CSV row."""
    amap = account_map or _DEFAULT_QB_ACCOUNT_MAP
    account = amap.get(rec.category, "Miscellaneous Expense")
    memo_parts = []
    if rec.location:
        memo_parts.append(rec.location)
    if rec.notes:
        memo_parts.append(rec.notes)
    if rec.source_id:
        memo_parts.append(f"src:{rec.source_kind}:{rec.source_id}")
    return [
        rec.date or "",
        rec.merchant or "",
        account,
        rec.total if rec.total is not None else "",
        rec.currency,
        " | ".join(memo_parts),
    ]
