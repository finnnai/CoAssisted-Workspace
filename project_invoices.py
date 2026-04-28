# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Project invoice extraction — LLM-backed parsing of vendor invoices.

Extends the receipt extractor for B2B/project work where the document is a
BILL (not yet paid) tied to a project, vs. a RECEIPT (already paid). Different
data shape, different downstream destination.

Receipt vs. Invoice distinguishing fields:
    Receipt:  paid, last_4 visible, transaction_date, total only
    Invoice:  unpaid, invoice_number, due_date, payment_terms, bill-to address,
              remit-to address, optional PO number, billable + markup math

Three extraction surfaces — same shape as receipts.py:
    extract_invoice_from_text(body)
    extract_invoice_from_pdf(pdf_bytes)
    extract_invoice_from_image(image_bytes)

Plus a doc classifier so the orchestrator can decide receipt-vs-invoice on
the fly:
    classify_document(text)        → ('receipt' | 'invoice', confidence, reason)

All extractors return ExtractedInvoice (Pydantic). Project_code is RESOLVED
in the tool layer via project_registry.resolve(...) — this module stays
project-agnostic so it's easy to test in isolation.

Cost reference (Claude Haiku 4.5):
    Text body: ~$0.0008 per invoice (slightly more output than receipts)
    PDF (1-page): ~$0.006 per invoice
    Image: ~$0.006 per invoice
"""

from __future__ import annotations

import base64 as _b64
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Reuse the QBO chart-of-accounts taxonomy from receipts so categories stay
# consistent across the two surfaces. Project invoices can also be categorized
# (e.g. Subcontractor labor → 'Contract Labor').
import receipts as _r


# --------------------------------------------------------------------------- #
# Status + payment terms
# --------------------------------------------------------------------------- #

# Lifecycle of an invoice in our sheet. Sheet rows can be updated to reflect
# new status. AWAITING_INFO means the orchestrator's quality guard fired,
# we sent a follow-up to the vendor on the original channel, and we're
# waiting for them to fill in the missing fields (invoice_number, total,
# due_date, etc.). vendor_followups.py tracks the outstanding ask.
INVOICE_STATUSES = [
    "OPEN", "AWAITING_INFO", "APPROVED", "PAID", "DISPUTED", "VOID",
]

# Common payment-terms strings the LLM might emit. Normalized to a canonical
# form so downstream sheet sorting / aging math is deterministic.
_TERMS_NORMALIZE: dict[str, str] = {
    "due on receipt":  "Due on receipt",
    "due upon receipt":"Due on receipt",
    "net 7":           "Net 7",
    "net 10":          "Net 10",
    "net 14":          "Net 14",
    "net 15":          "Net 15",
    "net 30":          "Net 30",
    "net 45":          "Net 45",
    "net 60":          "Net 60",
    "net 90":          "Net 90",
}

_TERMS_DAYS_MAP: dict[str, int] = {
    "Due on receipt": 0,
    "Net 7": 7,
    "Net 10": 10,
    "Net 14": 14,
    "Net 15": 15,
    "Net 30": 30,
    "Net 45": 45,
    "Net 60": 60,
    "Net 90": 90,
}


def normalize_terms(s: Optional[str]) -> Optional[str]:
    """Coerce a raw terms string to a canonical form. None passes through."""
    if not s:
        return None
    cleaned = s.strip().lower()
    return _TERMS_NORMALIZE.get(cleaned, s.strip())


def terms_to_days(terms: Optional[str]) -> Optional[int]:
    """Map a canonical terms string to its day count. Returns None if unknown."""
    if not terms:
        return None
    return _TERMS_DAYS_MAP.get(terms.strip())


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    description: str = Field(default="")
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    # Optional sub-project allocation. Most invoices map 1:1 to a project,
    # but a vendor sometimes itemizes work across two phases.
    sub_project: Optional[str] = None


class ExtractedInvoice(BaseModel):
    """Strict shape for the invoice path. Mirror of ExtractedReceipt's
    discipline (extra='ignore' so noisy LLM keys don't crash validation)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # Required-ish (LLM should always produce these; null if it can't)
    vendor: Optional[str] = Field(
        default=None, description="Issuing vendor / contractor name.",
    )
    invoice_number: Optional[str] = Field(
        default=None,
        description="The invoice's number as printed (alphanumeric).",
    )
    invoice_date: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD when the invoice was issued.",
    )
    due_date: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD when payment is due. Null if unstated.",
    )
    payment_terms: Optional[str] = Field(
        default=None, description="'Net 30', 'Due on receipt', etc.",
    )
    po_number: Optional[str] = Field(
        default=None, description="Customer PO number, if referenced.",
    )

    # Money
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = Field(
        default=None,
        description="Grand total INCLUDING tax. Use null if missing.",
    )
    currency: str = Field(default="USD")

    # Bill-to / remit-to (free-form addresses; we only need these for
    # vendor master and audit trails).
    bill_to: Optional[str] = None
    remit_to: Optional[str] = None

    # Project allocation — populated post-extraction by the tool layer.
    project_code: Optional[str] = Field(
        default=None,
        description="Project this invoice is allocated to. Null until resolved.",
    )

    # Billable / markup. These are caller-supplied defaults (from the project
    # registry), or LLM-suggested if the invoice text indicates billable work.
    billable: bool = Field(default=True)
    markup_pct: float = Field(
        default=0.0, ge=0,
        description="Markup percentage applied when re-billing client. 0 = pass-through.",
    )

    # Status defaults to OPEN — invoice has been received but not approved/paid.
    status: str = Field(default="OPEN")

    # Lines + categorization
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    category: str = Field(default="Miscellaneous Expense")

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return "USD"
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return "Miscellaneous Expense"
        return _r.normalize_category(v.strip() if isinstance(v, str) else v)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        if not v:
            return "OPEN"
        s = str(v).strip().upper()
        return s if s in INVOICE_STATUSES else "OPEN"

    @field_validator("payment_terms", mode="before")
    @classmethod
    def _normalize_terms(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return normalize_terms(str(v))

    # Provenance
    source_kind: str = Field(default="email_text")
    source_id: Optional[str] = None
    notes: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)

    # Computed convenience — populated by .compute_invoiceable_amount().
    invoiceable_amount: Optional[float] = Field(default=None)

    def compute_invoiceable_amount(self) -> Optional[float]:
        """Apply markup to the total to get the client-billable figure.

        Returns None if billable=False or total is missing. Mutates the
        record in place AND returns the value for convenience.
        """
        if not self.billable or self.total is None:
            self.invoiceable_amount = None
            return None
        amt = float(self.total) * (1.0 + (self.markup_pct or 0.0) / 100.0)
        self.invoiceable_amount = round(amt, 2)
        return self.invoiceable_amount


# --------------------------------------------------------------------------- #
# Document classifier — receipt vs. invoice
# --------------------------------------------------------------------------- #

# Two tiers of invoice signals — split after the Unum benefits-statement
# false positive (Apr 2026). A real invoice emits a printable invoice or PO
# *number* somewhere in the body; benefits statements and account summaries
# only use the WEAK supporting language ("amount due", "due date") without
# ever printing a number. Requiring at least one STRONG hit kills that
# false-positive class.
#
# STRONG: keyword + an identifier-shaped token after it ([A-Z0-9-]{3,}).
# WEAK: supporting language alone — not enough on its own.
_STRONG_INVOICE_TOKENS = [
    # "Invoice #INV-2026-0042" / "Invoice no: 12345" / "Invoice number ABC123"
    r"\binvoice\s*(?:no\.?|#|number)\s*[:\-#]?\s*[A-Z0-9][A-Z0-9\-_/]{2,}",
    # "Invoice: ABC-123" (no leading "no/#/number")
    r"\binvoice\s*[#:]\s*[A-Z0-9][A-Z0-9\-_/]{2,}",
    # "Bill # 12345" / "Bill no: ABC-123"
    r"\bbill\s*(?:no\.?|#|number)\s*[:\-#]?\s*[A-Z0-9][A-Z0-9\-_/]{2,}",
    # "PO # 12345" / "P.O. number: ABC-123" / "Purchase order: PO-99"
    r"\bp\.?o\.?\s*(?:number|#|no\.?)\s*[:\-#]?\s*[A-Z0-9][A-Z0-9\-_/]{2,}",
    r"\bpurchase\s+order\s*(?:number|#|no\.?)?\s*[:\-#]?\s*[A-Z0-9][A-Z0-9\-_/]{2,}",
]

_WEAK_INVOICE_TOKENS = [
    r"\bdue\s+(date|by|on)\b",
    r"\bnet\s*\d{1,3}\b",
    r"\bdue\s+on\s+receipt\b",
    r"\bremit\s+to\b",
    r"\bbill\s+to\b",
    r"\bbalance\s+due\b",
    r"\bplease\s+(pay|remit)\b",
    r"\bamount\s+due\b",
]

# Strong receipt signals — already paid.
_RECEIPT_TOKENS = [
    r"\bpaid\b",
    r"\bpayment\s+received\b",
    r"\bauthorization\s+code\b",
    r"\bauth\s*#\b",
    r"\btransaction\s+id\b",
    r"\bcard\s+ending\s+in\b",
    r"\b(visa|mastercard|amex|discover)\s*\*+\s*\d{4}\b",
    r"\bthank\s+you\s+for\s+your\s+(purchase|order|payment)\b",
    r"\bsubtotal\b.{0,40}\btotal\b",  # printed register-style breakdown
]

_STRONG_INVOICE_RE = [re.compile(p, re.IGNORECASE) for p in _STRONG_INVOICE_TOKENS]
_WEAK_INVOICE_RE = [re.compile(p, re.IGNORECASE) for p in _WEAK_INVOICE_TOKENS]
_RECEIPT_RE = [re.compile(p, re.IGNORECASE) for p in _RECEIPT_TOKENS]

# Backwards-compat — older callers may import this list.
_INVOICE_RE = _STRONG_INVOICE_RE + _WEAK_INVOICE_RE


def classify_document(text: str) -> tuple[str, float, str]:
    """Decide whether a body is a receipt, an invoice, or neither. Cheap
    heuristic BEFORE any LLM call. Returns (kind, confidence, reason).

    Tightened rule (Apr 2026): a body must produce ≥1 STRONG invoice signal
    (an invoice/PO number with an actual identifier-shaped token, not just
    'amount due' or 'net 30' filler) before we'll classify it as an invoice.
    Bodies that only have weak invoice language fall back to either receipt
    (when receipt signals dominate) or 'not an invoice' default.

    Loop-safety: any message stamped with BOT_FOOTER_MARKER is auto-rejected
    here just like in the receipt classifier — otherwise MCP's own outbound
    info-requests get re-extracted as 'new invoices' on the next scan.
    """
    if not text:
        return "invoice", 0.5, "empty_input_default_invoice"

    # Hard-NO: messages we generated ourselves. Same marker as receipts.py.
    if _r.BOT_FOOTER_MARKER.lower() in text.lower():
        return "receipt", 0.0, "bot_generated_outbound"

    strong_inv = sum(1 for r in _STRONG_INVOICE_RE if r.search(text))
    weak_inv = sum(1 for r in _WEAK_INVOICE_RE if r.search(text))
    rec_hits = sum(1 for r in _RECEIPT_RE if r.search(text))

    # Strong receipt signal beats weak-only invoice language.
    if rec_hits >= 2 and strong_inv == 0:
        return "receipt", min(0.95, 0.6 + 0.1 * rec_hits), (
            f"receipt_signals={rec_hits}, strong_invoice=0"
        )

    # Top-tier invoice classification: at least one printed invoice/PO number.
    if strong_inv >= 2:
        return "invoice", min(0.95, 0.7 + 0.1 * strong_inv), (
            f"strong_invoice_signals={strong_inv}, weak={weak_inv}"
        )
    if strong_inv >= 1 and weak_inv >= 1:
        return "invoice", 0.85, (
            f"strong_invoice=1, weak_invoice={weak_inv}"
        )
    if strong_inv >= 1:
        return "invoice", 0.7, "single_strong_invoice_signal"

    # No strong invoice signal — leans away from invoice.
    if rec_hits >= 1 and rec_hits > weak_inv:
        return "receipt", 0.65, (
            f"receipt_signals={rec_hits}, weak_invoice={weak_inv}, strong_invoice=0"
        )

    # Weak-only invoice language without a number is the Unum case —
    # return 'invoice' with a low score so the caller's classify_threshold
    # rejects it. Keeps the API shape ('invoice'|'receipt') stable.
    if weak_inv >= 1:
        return "invoice", 0.4, (
            f"weak_invoice_only={weak_inv}, no_number_printed"
        )

    if rec_hits == 0:
        return "invoice", 0.3, "no_signal"

    return "receipt", 0.6, f"receipt_signals={rec_hits}"


# --------------------------------------------------------------------------- #
# LLM extraction prompt
# --------------------------------------------------------------------------- #

_EXTRACT_PROMPT = """You are a precise vendor-invoice parser. Extract \
structured data from this invoice and return ONLY valid JSON matching this \
schema:

{{
  "vendor": "issuing vendor name or null",
  "invoice_number": "as printed (alphanumeric) or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "payment_terms": "Net 30 | Net 15 | Due on receipt | etc | null",
  "po_number": "customer PO if referenced or null",
  "subtotal": number or null,
  "tax": number or null,
  "total": number or null,
  "currency": "USD" (or other ISO 4217),
  "bill_to": "first line of bill-to address or null",
  "remit_to": "first line of remit-to address or null",
  "line_items": [
    {{"description": "...", "quantity": number_or_null, \
"unit_price": number_or_null, "line_total": number_or_null, \
"sub_project": "phase/sub-project name if itemized, or null"}}
  ],
  "category": "best match from this list: {categories}",
  "notes": "anything unusual the user should know, or null",
  "confidence": 0.0-1.0
}}

Rules:
- Output ONLY the JSON object. No prose, no markdown fences, no explanation.
- "total" is the GRAND total INCLUDING tax. Use null if not stated.
- Use null (NOT empty strings) for missing fields.
- Be exact on invoice_number — vendors use alphanumerics like 'INV-2026-0042'.
- For payment_terms, prefer the canonical strings ('Net 30', 'Due on receipt').
- Set confidence honestly: 0.9+ for clear printed invoices, 0.5-0.7 for OCR/
  low-quality, 0.2-0.4 for ambiguous, 0.0 if you can't extract anything.

Invoice content:
{content}"""


def _parse_llm_json(text: str) -> dict:
    """Strip code fences, parse JSON. Mirrors receipts._parse_llm_json."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
        s = s.strip()
    return json.loads(s)


# --------------------------------------------------------------------------- #
# Extraction surfaces
# --------------------------------------------------------------------------- #


def extract_invoice_from_text(
    body: str,
    *,
    source_id: Optional[str] = None,
    source_kind: str = "email_text",
) -> ExtractedInvoice:
    """Extract invoice fields from a plain-text or HTML body."""
    import llm as _llm
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(_r.DEFAULT_CATEGORIES),
        content=body[:15000],
    )
    result = _llm.call_simple(prompt, model="claude-haiku-4-5", max_tokens=2000)
    try:
        data = _parse_llm_json(result["text"])
    except Exception as e:
        return ExtractedInvoice(
            source_kind=source_kind, source_id=source_id,
            notes=f"LLM JSON parse failed: {e}; raw[:200]={result['text'][:200]}",
            confidence=0.0,
        )
    rec = ExtractedInvoice.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    return rec


def extract_invoice_from_pdf(
    pdf_bytes: bytes,
    *,
    source_id: Optional[str] = None,
    source_kind: str = "email_pdf",
) -> ExtractedInvoice:
    """Send an invoice PDF to Claude Vision."""
    import llm as _llm
    client = _llm.get_client()
    pdf_b64 = _b64.b64encode(pdf_bytes).decode("ascii")
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(_r.DEFAULT_CATEGORIES),
        content="(see attached PDF)",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
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
        return ExtractedInvoice(
            source_kind=source_kind, source_id=source_id,
            notes=f"Vision JSON parse failed: {e}; raw[:200]={text[:200]}",
            confidence=0.0,
        )
    rec = ExtractedInvoice.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    return rec


def extract_invoice_from_image(
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    source_id: Optional[str] = None,
    source_kind: str = "email_image",
) -> ExtractedInvoice:
    """Send a photo of an invoice to Claude Vision. Reuses the receipts
    image-shrinker so phone photos > 5 MB still go through."""
    import llm as _llm
    client = _llm.get_client()
    image_bytes, mime_type = _r._shrink_image_for_vision(image_bytes, mime_type)
    img_b64 = _b64.b64encode(image_bytes).decode("ascii")
    prompt = _EXTRACT_PROMPT.format(
        categories="; ".join(_r.DEFAULT_CATEGORIES),
        content="(see attached image)",
    )
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
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
        return ExtractedInvoice(
            source_kind=source_kind, source_id=source_id,
            notes=f"Vision JSON parse failed: {e}; raw[:200]={text[:200]}",
            confidence=0.0,
        )
    rec = ExtractedInvoice.model_validate(data)
    rec.source_kind = source_kind
    rec.source_id = source_id
    return rec


# --------------------------------------------------------------------------- #
# Content-key dedup (vendor + invoice_number is the natural key)
# --------------------------------------------------------------------------- #


def invoice_content_key(
    vendor: Optional[str],
    invoice_number: Optional[str],
    total: Optional[float],
) -> Optional[str]:
    """Stable content-dedup key for an invoice.

    Form: 'normalized_vendor|invoice_number|total_cents'

    Vendor + invoice_number is the natural unique key in any AP system —
    a vendor never reuses an invoice number for the same customer. Falls
    back to None when vendor or invoice_number is missing (the row can't
    be safely identified and the caller should fall back to source_id dedup).
    """
    if not vendor or not invoice_number:
        return None
    norm_vendor = _r._normalize_merchant_name(vendor)
    if not norm_vendor:
        return None
    inv_clean = invoice_number.strip().lower()
    try:
        total_cents = int(round(float(total) * 100)) if total is not None else 0
    except (TypeError, ValueError):
        total_cents = 0
    return f"{norm_vendor}|{inv_clean}|{total_cents}"


# --------------------------------------------------------------------------- #
# Sheet schema + QB mapping
# --------------------------------------------------------------------------- #

# Per-project AP+expense sheet column order — keep stable across versions.
# This sheet now carries BOTH invoices (unpaid bills) and receipts (paid
# expenses) so a project's full spend lives in one place. The `doc_type`
# column distinguishes them; receipt-specific fields stay blank for invoices
# and vice versa.
PROJECT_SHEET_COLUMNS: list[str] = [
    "logged_at",
    "doc_type",          # 'invoice' | 'receipt'
    "invoice_date",      # invoice date OR receipt transaction date
    "due_date",          # blank for receipts
    "vendor",            # invoice vendor OR receipt merchant
    "invoice_number",    # blank for receipts (use last_4 in notes if you need a tie-out)
    "po_number",         # blank for receipts
    "category",
    "subtotal",
    "tax",
    "total",
    "currency",
    "billable",
    "markup_pct",
    "invoiceable_amount",
    "status",            # OPEN/APPROVED/PAID/DISPUTED/VOID for invoices; PAID for receipts
    "days_outstanding",  # 0 for receipts (already paid)
    "payment_terms",     # blank for receipts
    "project_code",
    "bill_to",           # blank for receipts
    "remit_to",          # blank for receipts
    "source_kind",
    "source_id",
    "invoice_link",      # PDF/image link for both kinds
    "confidence",
    "notes",
    "content_key",
]

# Backwards-compat alias — the previous build called this constant
# INVOICE_SHEET_COLUMNS. Kept so existing import sites and old tests don't
# break. Prefer PROJECT_SHEET_COLUMNS for new code.
INVOICE_SHEET_COLUMNS: list[str] = PROJECT_SHEET_COLUMNS


def days_outstanding(invoice_date: Optional[str], asof_iso: str) -> Optional[int]:
    """Days between invoice_date and asof. None on bad input."""
    if not invoice_date:
        return None
    try:
        from datetime import date
        a = date.fromisoformat(invoice_date)
        b = date.fromisoformat(asof_iso[:10])
        return max(0, (b - a).days)
    except Exception:
        return None


def invoice_to_sheet_row(
    rec: ExtractedInvoice,
    *,
    logged_at: str,
    invoice_link: str = "",
    asof_iso: Optional[str] = None,
) -> list[Any]:
    """Project an ExtractedInvoice into a row matching PROJECT_SHEET_COLUMNS.

    days_outstanding is computed from `asof_iso` (defaults to logged_at).
    Markup math is applied here so the sheet row is final. doc_type='invoice'.
    """
    rec.compute_invoiceable_amount()
    asof = asof_iso or logged_at
    days_out = days_outstanding(rec.invoice_date, asof)
    ck = invoice_content_key(rec.vendor, rec.invoice_number, rec.total)
    return [
        logged_at,
        "invoice",
        rec.invoice_date or "",
        rec.due_date or "",
        rec.vendor or "",
        rec.invoice_number or "",
        rec.po_number or "",
        rec.category,
        rec.subtotal if rec.subtotal is not None else "",
        rec.tax if rec.tax is not None else "",
        rec.total if rec.total is not None else "",
        rec.currency,
        "TRUE" if rec.billable else "FALSE",
        rec.markup_pct,
        rec.invoiceable_amount if rec.invoiceable_amount is not None else "",
        rec.status,
        days_out if days_out is not None else "",
        rec.payment_terms or "",
        rec.project_code or "",
        rec.bill_to or "",
        rec.remit_to or "",
        rec.source_kind,
        rec.source_id or "",
        invoice_link,
        round(rec.confidence, 2),
        rec.notes or "",
        ck or "",
    ]


def receipt_to_project_sheet_row(
    rec: Any,
    *,
    project_code: Optional[str],
    billable: bool,
    markup_pct: float,
    logged_at: str,
    receipt_link: str = "",
) -> list[Any]:
    """Project an ExtractedReceipt (from `receipts` module) into the same
    schema as invoices, with doc_type='receipt' and invoice-specific fields
    blank.

    Receipts are by definition already paid, so:
      - status              = 'PAID'
      - days_outstanding    = 0
      - payment_terms       = ''  (not applicable)
      - due_date            = ''  (already paid)
      - invoice_number      = ''  (use last_4 in notes if you need to tie out)
      - po_number/bill_to/remit_to all blank

    Billable + markup come from the project record (caller-supplied).
    invoiceable_amount is computed locally — no method on ExtractedReceipt.
    """
    total = rec.total
    invoiceable = None
    if billable and total is not None:
        try:
            invoiceable = round(
                float(total) * (1.0 + (markup_pct or 0.0) / 100.0), 2,
            )
        except (TypeError, ValueError):
            invoiceable = None

    # Re-use receipts.content_key for receipt dedup. Falls back to None when
    # merchant or total is missing — caller must rely on source_id alone.
    try:
        import receipts as _r
        ck = _r.content_key(rec.merchant, rec.date, rec.total, rec.last_4)
    except Exception:
        ck = None

    return [
        logged_at,
        "receipt",
        rec.date or "",
        "",                          # due_date
        rec.merchant or "",          # vendor column ← receipt merchant
        "",                          # invoice_number
        "",                          # po_number
        rec.category,
        rec.subtotal if rec.subtotal is not None else "",
        rec.tax if rec.tax is not None else "",
        rec.total if rec.total is not None else "",
        rec.currency,
        "TRUE" if billable else "FALSE",
        markup_pct,
        invoiceable if invoiceable is not None else "",
        "PAID",                      # receipts always already paid
        0,                            # days_outstanding
        "",                          # payment_terms
        project_code or "",
        "",                          # bill_to
        "",                          # remit_to
        rec.source_kind,
        rec.source_id or "",
        receipt_link,
        round(rec.confidence, 2),
        rec.notes or "",
        ck or "",
    ]


# Mapping invoice categories → QuickBooks accounts. Same identity-mapping
# pattern as the receipt path — DEFAULT_CATEGORIES already aligns with QBO.
def invoice_to_qb_row(rec: ExtractedInvoice) -> list[Any]:
    """Project an ExtractedInvoice into a QuickBooks Bills-import CSV row.

    QBO Bills CSV expects:
        BillNo, Vendor, Date, Due Date, Account, Amount, Currency, Memo
    """
    memo_parts = []
    if rec.project_code:
        memo_parts.append(f"Project: {rec.project_code}")
    if rec.po_number:
        memo_parts.append(f"PO: {rec.po_number}")
    if rec.notes:
        memo_parts.append(rec.notes)
    if rec.source_id:
        memo_parts.append(f"src:{rec.source_kind}:{rec.source_id}")
    return [
        rec.invoice_number or "",
        rec.vendor or "",
        rec.invoice_date or "",
        rec.due_date or "",
        rec.category,
        rec.total if rec.total is not None else "",
        rec.currency,
        " | ".join(memo_parts),
    ]


QB_INVOICE_CSV_COLUMNS: list[str] = [
    "BillNo", "Vendor", "Date", "Due Date", "Account", "Amount",
    "Currency", "Memo",
]
