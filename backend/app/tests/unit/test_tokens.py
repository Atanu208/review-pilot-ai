from __future__ import annotations

import time

import pytest

from app.models.enums import OrgRole
from app.schemas.organization import InvitationCreate
from app.security import tokens
from app.services.organizations import can_manage, slugify


def test_state_roundtrip() -> None:
    token = tokens.sign_state("oauth_login", {"n": "abc", "next": "/x"}, ttl_seconds=60)
    assert tokens.verify_state(token, "oauth_login") == {"n": "abc", "next": "/x"}


def test_state_wrong_purpose_rejected() -> None:
    token = tokens.sign_state("gh_install", {"org": "1"}, ttl_seconds=60)
    with pytest.raises(tokens.InvalidStateError, match="purpose"):
        tokens.verify_state(token, "oauth_login")


def test_state_tamper_rejected() -> None:
    token = tokens.sign_state("p", {"uid": "victim"}, ttl_seconds=60)
    _body, sig = token.split(".")
    forged = tokens.sign_state("p", {"uid": "attacker"}, ttl_seconds=60).split(".")[0]
    with pytest.raises(tokens.InvalidStateError, match="signature"):
        tokens.verify_state(f"{forged}.{sig}", "p")


def test_state_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    token = tokens.sign_state("p", {}, ttl_seconds=10)
    real = time.time
    monkeypatch.setattr("app.security.tokens.time.time", lambda: real() + 60)
    with pytest.raises(tokens.InvalidStateError, match="expired"):
        tokens.verify_state(token, "p")


@pytest.mark.parametrize("bad", [None, "", "a.b.c", "nodot", "x.y"])
def test_malformed_state_rejected(bad: str | None) -> None:
    with pytest.raises(tokens.InvalidStateError):
        tokens.verify_state(bad, "p")


def test_csrf_bound_to_session() -> None:
    a, b = tokens.hash_token("session-a"), tokens.hash_token("session-b")
    assert tokens.verify_csrf(a, tokens.csrf_token_for(a))
    assert not tokens.verify_csrf(b, tokens.csrf_token_for(a))
    assert not tokens.verify_csrf(a, None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/repositories", "/repositories"),
        ("/dashboard?x=1", "/dashboard?x=1"),
        ("https://evil.example", "/dashboard"),
        ("//evil.example", "/dashboard"),
        ("/\\evil.example", "/dashboard"),
        ("/ok\r\nSet-Cookie: x", "/dashboard"),
        (None, "/dashboard"),
    ],
)
def test_safe_redirect_path(value: str | None, expected: str) -> None:
    assert tokens.safe_redirect_path(value) == expected


def test_hash_token_is_sha256_hex() -> None:
    assert len(tokens.hash_token("x")) == 64
    assert tokens.new_token() != tokens.new_token()


@pytest.mark.parametrize(
    ("actor", "current", "new", "allowed"),
    [
        (OrgRole.OWNER, OrgRole.OWNER, OrgRole.ADMIN, True),
        (OrgRole.ADMIN, OrgRole.DEVELOPER, OrgRole.ADMIN, True),
        (OrgRole.ADMIN, OrgRole.DEVELOPER, OrgRole.OWNER, False),
        (OrgRole.ADMIN, OrgRole.OWNER, OrgRole.VIEWER, False),
        (OrgRole.DEVELOPER, OrgRole.VIEWER, OrgRole.VIEWER, False),
        (OrgRole.VIEWER, OrgRole.VIEWER, None, False),
    ],
)
def test_role_management_matrix(
    actor: OrgRole, current: OrgRole, new: OrgRole | None, allowed: bool
) -> None:
    assert can_manage(actor, current, new) is allowed


def test_slugify() -> None:
    assert slugify("Acme Corp!") == "acme-corp"
    assert slugify("!!!") == "org"


@pytest.mark.parametrize("login", ["-bad", "bad-", "a--b", "has space", "x" * 40])
def test_invalid_github_logins_rejected(login: str) -> None:
    with pytest.raises(ValueError):
        InvitationCreate(github_login=login)


def test_valid_github_login_accepted() -> None:
    assert InvitationCreate(github_login="octo-cat9").github_login == "octo-cat9"
