"""Generic REST API data source — lets the team plug in any JSON HTTP API
(Zendesk, Jira, Stripe, an internal service, ...) without new code.

Requests are read-only by default; enable write methods per source if needed.
"""
import json
from urllib.parse import urljoin, urlparse

import httpx

MAX_CHARS = 20000
READ_METHODS = {"GET", "HEAD"}


def _auth_headers(config: dict) -> dict:
    auth_type = (config.get("auth_type") or "none").lower()
    headers = {"Accept": "application/json"}
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {config.get('token', '')}"
    elif auth_type == "header":
        name = config.get("header_name") or "X-API-Key"
        headers[name] = config.get("token", "")
    for line in (config.get("extra_headers") or "").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()
    return headers


def _auth_tuple(config: dict):
    if (config.get("auth_type") or "").lower() == "basic":
        return (config.get("username", ""), config.get("password", ""))
    return None


def _url(config: dict, path: str) -> str:
    base = (config.get("base_url") or "").strip()
    if not base:
        raise ValueError("This API data source has no base URL configured.")
    if not base.endswith("/"):
        base += "/"
    target = urljoin(base, path.lstrip("/"))
    if urlparse(target).netloc != urlparse(base).netloc:
        raise ValueError("Request path must stay on the data source's own host.")
    return target


def test_connection(config: dict) -> dict:
    path = config.get("test_path") or ""
    try:
        result = call(config, path or "/", method="GET")
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": result["status"] < 400,
            "message": f"HTTP {result['status']} from {result['url']}"}


def call(config: dict, path: str, method: str = "GET", params: dict | None = None,
         body: dict | None = None) -> dict:
    method = (method or "GET").upper()
    allow_writes = bool(config.get("allow_writes"))
    if method not in READ_METHODS and not allow_writes:
        raise ValueError(
            f"{method} is not permitted on this data source (it is configured read-only). "
            "Enable 'Allow write methods' on the data source to change that."
        )
    url = _url(config, path)
    timeout = float(config.get("timeout") or 30)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.request(
            method, url,
            headers=_auth_headers(config),
            auth=_auth_tuple(config),
            params=params or None,
            json=body or None,
        )
    text = resp.text
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    try:
        payload = json.loads(text) if not truncated else text
    except json.JSONDecodeError:
        payload = text
    return {"url": str(resp.url), "status": resp.status_code, "truncated": truncated, "data": payload}
