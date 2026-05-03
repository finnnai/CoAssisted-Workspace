# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""AP-4 Gmail Pub/Sub receiver (push endpoint).

Decodes Gmail's Pub/Sub push payload, fetches the new messages on the
`AP/Inbound` label, and dispatches each one to the receipt extractor.
Failures land in the v0.9.1 retry queue for delayed retry.

Module-level surface (for the HTTP-server caller):

    decode_pubsub_envelope(envelope) -> {history_id, email_address}
        Parse the push body Google sends.

    fetch_new_messages(history_id, *, label_id) -> list[dict]
        Hit gmail.users.history.list to find messages added since
        history_id, filtered to the AP/Inbound label.

    dispatch_message(message_id, *, label_id) -> dict
        Extract one receipt. On failure, enqueue for retry. Returns a
        summary dict.

    handle_push(envelope) -> dict
        End-to-end: decode → fetch → dispatch each. Returns the summary.

Run as a Flask/FastAPI handler, a Cloud Function, or a Cloud Run service.
The reference HTTP shim at the bottom of this file (commented-in if you
want to run as a standalone) shows how to wire it into Flask.

Provisioning (operator-side, not code):
    1. Create a Pub/Sub topic: `gmail-ap-inbound`
    2. Subscribe Gmail's push notifications to the topic via
       gmail.users.watch with topicName=projects/PROJECT/topics/gmail-ap-inbound
       and labelIds=[<AP/Inbound label id>]
    3. Add a push subscription with `pushEndpoint=https://YOUR/host/_pubsub`
    4. Re-call gmail.users.watch every 7 days (the watch expires)
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional


_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Envelope decode
# --------------------------------------------------------------------------- #


def decode_pubsub_envelope(envelope: dict) -> dict:
    """Pull the {historyId, emailAddress} out of a Pub/Sub push envelope.

    Push envelope shape:
        {
          "message": {
            "data": "<base64-encoded JSON {historyId, emailAddress}>",
            "messageId": "...",
            "publishTime": "..."
          },
          "subscription": "projects/.../subscriptions/..."
        }
    """
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a dict")
    msg = envelope.get("message")
    if not isinstance(msg, dict):
        raise ValueError("envelope.message missing")
    data_b64 = msg.get("data")
    if not data_b64:
        raise ValueError("envelope.message.data missing")
    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"envelope payload decode failed: {e}") from e
    return {
        "history_id": str(payload.get("historyId") or ""),
        "email_address": payload.get("emailAddress") or "",
        "raw_message_id": msg.get("messageId"),
        "publish_time": msg.get("publishTime"),
    }


# --------------------------------------------------------------------------- #
# Gmail fetch
# --------------------------------------------------------------------------- #


def fetch_new_messages(
    history_id: str,
    *,
    label_id: Optional[str] = None,
) -> list[dict]:
    """Use gmail.users.history.list to get every messageAdded since
    history_id, filtered to label_id when provided.

    Returns list of {message_id, thread_id, label_ids}.
    """
    import gservices  # type: ignore

    gmail = gservices.gmail_service()
    page_token: Optional[str] = None
    out: list[dict] = []
    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": history_id,
            "historyTypes": ["messageAdded"],
        }
        if label_id:
            kwargs["labelId"] = label_id
        if page_token:
            kwargs["pageToken"] = page_token
        resp = gmail.users().history().list(**kwargs).execute()
        for h in resp.get("history", []) or []:
            for added in h.get("messagesAdded", []) or []:
                m = added.get("message", {})
                if not m:
                    continue
                out.append({
                    "message_id": m.get("id"),
                    "thread_id": m.get("threadId"),
                    "label_ids": m.get("labelIds") or [],
                })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def dispatch_message(
    message_id: str,
    *,
    label_id: Optional[str] = None,
) -> dict:
    """Hand off one message to the receipt extractor. On any error,
    enqueue for retry and return a summary.

    Returns:
        {message_id, status, …}
        status ∈ {'extracted', 'queued_for_retry', 'skipped_unsupported',
                  'error_no_retry'}
    """
    try:
        # The receipt extractor lives in receipts.py with a one-message
        # entry point (workflow_extract_one_receipt's underlying logic).
        # We import lazily so this module stays importable in tests
        # without the full extractor stack.
        import receipts as _r  # type: ignore
        result = _r.extract_one_message(message_id=message_id)
        return {
            "message_id": message_id,
            "status": "extracted",
            "result": result,
        }
    except (ImportError, AttributeError):
        # The extractor doesn't expose a single-message entry yet —
        # queue the work for the sweep to pick up.
        try:
            import retry_queue as _rq
            entry = _rq.enqueue(
                {"message_id": message_id, "label_id": label_id},
                kind="receipt",
                error="extractor not available — queued for sweep",
                note="from pubsub_receipt_receiver",
            )
            return {
                "message_id": message_id,
                "status": "queued_for_retry",
                "retry_item_id": entry.get("item_id"),
            }
        except Exception as e:
            _log.exception("retry queue enqueue failed for %s", message_id)
            return {
                "message_id": message_id,
                "status": "error_no_retry",
                "error": f"{type(e).__name__}: {e}",
            }
    except Exception as e:
        # Real extractor error → retry queue.
        try:
            import retry_queue as _rq
            entry = _rq.enqueue(
                {"message_id": message_id, "label_id": label_id},
                kind="receipt",
                error=f"{type(e).__name__}: {e}",
            )
            return {
                "message_id": message_id,
                "status": "queued_for_retry",
                "retry_item_id": entry.get("item_id"),
                "error": f"{type(e).__name__}: {e}",
            }
        except Exception as e2:
            _log.exception("double-failure on message %s", message_id)
            return {
                "message_id": message_id,
                "status": "error_no_retry",
                "error": f"primary={type(e).__name__}: {e}; "
                         f"retry-enqueue={type(e2).__name__}: {e2}",
            }


# --------------------------------------------------------------------------- #
# End-to-end push handler
# --------------------------------------------------------------------------- #


def handle_push(envelope: dict, *, label_id: Optional[str] = None) -> dict:
    """Single entry point for the HTTP push handler. Decodes the
    envelope, fetches new messages, dispatches each, returns a summary.

    Returns:
        {history_id, email_address, fetched, dispatched: list[…],
         counts: {extracted, queued_for_retry, skipped_unsupported,
                  error_no_retry}}
    """
    decoded = decode_pubsub_envelope(envelope)
    history_id = decoded.get("history_id") or ""
    if not history_id:
        return {**decoded, "fetched": 0, "dispatched": [],
                "counts": {}, "error": "history_id missing"}

    messages = fetch_new_messages(history_id, label_id=label_id)
    dispatched = [dispatch_message(m["message_id"], label_id=label_id)
                  for m in messages]

    counts: dict[str, int] = {}
    for d in dispatched:
        s = d.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1

    return {
        "history_id": history_id,
        "email_address": decoded.get("email_address"),
        "raw_message_id": decoded.get("raw_message_id"),
        "fetched": len(messages),
        "dispatched": dispatched,
        "counts": counts,
    }


# --------------------------------------------------------------------------- #
# Reference HTTP shim — Flask
# --------------------------------------------------------------------------- #
#
# Uncomment + install Flask to run as a standalone push endpoint.
#
# from flask import Flask, request, jsonify
# app = Flask(__name__)
#
# @app.route("/_pubsub", methods=["POST"])
# def pubsub_endpoint():
#     try:
#         envelope = request.get_json(force=True)
#         summary = handle_push(envelope, label_id=os.environ.get("AP_INBOUND_LABEL_ID"))
#         return jsonify(summary), 200
#     except ValueError as e:
#         return jsonify({"error": str(e)}), 400
#     except Exception as e:
#         _log.exception("pubsub_endpoint failed")
#         # Returning 200 prevents Pub/Sub from retrying the same envelope
#         # — we've already enqueued the failure to retry_queue so it'll
#         # be reprocessed on its own schedule.
#         return jsonify({"status": "error", "error": str(e)}), 200
