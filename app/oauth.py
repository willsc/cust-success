"""Signing in to Microsoft 365 and HubSpot as yourself, instead of pasting secrets.

The older path is still there and still works: an Entra ID app registration with
application permissions, or a HubSpot private-app token, typed into the source's
Advanced fields. What this module adds is the path most people should take -
click Connect, sign in on Microsoft's or HubSpot's own page, done. Nobody handles
a client secret, and the app never sees anyone's password.

Two different flows, because the two vendors support different things:

  Microsoft   device code. The user is shown a short code to enter at
              microsoft.com/devicelogin. No redirect URI, so it behaves the same
              whether this app is reached on localhost, a LAN address or through
              the Windows service - which the redirect flow cannot claim.

  HubSpot     authorization code, because HubSpot offers nothing else. That does
              need a redirect URL registered on the HubSpot app, and it is shown
              in the UI ready to copy.

Tokens live in the data source's own config, next to the credentials the Sources
tab already stores, and are refreshed here as they expire. `_source_id` rides
along in the resolved config so a refresh knows which row to write back to; it is
stripped before saving, and never reaches the browser.
"""
import json
import secrets
import time
from urllib.parse import urlencode

import httpx

from . import db, settings

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
HUBSPOT_AUTH = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN = "https://api.hubapi.com/oauth/v1/token"

# Delegated scopes: the signed-in user's own mail and calendar, plus the shared
# mailboxes they have been given access to. offline_access is what makes a
# refresh token come back, so nobody has to sign in again tomorrow.
MS_SCOPES = [
    "offline_access", "openid", "profile", "User.Read",
    "Mail.Read", "Mail.Send", "Mail.Read.Shared", "Mail.Send.Shared",
    "Calendars.Read", "Calendars.Read.Shared",
]

HUBSPOT_SCOPES = [
    "oauth",
    "crm.objects.contacts.read", "crm.objects.companies.read",
    "crm.objects.deals.read", "tickets",
]

# state -> (source_id, created) for the HubSpot round trip. In memory on purpose:
# a restart mid-authorisation should invalidate it rather than leave it usable.
_pending_states: dict[str, tuple[int, float]] = {}
STATE_TTL = 900


class OAuthError(RuntimeError):
    """Something the user needs to read, not a stack trace."""


# ---------- storage ----------

def source_id_of(source_config: dict | None) -> int | None:
    value = (source_config or {}).get("_source_id")
    return int(value) if value is not None else None


def tokens_of(source_config: dict | None) -> dict:
    return (source_config or {}).get("oauth") or {}


def connected(source_config: dict | None) -> bool:
    return bool(tokens_of(source_config).get("refresh_token")
                or tokens_of(source_config).get("access_token"))


def account_of(source_config: dict | None) -> str:
    return tokens_of(source_config).get("account", "")


def _store(source_id: int | None, tokens: dict) -> None:
    """Persist tokens onto the data source row, leaving its other config alone."""
    if source_id is None:
        return
    row = db.get_datasource(source_id)
    if not row:
        return
    try:
        config = json.loads(row.get("config_json") or "{}")
    except json.JSONDecodeError:
        config = {}
    config.pop("_source_id", None)      # never persist the marker we ride in on
    config["oauth"] = tokens
    db.update_datasource(source_id, config_json=json.dumps(config))


def disconnect(source_id: int) -> dict:
    """Forget the tokens. The remote grant is not revoked - that is the user's to do."""
    row = db.get_datasource(source_id)
    if not row:
        raise OAuthError(f"Data source {source_id} not found")
    try:
        config = json.loads(row.get("config_json") or "{}")
    except json.JSONDecodeError:
        config = {}
    had = bool(config.pop("oauth", None))
    config.pop("_source_id", None)
    db.update_datasource(source_id, config_json=json.dumps(config))
    return {"ok": True, "was_connected": had}


# ---------- Microsoft: device code ----------

def ms_client_id(source_config: dict | None = None) -> str:
    return ((source_config or {}).get("client_id") or settings.value("MS_CLIENT_ID") or "").strip()


def ms_tenant(source_config: dict | None = None) -> str:
    # "organizations" lets any work or school account sign in, which is the right
    # default when nobody has said which tenant this is.
    return (((source_config or {}).get("tenant_id") or settings.value("MS_TENANT_ID")
             or "organizations").strip())


def ms_device_start(source_config: dict) -> dict:
    client_id = ms_client_id(source_config)
    if not client_id:
        raise OAuthError(
            "This source has no Microsoft client ID yet. An administrator registers an "
            "app once in Entra ID (public client, delegated permissions) and pastes the "
            "Application (client) ID here - no secret needed.")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{LOGIN}/{ms_tenant(source_config)}/oauth2/v2.0/devicecode",
            data={"client_id": client_id, "scope": " ".join(MS_SCOPES)},
        )
    if resp.status_code >= 400:
        raise OAuthError(_ms_error(resp))
    data = resp.json()
    return {
        "mode": "device",
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri") or "https://microsoft.com/devicelogin",
        "expires_in": int(data.get("expires_in", 900)),
        "interval": int(data.get("interval", 5)),
        "message": data.get("message", ""),
    }


def ms_device_poll(source_config: dict, device_code: str) -> dict:
    """One poll. Returns pending until the user finishes signing in."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{LOGIN}/{ms_tenant(source_config)}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": ms_client_id(source_config),
                "device_code": device_code,
            },
        )
    payload = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        code = payload.get("error", "")
        if code == "authorization_pending":
            return {"status": "pending"}
        if code == "slow_down":
            return {"status": "pending", "slow_down": True}
        if code == "expired_token":
            raise OAuthError("The sign-in code expired before it was used. Start again.")
        if code == "authorization_declined":
            raise OAuthError("Sign-in was declined.")
        raise OAuthError(_ms_error(resp))

    tokens = _ms_store_tokens(source_config, payload)
    return {"status": "connected", "account": tokens.get("account", "")}


def _ms_store_tokens(source_config: dict, payload: dict) -> dict:
    tokens = {
        "provider": "microsoft",
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        "scope": payload.get("scope", ""),
        "account": _ms_account(payload["access_token"]),
        "connected_at": time.time(),
    }
    _store(source_id_of(source_config), tokens)
    return tokens


def _ms_account(access_token: str) -> str:
    """Who signed in - shown in the UI so it is obvious whose mailbox this is."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{GRAPH}/me",
                              headers={"Authorization": f"Bearer {access_token}"},
                              params={"$select": "mail,userPrincipalName,displayName"})
        resp.raise_for_status()
        me = resp.json()
        return (me.get("mail") or me.get("userPrincipalName") or "").strip()
    except Exception:
        return ""


def ms_access_token(source_config: dict) -> str:
    """A valid delegated token, refreshed if it has aged out."""
    tokens = tokens_of(source_config)
    if not tokens:
        raise OAuthError("Not signed in to Microsoft 365 for this source.")
    if time.time() < float(tokens.get("expires_at", 0)) - 60:
        return tokens["access_token"]

    refresh = tokens.get("refresh_token")
    if not refresh:
        raise OAuthError("The Microsoft sign-in has expired. Open the Sources tab and connect again.")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{LOGIN}/{ms_tenant(source_config)}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": ms_client_id(source_config),
                "refresh_token": refresh,
                "scope": " ".join(MS_SCOPES),
            },
        )
    if resp.status_code >= 400:
        raise OAuthError(
            "The Microsoft sign-in could not be renewed - it was probably revoked or expired. "
            "Open the Sources tab and connect again.")
    payload = resp.json()
    # A refresh response may or may not carry a new refresh token; keep the old one if not.
    payload.setdefault("refresh_token", refresh)
    updated = {**tokens, **{
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
    }}
    _store(source_id_of(source_config), updated)
    # Keep the caller's copy current too, so one request doesn't refresh twice.
    if isinstance(source_config, dict):
        source_config["oauth"] = updated
    return updated["access_token"]


def _ms_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        detail = payload.get("error_description") or payload.get("error") or resp.text[:300]
    except Exception:
        detail = resp.text[:300]
    detail = " ".join(str(detail).split())
    if "AADSTS7000218" in detail or "client_assertion" in detail:
        detail += (" - the app registration must be marked as a public client "
                   "(Authentication -> Allow public client flows -> Yes).")
    return f"Microsoft rejected the sign-in: {detail}"


# ---------- HubSpot: authorization code ----------

def hubspot_client(source_config: dict | None = None) -> tuple[str, str]:
    cfg = source_config or {}
    return ((cfg.get("client_id") or settings.value("HUBSPOT_CLIENT_ID") or "").strip(),
            (cfg.get("client_secret") or settings.value("HUBSPOT_CLIENT_SECRET") or "").strip())


def hubspot_start(source_config: dict, redirect_uri: str) -> dict:
    client_id, _ = hubspot_client(source_config)
    if not client_id:
        raise OAuthError(
            "This source has no HubSpot client ID yet. Create an app once in HubSpot "
            "(Settings -> Integrations -> Private Apps -> ... or a public app for OAuth), "
            "add the redirect URL shown here, and paste its client ID and secret.")

    _sweep_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = (source_id_of(source_config), time.time())
    url = HUBSPOT_AUTH + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(HUBSPOT_SCOPES),
        "state": state,
    })
    return {"mode": "redirect", "url": url, "state": state}


def _sweep_states() -> None:
    cutoff = time.time() - STATE_TTL
    for key in [k for k, (_, born) in _pending_states.items() if born < cutoff]:
        _pending_states.pop(key, None)


def hubspot_complete(state: str, code: str, redirect_uri: str) -> dict:
    _sweep_states()
    entry = _pending_states.pop(state, None)
    if not entry:
        raise OAuthError("That sign-in link has expired or was already used. Start again from the Sources tab.")
    source_id, _ = entry

    row = db.get_datasource(source_id) if source_id else None
    config = json.loads(row.get("config_json") or "{}") if row else {}
    client_id, client_secret = hubspot_client(config)

    with httpx.Client(timeout=30) as client:
        resp = client.post(HUBSPOT_TOKEN, data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        })
    if resp.status_code >= 400:
        raise OAuthError(f"HubSpot rejected the sign-in: {' '.join(resp.text[:300].split())}")
    payload = resp.json()
    tokens = {
        "provider": "hubspot",
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": time.time() + int(payload.get("expires_in", 1800)),
        "account": _hubspot_account(payload["access_token"]),
        "connected_at": time.time(),
    }
    _store(source_id, tokens)
    return {"ok": True, "source_id": source_id, "account": tokens["account"]}


def _hubspot_account(access_token: str) -> str:
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"https://api.hubapi.com/oauth/v1/access-tokens/{access_token}")
        resp.raise_for_status()
        info = resp.json()
        hub = info.get("hub_domain") or info.get("hub_id") or ""
        user = info.get("user") or ""
        return f"{user} ({hub})".strip() if user else str(hub)
    except Exception:
        return ""


def hubspot_access_token(source_config: dict) -> str:
    tokens = tokens_of(source_config)
    if not tokens:
        raise OAuthError("Not signed in to HubSpot for this source.")
    if time.time() < float(tokens.get("expires_at", 0)) - 60:
        return tokens["access_token"]

    refresh = tokens.get("refresh_token")
    if not refresh:
        raise OAuthError("The HubSpot sign-in has expired. Open the Sources tab and connect again.")
    client_id, client_secret = hubspot_client(source_config)
    with httpx.Client(timeout=30) as client:
        resp = client.post(HUBSPOT_TOKEN, data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
        })
    if resp.status_code >= 400:
        raise OAuthError("The HubSpot sign-in could not be renewed. Connect again on the Sources tab.")
    payload = resp.json()
    updated = {**tokens, **{
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh),
        "expires_at": time.time() + int(payload.get("expires_in", 1800)),
    }}
    _store(source_id_of(source_config), updated)
    if isinstance(source_config, dict):
        source_config["oauth"] = updated
    return updated["access_token"]
