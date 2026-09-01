"""Demo identity plus optional OIDC authorization-code/PKCE userinfo integration."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services.access import ROLE_PERMISSIONS


def auth_mode() -> str:
    return os.getenv("WORKFLOW_AUTH_MODE", "demo").strip().lower() or "demo"


def oidc_enabled() -> bool:
    return auth_mode() == "oidc"


def oidc_cookie_secure() -> bool:
    if not oidc_enabled():
        return False
    redirect = oidc_settings()["redirect_uri"]
    return urlsplit(redirect).scheme.lower() == "https"


def oidc_settings() -> dict[str, str]:
    return {
        "issuer": os.getenv("WORKFLOW_OIDC_ISSUER", "").rstrip("/"),
        "client_id": os.getenv("WORKFLOW_OIDC_CLIENT_ID", ""),
        "client_secret": os.getenv("WORKFLOW_OIDC_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("WORKFLOW_OIDC_REDIRECT_URI", "http://127.0.0.1:8010/auth/callback"),
        "default_role": os.getenv("WORKFLOW_OIDC_DEFAULT_ROLE", "Case Manager"),
    }


def discovery() -> dict[str, Any]:
    cfg = oidc_settings()
    if not cfg["issuer"] or not cfg["client_id"]:
        raise RuntimeError("OIDC mode requires WORKFLOW_OIDC_ISSUER and WORKFLOW_OIDC_CLIENT_ID.")
    if not cfg["issuer"].startswith("https://"):
        raise RuntimeError("WORKFLOW_OIDC_ISSUER must use HTTPS.")
    url = cfg["issuer"] + "/.well-known/openid-configuration"
    response = httpx.get(url, timeout=10.0, follow_redirects=False)
    response.raise_for_status()
    value = response.json()
    discovered_issuer = str(value.get("issuer", "")).rstrip("/")
    if discovered_issuer and discovered_issuer != cfg["issuer"]:
        raise RuntimeError("OIDC discovery issuer does not match WORKFLOW_OIDC_ISSUER.")
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not str(value.get(key, "")).startswith("https://"):
            raise RuntimeError(f"OIDC discovery field {key} must use HTTPS.")
    return value


def begin_oidc_login(session: dict[str, Any]) -> str:
    cfg = oidc_settings()
    meta = discovery()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    session["oidc_state"] = state
    session["oidc_verifier"] = verifier
    return meta["authorization_endpoint"] + "?" + urlencode({"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "scope": "openid profile email", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _validate_role(role: str) -> str:
    role = role.strip()[:80]
    if role not in ROLE_PERMISSIONS:
        raise RuntimeError(f"OIDC role mapping resolved to unsupported role: {role or '<blank>'}.")
    return role


def _role_from_profile(profile: dict[str, Any], default_role: str) -> str:
    role = _validate_role(default_role)
    mapping_raw = os.getenv("WORKFLOW_OIDC_ROLE_MAP_JSON", "")
    if not mapping_raw:
        return role
    try:
        mapping = json.loads(mapping_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WORKFLOW_OIDC_ROLE_MAP_JSON is not valid JSON.") from exc
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise RuntimeError("WORKFLOW_OIDC_ROLE_MAP_JSON must be a JSON object mapping claim strings to supported roles.")
    claims = profile.get("groups") or profile.get("roles") or []
    if isinstance(claims, str):
        claims = [claims]
    if not isinstance(claims, list):
        claims = []
    for claim in claims:
        mapped = mapping.get(str(claim))
        if mapped is not None:
            return _validate_role(mapped)
    return role


def complete_oidc_login(session: dict[str, Any], db: Session, *, state: str, code: str) -> User:
    expected = session.pop("oidc_state", None)
    verifier = session.pop("oidc_verifier", None)
    if not expected or not secrets.compare_digest(str(expected), state) or not verifier:
        raise RuntimeError("OIDC login state validation failed.")
    cfg = oidc_settings()
    meta = discovery()
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg["redirect_uri"], "client_id": cfg["client_id"], "code_verifier": verifier}
    if cfg["client_secret"]:
        data["client_secret"] = cfg["client_secret"]
    token = httpx.post(meta["token_endpoint"], data=data, timeout=10.0, follow_redirects=False)
    token.raise_for_status()
    access_token = str(token.json().get("access_token", ""))
    if not access_token:
        raise RuntimeError("OIDC token response did not contain an access token.")
    userinfo = httpx.get(meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}, timeout=10.0, follow_redirects=False)
    userinfo.raise_for_status()
    profile = userinfo.json()
    email = str(profile.get("email", "")).strip().lower()
    subject = str(profile.get("sub", "")).strip()
    issuer = cfg["issuer"]
    if not subject:
        raise RuntimeError("OIDC userinfo did not provide a subject identifier.")
    if profile.get("email_verified") is False:
        raise RuntimeError("OIDC userinfo reports an unverified email address.")
    if "@" not in email:
        raise RuntimeError("OIDC userinfo did not provide a usable email address.")
    name = str(profile.get("name") or profile.get("preferred_username") or email).strip()[:120]
    role = _role_from_profile(profile, cfg["default_role"])

    # Stable provider identity is authoritative; email is an editable profile attribute.
    user = db.scalar(select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject))
    email_owner = db.scalar(select(User).where(User.email == email))
    if user is None:
        if email_owner is not None:
            if email_owner.oidc_subject and (email_owner.oidc_issuer != issuer or email_owner.oidc_subject != subject):
                raise RuntimeError("OIDC email is already bound to a different provider identity.")
            user = email_owner
            user.oidc_issuer = issuer
            user.oidc_subject = subject
        else:
            user = User(name=name, email=email, role=role, active=True, oidc_issuer=issuer, oidc_subject=subject)
            db.add(user)
            db.flush()
    else:
        if email_owner is not None and email_owner.id != user.id:
            raise RuntimeError("OIDC profile email is already used by another local user.")
        user.email = email
    user.name = name or user.name
    user.role = role
    user.active = True
    session["oidc_user_id"] = user.id
    return user
