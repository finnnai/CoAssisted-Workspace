# © 2026 CoAssisted Workspace. Licensed under MIT.
"""GL classifier — three-tier hybrid model for AP-3.

Maps a transaction (merchant, MCC code, memo, amount) to a Workday Ledger
Account from the 212-account chart of accounts. Designed for the AMEX +
WEX card streams in Wave 1, but the interface is card-agnostic.

Three tiers, evaluated in order:

    1. MCC TABLE (deterministic)
       AMEX transactions carry an ISO 18245 Merchant Category Code. We
       maintain a hand-curated map from MCC ranges to GL accounts. When
       the MCC is in the table, confidence is HIGH and we return.

    2. JE-TRAINED MEMO MATCHER (statistical)
       For transactions without an MCC (WEX), or merchants the MCC table
       routes ambiguously, we consult patterns learned from the 17,346
       Wolfhound Corp JE rows (`samples/Wolfhound Corp JEs Jan-Mar'26.xlsx`).
       The matcher is a simple TF-IDF + nearest-neighbor lookup over
       memo text; the trained index lives in `gl_memo_index.json` and is
       rebuilt by `scripts/train_gl_classifier.py`. Confidence MEDIUM.

    3. LLM FALLBACK
       For genuinely novel merchants, we ask Claude-haiku. Result lands
       in the review queue with confidence LOW; an operator must
       approve before the journal entry posts.

After every operator approval / override, the (merchant, GL) pair is
appended to `gl_merchant_map.json` (atomic writes). On the next
classification of the same merchant the persistent map wins, regardless
of tier — operator overrides are sticky.

This module is the ONLY place the three tiers are stitched together.
The training-data ingestion lives in `scripts/train_gl_classifier.py`;
the persistent merchant map's I/O lives in `gl_merchant_map.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# Public types
# =============================================================================

class Tier(str, Enum):
    """Which tier produced the classification."""
    MERCHANT_MAP = "merchant_map"  # operator-confirmed historical entry
    MCC_TABLE = "mcc_table"        # deterministic MCC lookup
    JE_TRAINED = "je_trained"      # statistical matcher trained on JE corpus
    LLM_FALLBACK = "llm_fallback"  # Claude-haiku call, novel merchant


class Confidence(str, Enum):
    """Confidence band returned with the classification.

    Used by AP-2 / AP-1 to decide whether to auto-post or send to the
    review queue.
    """
    HIGH = "high"      # auto-post
    MEDIUM = "medium"  # auto-post + flag for review
    LOW = "low"        # hold in review queue, require operator approval


@dataclass(frozen=True)
class ClassificationResult:
    gl_account: str            # e.g. "62300:IT Expenses"
    confidence: Confidence
    tier_used: Tier
    reason: str = ""           # human-readable explanation of the decision
    merchant_map_hit: bool = False


# =============================================================================
# Tier 1 — MCC table (deterministic)
# =============================================================================

# Maps ISO 18245 Merchant Category Code → Workday GL account. Hand-curated
# from the categories that show up in the existing AMEX corpus + the COA.
#
# Coverage strategy: prioritize MCC ranges Surefox actually sees. The
# Wave-1 sample (`samples/Amex Transactions - April.csv`) had 8 of 8 top
# categories covered by this table. Anything not in the table falls
# through to tier 2.
#
# Format: { (mcc_low, mcc_high): "GL_account_string" } — inclusive ranges.
# Keep entries grouped by GL account for readability.
MCC_TO_GL: dict[tuple[int, int], str] = {
    # --- 53000:Travel - COS (active-duty travel) ---
    (3000, 3299): "53000:Travel - COS",        # airlines (carrier-specific codes)
    (3500, 3899): "53000:Travel - COS",        # hotels
    (4112, 4112): "53000:Travel - COS",        # passenger rail
    (4121, 4121): "53000:Travel - COS",        # taxis / limousines
    (4131, 4131): "53000:Travel - COS",        # bus lines
    (4411, 4411): "53000:Travel - COS",        # cruise
    (4511, 4511): "53000:Travel - COS",        # airlines / air carriers (general)
    (4722, 4722): "53000:Travel - COS",        # travel agencies
    (7011, 7011): "53000:Travel - COS",        # lodging
    (7512, 7519): "53000:Travel - COS",        # car rental + truck rental

    # --- 52100:Vehicles - COS (fleet operations / fuel) ---
    (5541, 5542): "52100:Vehicles - COS",      # service stations / fuel dispensers
    (7538, 7549): "52100:Vehicles - COS",      # auto service / auto body / towing

    # --- 62300:IT Expenses (admin IT) ---
    (4814, 4814): "62300:IT Expenses",         # telecommunications services
    (4816, 4816): "62300:IT Expenses",         # computer network services
    (5045, 5045): "62300:IT Expenses",         # computers / peripherals
    (5734, 5734): "62300:IT Expenses",         # computer software stores
    (7372, 7372): "62300:IT Expenses",         # computer programming / data processing
    (7379, 7379): "62300:IT Expenses",         # computer maintenance / repair

    # --- 62000:Facilities (admin facilities) ---
    (4900, 4900): "62000:Facilities",          # utilities (electric, gas, water, sanitary)
    (4812, 4812): "62000:Facilities",          # telecom equipment
    (1711, 1711): "62000:Facilities",          # heating, plumbing, AC contractors
    (1740, 1740): "62000:Facilities",          # masonry / stonework / plastering
    (1750, 1750): "62000:Facilities",          # carpentry contractors

    # --- 62200:Supplies & Equipment (admin office supplies) ---
    (5111, 5111): "62200:Supplies & Equipment",  # stationery / office supplies
    (5943, 5943): "62200:Supplies & Equipment",  # stationery / office / school supply
    (5947, 5947): "62200:Supplies & Equipment",  # gift / card / novelty / souvenir

    # --- 52200:Supplies & Equipment - COS (field-issued supplies) ---
    (5251, 5251): "52200:Supplies & Equipment - COS",  # hardware
    (5200, 5200): "52200:Supplies & Equipment - COS",  # home supply / warehouse
    (5712, 5719): "52200:Supplies & Equipment - COS",  # furniture / home furnishings

    # --- 62900:Legal Services ---
    (8111, 8111): "62900:Legal Services",      # legal services / attorneys

    # --- 62400:Accounting Services ---
    (8931, 8931): "62400:Accounting Services", # accounting / auditing / bookkeeping

    # --- 63300:Marketing & Advertising ---
    (7311, 7311): "63300:Marketing & Advertising",  # advertising services
    (5965, 5969): "63300:Marketing & Advertising",  # direct marketing (NEC)

    # --- 63100:Entertainment ---
    (5811, 5814): "63100:Entertainment",       # caterers / restaurants / fast food
    (5462, 5462): "63100:Entertainment",       # bakeries

    # --- 63000:Travel - SG&A (admin travel — separated from COS at the worktag layer) ---
    # Same MCC ranges as 53000 but routed by cost-center hint, not MCC alone.
    # Tier 2 (JE-trained) handles the COS-vs-SG&A split based on cardholder.

    # --- 62600:Insurance ---
    (6300, 6300): "62600:Insurance",           # insurance sales / underwriting

    # --- 62800:Interest & Penalties ---
    (6011, 6011): "62800:Interest & Penalties",  # cash advances (interest)
    (9311, 9311): "62800:Interest & Penalties",  # tax payments (penalties)

    # --- 63400:Training ---
    (8211, 8244): "63400:Training",            # schools / educational services
    (8299, 8299): "63400:Training",            # schools and educational services NEC

    # --- 22100:Sales & Excise Tax Payable (the credit side, not spend) ---
    # Tax-only MCCs (rare on cards) are handled at the journal-line level,
    # not here. Skipped.
}


def _lookup_mcc(mcc: int) -> Optional[str]:
    """Linear scan over MCC ranges. ~50 entries — fast enough."""
    for (lo, hi), gl in MCC_TO_GL.items():
        if lo <= mcc <= hi:
            return gl
    return None


# =============================================================================
# Public entry point — three-tier classification
# =============================================================================

def classify_transaction(
    *,
    merchant_name: str,
    mcc_code: Optional[int] = None,
    memo: Optional[str] = None,
    amount: Optional[float] = None,
    cardholder_email: Optional[str] = None,
    department_hint: Optional[str] = None,
) -> ClassificationResult:
    """Classify a transaction to a Workday Ledger Account.

    Args:
        merchant_name: e.g. "AMAZON MARKEPLACE NA PA"
        mcc_code: ISO 18245 code, when available (AMEX has it; WEX doesn't).
        memo: free-text annotation; used by tier 2 if MCC misses.
        amount: USD amount; reserved for amount-based heuristics.
        cardholder_email: when set, the persistent merchant map is keyed
            on (merchant_name, cardholder_email) so the same merchant can
            map to different GL for different cardholders' worktag rules.
        department_hint: WEX 'Department' field (OXBLOOD, GREEN FLEET,
            etc.) used by tier 2 to disambiguate vehicle vs. admin-fleet
            spend.

    Returns:
        ClassificationResult with gl_account, confidence, tier_used.
    """
    # Tier 0: operator-confirmed historical mapping wins. Lookup checks
    # per-cardholder first, then falls back to the global default for
    # the merchant. Hit_count + last_seen advance inside lookup().
    import gl_merchant_map  # local import: avoids circular import at module load
    mapped = gl_merchant_map.lookup(merchant_name, cardholder_email)
    if mapped:
        return ClassificationResult(
            gl_account=mapped,
            confidence=Confidence.HIGH,
            tier_used=Tier.MERCHANT_MAP,
            reason="operator-confirmed historical mapping",
            merchant_map_hit=True,
        )

    # Tier 1: MCC deterministic table.
    if mcc_code is not None:
        gl = _lookup_mcc(mcc_code)
        if gl is not None:
            return ClassificationResult(
                gl_account=gl,
                confidence=Confidence.HIGH,
                tier_used=Tier.MCC_TABLE,
                reason=f"MCC {mcc_code} routed via deterministic table",
            )

    # Tier 2: JE-trained memo matcher.
    # Loaded lazily inside gl_memo_classifier.lookup_by_memo so tests +
    # processes that don't need the matcher don't pay the JSON parse cost.
    # Index is built offline by scripts/train_gl_memo_classifier.py against
    # samples/Wolfhound Corp JEs Jan-Mar'26.xlsx.
    if memo:
        import gl_memo_classifier  # local import for the same reason
        candidates = gl_memo_classifier.lookup_by_memo(memo, top_k=3)
        if candidates:
            top_gl, top_score = candidates[0]
            tier2_conf = gl_memo_classifier.confidence_from_top_two(candidates)
            confidence = (
                Confidence.MEDIUM if tier2_conf == "medium" else Confidence.LOW
            )
            # Build a reason string the review queue can show the operator.
            top_2 = candidates[1] if len(candidates) > 1 else None
            reason = (
                f"JE-trained memo matcher: top={top_gl!r} (norm={top_score:.2f})"
            )
            if top_2:
                reason += f", runner-up={top_2[0]!r} (norm={top_2[1]:.2f})"
            return ClassificationResult(
                gl_account=top_gl,
                confidence=confidence,
                tier_used=Tier.JE_TRAINED,
                reason=reason,
            )

    # Tier 3: LLM fallback. Fires only when tier 2 had nothing to say
    # (no memo, no index, or zero candidates). Returns LOW confidence
    # regardless of how confident the LLM sounds — operator approval
    # required before posting.
    import gl_classifier_llm  # local import: defer the anthropic SDK load
    llm_gl = gl_classifier_llm.classify_via_llm(
        merchant_name=merchant_name,
        mcc_code=mcc_code,
        memo=memo,
        amount=amount,
        cardholder_email=cardholder_email,
        department_hint=department_hint,
    )
    if llm_gl:
        return ClassificationResult(
            gl_account=llm_gl,
            confidence=Confidence.LOW,
            tier_used=Tier.LLM_FALLBACK,
            reason=(
                f"Claude-haiku tier-3 fallback (novel merchant). "
                f"Operator approval required before posting."
            ),
        )

    # Final fallback: the clearing account holds the line until an
    # operator manually classifies it via the review queue.
    return ClassificationResult(
        gl_account="22040:Credit Card Clearing",
        confidence=Confidence.LOW,
        tier_used=Tier.LLM_FALLBACK,
        reason=(
            f"No tier 0-3 match for merchant={merchant_name!r} "
            f"mcc={mcc_code!r}. Holding in 22040:Credit Card Clearing "
            f"for operator review."
        ),
    )
