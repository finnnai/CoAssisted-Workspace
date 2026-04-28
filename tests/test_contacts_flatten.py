"""Tests for tools.contacts._flatten_person and _build_person_body."""

from tools.contacts import _build_person_body, _flatten_person


def test_flatten_minimal():
    p = {
        "resourceName": "people/c1",
        "etag": "e1",
        "names": [{"displayName": "Josh Szott", "givenName": "Josh", "familyName": "Szott"}],
        "emailAddresses": [{"value": "josh@bajafox.com"}],
    }
    flat = _flatten_person(p)
    assert flat["first_name"] == "Josh"
    assert flat["last_name"] == "Szott"
    assert flat["email"] == "josh@bajafox.com"
    assert flat["emails"] == ["josh@bajafox.com"]
    assert flat["resource_name"] == "people/c1"


def test_flatten_custom_fields():
    p = {
        "resourceName": "people/c2",
        "etag": "e2",
        "userDefined": [
            {"key": "stage", "value": "prospect"},
            {"key": "tier", "value": "enterprise"},
        ],
    }
    flat = _flatten_person(p)
    assert flat["custom"] == {"stage": "prospect", "tier": "enterprise"}


def test_flatten_notes_joined():
    p = {
        "resourceName": "people/c3",
        "etag": "e3",
        "biographies": [{"value": "line1"}, {"value": "line2"}],
    }
    flat = _flatten_person(p)
    assert "line1" in flat["notes"]
    assert "line2" in flat["notes"]


def test_flatten_group_memberships():
    p = {
        "resourceName": "people/c4",
        "etag": "e4",
        "memberships": [
            {"contactGroupMembership": {"contactGroupResourceName": "contactGroups/abc"}},
            {"contactGroupMembership": {"contactGroupResourceName": "contactGroups/def"}},
        ],
    }
    flat = _flatten_person(p)
    assert "contactGroups/abc" in flat["groups"]
    assert "contactGroups/def" in flat["groups"]


def test_build_person_body_names_only():
    body = _build_person_body(first_name="Finnn", last_name="Ai")
    assert body == {"names": [{"givenName": "Finnn", "familyName": "Ai"}]}


def test_build_person_body_emails():
    body = _build_person_body(emails=["a@x.com", "b@x.com"])
    assert body["emailAddresses"] == [{"value": "a@x.com"}, {"value": "b@x.com"}]


def test_build_person_body_custom_fields():
    body = _build_person_body(custom_fields={"stage": "prospect"})
    assert body["userDefined"] == [{"key": "stage", "value": "prospect"}]


def test_build_person_body_empty_no_keys():
    """Passing no args produces an empty dict — safe to check before POST."""
    assert _build_person_body() == {}
