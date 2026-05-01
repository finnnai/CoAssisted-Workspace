# © 2026 CoAssisted Workspace. Licensed under MIT.
"""GL classifier — Tier 3 LLM fallback.

When tier 0 (operator merchant map), tier 1 (MCC table), and tier 2
(JE-trained memo matcher) all miss or all return LOW confidence, this
module asks Claude-haiku to pick the most likely Workday Ledger Account.

Cost: each call is ~$0.001-0.002 against haiku-4-5. Designed to be the
last-resort tier — if the operator map and JE matcher are healthy, this
fires only on novel merchants the model has never seen.

Output is parsed strictly: Claude returns either an exact GL account
string from the AP_EXPENSE_GL_ACCOUNTS list, or the literal `NONE`
when it can't tell. We don't accept free-form text. If the response
doesn't match a known account, the result is None and the caller
falls back to the clearing account.

Every result lands in the review queue at LOW confidence regardless —
LLM guesses for novel merchants always need an operator approval
before posting.
"""

from __future__ import annotations

from typing import Optional


# =============================================================================
# Candidate GL accounts (AP-relevant subset of the chart of accounts)
# =============================================================================

# 86 total expense accounts in Surefox's COA. We give the LLM only the
# subset that AP card / vendor-invoice spend actually lands in:
# - COS items (51000-53500): rebillable, facilities, vehicles, supplies,
#   IT, travel, consulting
# - SG&A items (62000-65000): same categories at the admin layer
# - 62350: Dues, Subscriptions, Services (high-volume SaaS bucket)
#
# Excludes:
# - Payroll lines (50000-50015, 60000-60090): handled by payroll runs,
#   not card spend
# - 80000s (depreciation, amortization): handled by close journals
# - Suspense accounts (999996/999997): system-only
AP_EXPENSE_GL_ACCOUNTS: list[str] = [
    # --- COS (cost of sales — field operations) ---
    "51000:Rebillable Expenses - COS",
    "52000:Facilities - COS",
    "52100:Vehicles - COS",
    "52200:Supplies & Equipment- COS",
    "52300:IT Expenses - COS",
    "53000:Travel - COS",
    "53500:Consulting Services - COS",
    # --- SG&A (admin / corporate) ---
    "62000:Facilities",
    "62100:Vehicles",
    "62200:Supplies & Equipment",
    "62300:IT Expenses",
    "62350:Dues, Subscriptions, Services",
    "62400:Accounting Services",
    "62500:Recruiting Expenses",
    "62600:Insurance",
    "62700:Bad Debt Expense",
    "62800:Interest & Penalties",
    "62900:Legal Services",
    "63000:Travel",
    "63100:Entertainment",
    "63200:Charitable Gifts",
    "63300:Marketing & Advertising",
    "63400:Training",
    "63500:Consulting Services",
    "63600:BoD Expenses",
    "63700:Taxes",
    "64000:Licenses & Permits",
    "64100:Discounts Taken",
    "65000:Intercompany Expense",
]

# Membership set for fast validation of LLM responses.
_VALID_ACCOUNTS = frozenset(AP_EXPENSE_GL_ACCOUNTS)


# =============================================================================
# Prompt
# =============================================================================

_SYSTEM_PROMPT = """You are a Workday accounting classifier for Surefox North America Inc., a security services company.

Given a credit-card or vendor invoice transaction, you pick the single most likely Workday Ledger Account from a fixed list. You ONLY return one of the accounts from the candidate list, or the literal string NONE if you genuinely can't tell.

Decision rules:
- COS accounts (51000-53500) are for field-operations spend tied to client engagements: guards on duty, vehicles for active patrols, supplies issued to field teams, travel to client sites.
- SG&A accounts (62000-65000) are for admin / corporate spend: office facilities, admin vehicles, corporate IT, internal travel, marketing.
- When ambiguous, lean on the cardholder's role + memo text. If the cardholder appears to be on a field team, prefer COS. If admin/exec, prefer SG&A.
- 62350 (Dues, Subscriptions, Services) is the right bucket for SaaS subscriptions, professional memberships, software licenses.
- 53000/63000 (Travel) covers airlines, hotels, rental cars, taxis, parking — distinguished by COS vs SG&A based on cardholder role.

Output ONLY the account string (e.g. "62300:IT Expenses") or NONE. No commentary, no explanation, no JSON wrapping."""


def _build_user_prompt(
    *,
    merchant: str,
    mcc: Optional[int],
    mcc_description: Optional[str],
    memo: Optional[str],
    amount: Optional[float],
    cardholder_email: Optional[str],
    department_hint: Optional[str],
) -> str:
    """Assemble the per-transaction prompt with the candidate list."""
    lines = [
        "Transaction:",
        f"  Merchant: {merchant!r}",
    ]
    if mcc is not None:
        lines.append(f"  MCC: {mcc}" + (f" ({mcc_description})" if mcc_description else ""))
    if memo:
        lines.append(f"  Memo: {memo!r}")
    if amount is not None:
        lines.append(f"  Amount: ${amount:.2f}")
    if cardholder_email:
        lines.append(f"  Cardholder email: {cardholder_email}")
    if department_hint:
        lines.append(f"  Department hint: {department_hint}")

    lines.append("")
    lines.append("Candidate accounts (you MUST pick one of these or return NONE):")
    for acct in AP_EXPENSE_GL_ACCOUNTS:
        lines.append(f"  - {acct}")
    lines.append("")
    lines.append("Return only the account string or NONE.")
    return "\n".join(lines)


# =============================================================================
# Public entry point
# =============================================================================

def classify_via_llm(
    *,
    merchant_name: str,
    mcc_code: Optional[int] = None,
    mcc_description: Optional[str] = None,
    memo: Optional[str] = None,
    amount: Optional[float] = None,
    cardholder_email: Optional[str] = None,
    department_hint: Optional[str] = None,
) -> Optional[str]:
    """Ask Claude-haiku to classify a novel transaction.

    Returns the predicted GL account string, or None if:
        - LLM is unavailable (no API key, network error, etc.)
        - LLM returned NONE (genuinely couldn't tell)
        - LLM returned a string that doesn't match the candidate list
          (defensive — we never trust free-form output)

    The caller (gl_classifier.classify_transaction) wraps the result in
    LOW confidence regardless — even a confident-looking LLM answer needs
    operator approval before posting.
    """
    if not merchant_name:
        return None

    # Imported lazily so test runs that don't exercise tier 3 don't load
    # the anthropic SDK.
    try:
        import llm
    except ImportError:
        return None

    ok, _reason = llm.is_available()
    if not ok:
        return None

    prompt = _build_user_prompt(
        merchant=merchant_name,
        mcc=mcc_code,
        mcc_description=mcc_description,
        memo=memo,
        amount=amount,
        cardholder_email=cardholder_email,
        department_hint=department_hint,
    )

    try:
        result = llm.call_simple(
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            max_tokens=64,    # We only need a short account string.
            temperature=0.0,  # Determinism — same transaction → same answer.
        )
    except Exception:
        # Any LLM transport / parse error → graceful None.
        # The caller falls back to the clearing account.
        return None

    text = (result.get("text") or "").strip()
    if not text or text.upper() == "NONE":
        return None

    # Strict validation — never trust free-form LLM output.
    if text in _VALID_ACCOUNTS:
        return text

    # Sometimes the model wraps the answer in quotes or code fences.
    cleaned = text.strip("`\"'").strip()
    if cleaned in _VALID_ACCOUNTS:
        return cleaned

    # Last try: extract the leading account number and match by prefix.
    # If the model wrote "62300:IT Expenses (admin)" we still want 62300.
    head = cleaned.split()[0] if cleaned else ""
    for acct in AP_EXPENSE_GL_ACCOUNTS:
        if acct.startswith(head):
            return acct

    return None
