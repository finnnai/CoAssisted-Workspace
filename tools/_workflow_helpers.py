# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Shared helpers for the cross-service workflow modules.

These were originally module-private functions in the old
monolithic tools/workflows.py. After P1-1 we keep them in one
place so the 5 split modules can import them without circular
imports. tools/workflows.py itself becomes a thin shim that
re-exports these for backwards compat with any test that
patches/inspects the old `tools.workflows._helper` path.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Optional

from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
from pydantic import BaseModel, ConfigDict, Field

import config
import crm_stats
import gservices
import rendering
import templates as templates_mod
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from tools.contacts import _flatten_person  # noqa: E402 — reuse the flattening logic

# Inline MIME builder import — we can't cleanly import from tools.gmail without
# a circular import, so we use the email stdlib directly here.
import mimetypes
from email.message import EmailMessage

# Module globals (geocode cache state) ---
_GEOCODE_CACHE: dict[str, dict] | None = None
_GEOCODE_CACHE_PATH: "Path | None" = None  # type: ignore  # noqa


def _gmail():
    return gservices.gmail()

def _drive():
    return gservices.drive()

def _calendar_svc():
    return gservices.calendar()

def _log_activity_on_contact(email: str, subject: str, template_name: str | None = None) -> None:
    """If the email matches a saved contact, append a timestamped activity note to its biography.

    Silently no-ops if the contact isn't found or if the append fails. We don't
    want a note-logging problem to break the send path.
    """
    if not email:
        return
    try:
        import datetime as _dt
        people = gservices.people()
        # Warm up search (documented People API quirk).
        people.people().searchContacts(query="", readMask="names,metadata").execute()
        resp = people.people().searchContacts(
            query=email, readMask="names,emailAddresses,biographies,metadata", pageSize=5
        ).execute()
        person = None
        for r in resp.get("results", []):
            addrs = [
                (e.get("value") or "").lower()
                for e in r["person"].get("emailAddresses", []) or []
            ]
            if email.lower() in addrs:
                person = r["person"]
                break
        if not person:
            return
        now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        suffix = f" (template: {template_name})" if template_name else ""
        note = f'[{now}] Sent: "{subject}"{suffix}'
        bios = person.get("biographies") or []
        prev = bios[0].get("value", "") if bios else ""
        combined = (prev + "\n\n" + note).strip() if prev else note
        people.people().updateContact(
            resourceName=person["resourceName"],
            updatePersonFields="biographies",
            body={
                "etag": person["etag"],
                "biographies": [{"value": combined, "contentType": "TEXT_PLAIN"}],
            },
        ).execute()
        log.info("activity logged on %s for %s", person["resourceName"], email)
    except Exception as e:
        log.warning("activity log skipped for %s: %s", email, e)

def _list_attachments_meta(payload: dict, acc: list | None = None) -> list[dict]:
    if acc is None:
        acc = []
    filename = payload.get("filename")
    body = payload.get("body", {})
    if filename and body.get("attachmentId"):
        acc.append(
            {
                "attachment_id": body["attachmentId"],
                "filename": filename,
                "mime_type": payload.get("mimeType"),
                "size": body.get("size", 0),
            }
        )
    for part in payload.get("parts", []) or []:
        _list_attachments_meta(part, acc)
    return acc

def _extract_plaintext(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    out: list[str] = []
    for part in payload.get("parts", []) or []:
        chunk = _extract_plaintext(part)
        if chunk:
            out.append(chunk)
    return "\n\n".join(out)

def _extract_address_block(p: dict) -> dict:
    """Pick the best mailing address from a People API person record.

    Preference order: 'work' → 'home' → first listed. Returns an empty-ish dict
    if no addresses are present.
    """
    addrs = p.get("addresses") or []
    if not addrs:
        return {}
    by_type = {a.get("type"): a for a in addrs if a.get("type")}
    chosen = by_type.get("work") or by_type.get("home") or addrs[0]
    return {
        "formatted": chosen.get("formattedValue"),
        "street": chosen.get("streetAddress"),
        "city": chosen.get("city"),
        "region": chosen.get("region"),
        "postal_code": chosen.get("postalCode"),
        "country": chosen.get("country"),
        "type": chosen.get("type"),
    }

def _geocode_cache_path():
    from pathlib import Path as _P
    import config as _cfg
    project_dir = _P(_cfg.__file__).parent
    cache_dir = project_dir / "logs"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / "geocode_cache.json"

def _load_geocode_cache() -> dict[str, dict]:
    global _GEOCODE_CACHE
    if _GEOCODE_CACHE is not None:
        return _GEOCODE_CACHE
    path = _geocode_cache_path()
    if path.exists():
        try:
            _GEOCODE_CACHE = json.loads(path.read_text())
        except Exception:
            log.warning("geocode_cache.json corrupt — starting fresh")
            _GEOCODE_CACHE = {}
    else:
        _GEOCODE_CACHE = {}
    return _GEOCODE_CACHE

def _save_geocode_cache():
    if _GEOCODE_CACHE is None:
        return
    try:
        _geocode_cache_path().write_text(json.dumps(_GEOCODE_CACHE, indent=2))
    except Exception as e:
        log.warning("Failed to save geocode_cache: %s", e)

def _geocode_cached(address: str) -> dict | None:
    """Geocode `address` using the JSON cache; fall through to live API on miss.

    Returns a dict with keys: lat, lng, formatted_address, place_id, source ('cache' or 'api').
    Returns None if both cache miss AND live API call fail.
    """
    if not address or not address.strip():
        return None
    key = address.strip().lower()
    cache = _load_geocode_cache()
    if key in cache:
        hit = dict(cache[key])
        hit["source"] = "cache"
        return hit
    try:
        gmaps = gservices.maps()
        gres = gmaps.geocode(address)
        if not gres:
            return None
        loc = gres[0]["geometry"]["location"]
        record = {
            "lat": loc["lat"], "lng": loc["lng"],
            "formatted_address": gres[0].get("formatted_address", address),
            "place_id": gres[0].get("place_id"),
        }
        cache[key] = record
        _save_geocode_cache()
        out = dict(record); out["source"] = "api"
        return out
    except Exception as e:
        log.warning("Geocode failed for %r: %s", address, e)
        return None

def _resolve_current_location(
    manual: str | None = None,
    mode: str = "auto",
) -> dict | None:
    """Resolve the user's current physical location.

    Priority order: manual override → macOS CoreLocationCLI → IP geolocation.
    Best-effort. Returns None if all methods fail or mode is 'off'/'home'.

    Returns:
        dict with keys: lat (float), lng (float), accuracy_m (float),
        source (str), formatted_address (str | None), or None on failure.

    Args:
        manual: If provided AND mode is 'auto' or 'manual', geocode this
            string and return immediately. Always wins.
        mode: 'auto' = try all methods, 'home' = skip (caller falls back to
            home_address), 'off' = skip entirely, 'manual' = require `manual`.
    """
    if mode in ("off", "home"):
        return None

    # 1. Manual override — always wins if provided.
    if manual:
        try:
            gmaps = gservices.maps()
            gres = gmaps.geocode(manual)
            if gres:
                loc = gres[0]["geometry"]["location"]
                return {
                    "lat": loc["lat"], "lng": loc["lng"],
                    "accuracy_m": 0.0, "source": "manual",
                    "formatted_address": gres[0].get("formatted_address", manual),
                }
            log.warning("Manual location geocode returned no results: %s", manual)
        except Exception as e:
            log.warning("Manual location geocode failed: %s", e)

    if mode == "manual":
        # If manual was required but failed, don't fall through to detection.
        return None

    # 2. macOS CoreLocationCLI (~10m accuracy, requires `brew install corelocationcli`).
    # Cowork's MCP subprocess often doesn't inherit the user's shell PATH, so
    # the bare command name `CoreLocationCLI` may not be found. Try common
    # Homebrew install paths explicitly before falling back to PATH lookup.
    import subprocess
    import shutil
    from pathlib import Path as _CLPath
    candidate_paths = [
        "/opt/homebrew/bin/CoreLocationCLI",   # Apple Silicon Homebrew
        "/usr/local/bin/CoreLocationCLI",      # Intel Homebrew
        "/opt/local/bin/CoreLocationCLI",      # MacPorts
    ]
    cli_path = next((p for p in candidate_paths if _CLPath(p).exists()), None)
    if cli_path is None:
        # Final fallback: PATH lookup.
        cli_path = shutil.which("CoreLocationCLI")
    if cli_path:
        try:
            proc = subprocess.run(
                [cli_path, "-format",
                 "%latitude,%longitude,%horizontalAccuracy"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                out = proc.stdout.strip()
                # CoreLocationCLI's `-format` flag is permissive: comma-format
                # works on some installs but most return whitespace-separated
                # `lat lng [accuracy]` regardless. Parse robustly: try comma
                # first, then any whitespace.
                if "," in out:
                    parts = [p.strip() for p in out.split(",") if p.strip()]
                else:
                    parts = out.split()
                if len(parts) >= 2:
                    try:
                        lat = float(parts[0])
                        lng = float(parts[1])
                    except ValueError:
                        log.warning(
                            "CoreLocationCLI [%s] output failed numeric parse: %r",
                            cli_path, out[:200],
                        )
                        lat = lng = None  # type: ignore
                    if lat is not None and lng is not None:
                        accuracy = 50.0  # ~50m typical for Wi-Fi triangulation
                        if len(parts) > 2:
                            try:
                                accuracy = float(parts[2])
                            except ValueError:
                                pass
                        # Reverse-geocode for a friendly address (best-effort).
                        addr = None
                        try:
                            gmaps = gservices.maps()
                            rev = gmaps.reverse_geocode((lat, lng))
                            if rev:
                                addr = rev[0].get("formatted_address")
                        except Exception:
                            pass
                        log.info(
                            "current_location via CoreLocationCLI [%s]: "
                            "%.5f,%.5f (±%.0fm)",
                            cli_path, lat, lng, accuracy,
                        )
                        return {
                            "lat": lat, "lng": lng, "accuracy_m": accuracy,
                            "source": "corelocationcli",
                            "formatted_address": addr,
                        }
                else:
                    log.warning(
                        "CoreLocationCLI [%s] output couldn't be split into "
                        "lat/lng: %r", cli_path, out[:200],
                    )
            else:
                log.warning(
                    "CoreLocationCLI [%s] returned rc=%d, stdout=%r, stderr=%r",
                    cli_path, proc.returncode,
                    proc.stdout[:200], proc.stderr[:200],
                )
        except subprocess.TimeoutExpired:
            log.warning(
                "CoreLocationCLI [%s] timed out after 10s — likely waiting for "
                "macOS Location Services permission. Open System Settings → "
                "Privacy & Security → Location Services and enable for the "
                "calling process (Terminal/Cowork).",
                cli_path,
            )
        except Exception as e:
            log.warning("CoreLocationCLI [%s] failed: %s", cli_path, e)
    else:
        log.info(
            "CoreLocationCLI not found in any of: %s — falling through to "
            "Google Geolocation API",
            ", ".join(candidate_paths),
        )

    # 3. Google Geolocation API (uses Maps API key, ~city accuracy via IP).
    try:
        import os
        import requests
        api_key = (
            config.get("google_maps_api_key")
            or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        )
        if api_key:
            url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}"
            resp = requests.post(url, json={"considerIp": True}, timeout=8)
            if resp.ok:
                data = resp.json()
                loc = data.get("location") or {}
                if "lat" in loc and "lng" in loc:
                    accuracy = float(data.get("accuracy", 5000))
                    addr = None
                    try:
                        gmaps = gservices.maps()
                        rev = gmaps.reverse_geocode((loc["lat"], loc["lng"]))
                        if rev:
                            addr = rev[0].get("formatted_address")
                    except Exception:
                        pass
                    log.info(
                        "current_location via Google Geolocation API: "
                        "%.4f,%.4f (±%.0fm)", loc["lat"], loc["lng"], accuracy,
                    )
                    return {
                        "lat": float(loc["lat"]), "lng": float(loc["lng"]),
                        "accuracy_m": accuracy,
                        "source": "google_geolocation",
                        "formatted_address": addr,
                    }
    except Exception as e:
        log.warning("Google Geolocation API failed: %s", e)

    # 4. IP geolocation final fallback (~10km city-level, no API key needed).
    try:
        import requests
        resp = requests.get("https://ipapi.co/json/", timeout=5)
        if resp.ok:
            data = resp.json()
            if "latitude" in data and "longitude" in data:
                addr_parts = [
                    str(data.get("city") or "").strip(),
                    str(data.get("region") or "").strip(),
                    str(data.get("country_name") or data.get("country") or "").strip(),
                ]
                addr = ", ".join(p for p in addr_parts if p)
                log.info(
                    "current_location via ipapi.co: %.4f,%.4f (~%s)",
                    data["latitude"], data["longitude"], addr,
                )
                return {
                    "lat": float(data["latitude"]),
                    "lng": float(data["longitude"]),
                    "accuracy_m": 10000.0,
                    "source": "ipapi_co",
                    "formatted_address": addr or None,
                }
    except Exception as e:
        log.warning("ipapi.co fallback failed: %s", e)

    log.warning("current_location: all detection methods failed")
    return None

def _contact_lat_lng(p: dict) -> tuple[float, float] | None:
    """Read stored lat/lng from a contact's userDefined fields. None if missing/invalid."""
    custom = {u.get("key"): u.get("value") for u in (p.get("userDefined") or [])}
    try:
        lat = float(custom.get("lat"))
        lng = float(custom.get("lng"))
        return (lat, lng)
    except (TypeError, ValueError):
        return None

def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lng) points in kilometers."""
    import math
    lat1, lng1 = a
    lat2, lng2 = b
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def _walk_all_contacts(
    person_fields: str = "names,emailAddresses,addresses,userDefined,memberships,organizations",
    only_groups: list[str] | None = None,
    max_contacts: int = 5000,
):
    """Generator yielding every saved contact (raw People API dicts).

    Honors `only_groups` membership filter. Pages through `connections.list`
    so it works on large address books.
    """
    svc = gservices.people()
    only_set = set(only_groups or []) or None
    page_token = None
    yielded = 0
    while yielded < max_contacts:
        kwargs: dict = {
            "resourceName": "people/me",
            "personFields": person_fields,
            "pageSize": 1000,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.people().connections().list(**kwargs).execute()
        for p in resp.get("connections", []) or []:
            if only_set:
                memb = {
                    (m.get("contactGroupMembership") or {}).get("contactGroupResourceName")
                    for m in (p.get("memberships") or [])
                }
                if not (memb & only_set):
                    continue
            yield p
            yielded += 1
            if yielded >= max_contacts:
                return
        page_token = resp.get("nextPageToken")
        if not page_token:
            return

def _set_contact_custom_fields(
    person_resource_name: str, etag: str, existing_userdefined: list[dict],
    updates: dict[str, str],
) -> str:
    """Merge `updates` into a contact's userDefined fields and PATCH via People API.

    Returns the new etag.
    """
    svc = gservices.people()
    by_key = {u.get("key"): dict(u) for u in (existing_userdefined or [])}
    for k, v in updates.items():
        if v is None:
            by_key.pop(k, None)
        else:
            by_key[k] = {"key": k, "value": str(v)}
    body = {"etag": etag, "userDefined": list(by_key.values())}
    resp = svc.people().updateContact(
        resourceName=person_resource_name,
        updatePersonFields="userDefined",
        body=body,
    ).execute()
    return resp.get("etag", etag)

def _resolve_to_address(stop: str) -> tuple[str, dict | None]:
    """Resolve a 'stop' (free-form address, lat,lng, or contact resource_name) to an address.

    Returns (address_string, contact_record_or_None).
    """
    if stop.startswith("people/"):
        svc = gservices.people()
        person = svc.people().get(
            resourceName=stop,
            personFields="names,addresses,emailAddresses,userDefined,organizations",
        ).execute()
        block = _extract_address_block(person)
        formatted = block.get("formatted")
        if not formatted:
            raise ValueError(f"Contact {stop} has no address on record.")
        return formatted, person
    return stop, None

def _build_simple_email(
    to: list[str], subject: str, body: str, *, cc: list[str] | None = None,
    attachment: tuple[bytes, str, str] | None = None, from_alias: str | None = None,
) -> dict:
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if from_alias:
        msg["From"] = from_alias
    msg.set_content(body)
    if attachment is not None:
        data, filename, mime = attachment
        maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}

def _resolve_recipient(r) -> dict:
    """Turn a RecipientInput into a flat field dict usable by the renderer.

    If resource_name is set, fetches the contact from People API and merges any
    inline overrides on top. Otherwise uses the inline fields directly.
    """
    fields: dict = {}
    if r.resource_name:
        people = gservices.people()
        person = (
            people.people()
            .get(
                resourceName=r.resource_name,
                personFields="names,emailAddresses,organizations,userDefined,metadata",
            )
            .execute()
        )
        fields.update(_flatten_person(person))
    # Inline overrides (always win over contact fetch).
    if r.email:
        fields["email"] = r.email
    if r.first_name is not None:
        fields["first_name"] = r.first_name
    if r.last_name is not None:
        fields["last_name"] = r.last_name
    if r.organization is not None:
        fields["organization"] = r.organization
    if r.title is not None:
        fields["title"] = r.title
    # Merge inline custom fields into both top-level (for easy {key} access)
    # and nested custom dict (preserving previous tool conventions).
    if r.custom:
        existing = fields.get("custom") or {}
        existing.update(r.custom)
        fields["custom"] = existing
        for k, v in r.custom.items():
            fields.setdefault(k, v)
    # If flattening exposed custom dict keys, promote to top-level too.
    for k, v in (fields.get("custom") or {}).items():
        fields.setdefault(k, v)
    return fields

def _parse_email_address(raw: str) -> str | None:
    """Extract the bare address out of 'Name <addr>' or plain 'addr'."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "<" in raw and ">" in raw:
        return raw[raw.rfind("<") + 1 : raw.rfind(">")].strip()
    return raw if "@" in raw else None

def _extract_display_name(raw: str) -> str:
    """Pull the human-readable part of 'Name <addr>' — '' if none present."""
    raw = (raw or "").strip()
    if not raw or "<" not in raw:
        return ""
    name = raw.split("<", 1)[0].strip()
    # Strip matched surrounding quotes.
    if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
        name = name[1:-1].strip()
    return name

def _split_display_name(name: str) -> tuple[str, str]:
    """Split 'First Last' or 'Last, First' into (first, last). Empty fields OK."""
    name = (name or "").strip()
    if not name:
        return "", ""
    if "," in name:
        # 'Last, First [M.]' — common directory format.
        last, _, rest = name.partition(",")
        return rest.strip(), last.strip()
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])

def _guess_name_from_email(email: str) -> tuple[str, str]:
    """Fallback name extraction from an email address's local-part.

    Examples:
      jane.smith@x → ('Jane', 'Smith')
      jane_smith@x → ('Jane', 'Smith')
      jsmith@x     → ('Jsmith', '')
      conor@x      → ('Conor', '')
    """
    local = (email or "").split("@", 1)[0]
    if not local:
        return "", ""
    # Split on common separators.
    import re as _re
    parts = [p for p in _re.split(r"[.\-_]+", local) if p]
    if not parts:
        return "", ""
    parts = [p.capitalize() for p in parts]
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])

def _strip_reply_prefixes(subject: str) -> str:
    s = subject
    while True:
        low = s.lstrip().lower()
        if low.startswith("re:") or low.startswith("fwd:") or low.startswith("fw:"):
            s = s.lstrip()[len("re:") if low.startswith("re:") else 3 :].lstrip()
        else:
            break
    return s.strip()

