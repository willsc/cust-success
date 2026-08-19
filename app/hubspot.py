"""HubSpot CRM access via a private-app token, with built-in demo data when unconfigured."""
import httpx

from . import oauth, settings

BASE = "https://api.hubapi.com"

DEFAULT_PROPERTIES = {
    "contacts": ["firstname", "lastname", "email", "company", "phone", "lifecyclestage"],
    "companies": ["name", "domain", "industry", "numberofemployees", "annualrevenue", "lifecyclestage"],
    "deals": ["dealname", "amount", "dealstage", "pipeline", "closedate", "hs_deal_stage_probability"],
    "tickets": ["subject", "content", "hs_pipeline_stage", "hs_ticket_priority", "createdate"],
}

DEMO_DATA = {
    "contacts": [
        {"id": "101", "firstname": "Maya", "lastname": "Chen", "email": "maya.chen@acmeretail.example",
         "company": "Acme Retail", "lifecyclestage": "customer"},
        {"id": "102", "firstname": "Tom", "lastname": "Okafor", "email": "t.okafor@brightlabs.example",
         "company": "Bright Labs", "lifecyclestage": "customer"},
        {"id": "103", "firstname": "Sara", "lastname": "Lindqvist", "email": "sara@nordwind.example",
         "company": "Nordwind AB", "lifecyclestage": "opportunity"},
    ],
    "companies": [
        {"id": "201", "name": "Acme Retail", "domain": "acmeretail.example", "industry": "Retail",
         "numberofemployees": "480", "annualrevenue": "12000000", "lifecyclestage": "customer"},
        {"id": "202", "name": "Bright Labs", "domain": "brightlabs.example", "industry": "Biotech",
         "numberofemployees": "95", "annualrevenue": "4500000", "lifecyclestage": "customer"},
        {"id": "203", "name": "Nordwind AB", "domain": "nordwind.example", "industry": "Logistics",
         "numberofemployees": "220", "annualrevenue": "8800000", "lifecyclestage": "opportunity"},
    ],
    "deals": [
        {"id": "301", "dealname": "Acme Retail - Enterprise renewal", "amount": "60000",
         "dealstage": "contractsent", "closedate": "2026-09-30"},
        {"id": "302", "dealname": "Bright Labs - Seat expansion", "amount": "18000",
         "dealstage": "qualifiedtobuy", "closedate": "2026-10-15"},
        {"id": "303", "dealname": "Nordwind AB - New business", "amount": "42000",
         "dealstage": "presentationscheduled", "closedate": "2026-11-01"},
    ],
    "tickets": [
        {"id": "401", "subject": "SSO login failing for EU users", "hs_ticket_priority": "HIGH",
         "content": "Acme Retail reports intermittent SSO failures since last release."},
        {"id": "402", "subject": "CSV export truncates rows", "hs_ticket_priority": "MEDIUM",
         "content": "Bright Labs sees exports capped at 10k rows."},
    ],
}

# contact/company/deal/ticket wiring for the demo portal, keyed
# "<object_type>:<id>" -> {related object type: [ids]}.
DEMO_ASSOCIATIONS = {
    "contacts:101": {"companies": ["201"], "deals": ["301"], "tickets": ["401"]},
    "contacts:102": {"companies": ["202"], "deals": ["302"], "tickets": ["402"]},
    "contacts:103": {"companies": ["203"], "deals": ["303"], "tickets": []},
    "companies:201": {"contacts": ["101"], "deals": ["301"], "tickets": ["401"]},
    "companies:202": {"contacts": ["102"], "deals": ["302"], "tickets": ["402"]},
    "companies:203": {"contacts": ["103"], "deals": ["303"], "tickets": []},
    "deals:301": {"contacts": ["101"], "companies": ["201"], "tickets": []},
    "deals:302": {"contacts": ["102"], "companies": ["202"], "tickets": []},
    "deals:303": {"contacts": ["103"], "companies": ["203"], "tickets": []},
    "tickets:401": {"contacts": ["101"], "companies": ["201"], "deals": []},
    "tickets:402": {"contacts": ["102"], "companies": ["202"], "deals": []},
}


def object_types() -> list[str]:
    return sorted(DEFAULT_PROPERTIES)


def _check_type(object_type: str) -> str:
    object_type = (object_type or "").lower().strip()
    if object_type not in DEFAULT_PROPERTIES:
        raise ValueError(f"object_type must be one of {sorted(DEFAULT_PROPERTIES)}")
    return object_type


def signed_in(source_config: dict | None = None) -> bool:
    """The user connected this source by signing in, rather than pasting a token."""
    return oauth.connected(source_config)


def _token(source_config: dict | None = None) -> str:
    """A bearer token: the signed-in user's if there is one, else a private-app token."""
    if oauth.connected(source_config):
        return oauth.hubspot_access_token(source_config)
    if source_config and source_config.get("token"):
        return source_config["token"]
    return settings.value("HUBSPOT_TOKEN")


def configured(source_config: dict | None = None) -> bool:
    if signed_in(source_config):
        return True
    if source_config and source_config.get("token"):
        return True
    return bool(settings.value("HUBSPOT_TOKEN"))


def test_connection(source_config: dict) -> dict:
    if not configured(source_config):
        return {"ok": False,
                "message": "Not connected yet — click Connect to sign in, or paste a "
                           "private-app token under Advanced. Until then the bot uses demo data."}
    try:
        result = query("contacts", limit=1, source_config=source_config)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    who = oauth.account_of(source_config)
    signed = f" as {who}" if who else ""
    return {"ok": True,
            "message": f"Connected{signed} to HubSpot ({len(result['results'])} record read back)."}


def properties_for(object_type: str, source_config: dict | None = None) -> list[str]:
    """Default properties, overridable per source (comma-separated in config)."""
    custom = (source_config or {}).get(f"{object_type}_properties") or ""
    extra = [p.strip() for p in custom.split(",") if p.strip()]
    return list(dict.fromkeys(DEFAULT_PROPERTIES[object_type] + extra))


def query(object_type: str, search: str = "", limit: int = 20,
          source_config: dict | None = None) -> dict:
    """Search or list CRM objects. object_type: contacts | companies | deals | tickets."""
    object_type = _check_type(object_type)
    limit = max(1, min(int(limit), 100))

    if not configured(source_config):
        items = DEMO_DATA[object_type]
        if search:
            s = search.lower()
            items = [i for i in items if s in " ".join(str(v) for v in i.values()).lower()]
        return {"source": "demo (no HubSpot token configured)", "results": items[:limit]}

    headers = {"Authorization": f"Bearer {_token(source_config)}"}
    props = properties_for(object_type, source_config)
    with httpx.Client(timeout=30) as client:
        if search:
            resp = client.post(
                f"{BASE}/crm/v3/objects/{object_type}/search",
                headers=headers,
                json={"query": search, "limit": limit, "properties": props},
            )
        else:
            resp = client.get(
                f"{BASE}/crm/v3/objects/{object_type}",
                headers=headers,
                params={"limit": limit, "properties": ",".join(props)},
            )
    resp.raise_for_status()
    data = resp.json()
    results = [{"id": r.get("id"), **(r.get("properties") or {})} for r in data.get("results", [])]
    return {"source": "hubspot", "results": results}


def get(object_type: str, record_id: str, source_config: dict | None = None) -> dict:
    """One CRM record by its id, with the properties this source is set up to read."""
    object_type = _check_type(object_type)
    record_id = str(record_id).strip()
    if not record_id:
        raise ValueError("record_id is required")

    if not configured(source_config):
        for item in DEMO_DATA[object_type]:
            if item["id"] == record_id:
                return {"source": "demo (no HubSpot token configured)",
                        "object_type": object_type, "record": item}
        raise ValueError(f"{object_type} record {record_id!r} not found in the demo portal")

    props = properties_for(object_type, source_config)
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE}/crm/v3/objects/{object_type}/{record_id}",
            headers={"Authorization": f"Bearer {_token(source_config)}"},
            params={"properties": ",".join(props)},
        )
    resp.raise_for_status()
    data = resp.json()
    return {"source": "hubspot", "object_type": object_type,
            "record": {"id": data.get("id"), **(data.get("properties") or {})}}


def associations(object_type: str, record_id: str, to_object_type: str,
                 limit: int = 50, source_config: dict | None = None) -> dict:
    """Records associated with one record — the company behind a contact, a
    customer's open deals, the tickets on an account."""
    object_type = _check_type(object_type)
    to_object_type = _check_type(to_object_type)
    record_id = str(record_id).strip()
    limit = max(1, min(int(limit), 100))

    if not configured(source_config):
        ids = DEMO_ASSOCIATIONS.get(f"{object_type}:{record_id}", {}).get(to_object_type, [])
        related = [i for i in DEMO_DATA[to_object_type] if i["id"] in ids][:limit]
        return {"source": "demo (no HubSpot token configured)", "from": object_type,
                "to": to_object_type, "results": related}

    headers = {"Authorization": f"Bearer {_token(source_config)}"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE}/crm/v4/objects/{object_type}/{record_id}/associations/{to_object_type}",
            headers=headers, params={"limit": limit},
        )
        resp.raise_for_status()
        ids = [r.get("toObjectId") for r in resp.json().get("results", []) if r.get("toObjectId")]
        if not ids:
            return {"source": "hubspot", "from": object_type, "to": to_object_type, "results": []}

        # One batch read rather than a request per id.
        resp = client.post(
            f"{BASE}/crm/v3/objects/{to_object_type}/batch/read",
            headers=headers,
            json={"properties": properties_for(to_object_type, source_config),
                  "inputs": [{"id": str(i)} for i in ids]},
        )
    resp.raise_for_status()
    results = [{"id": r.get("id"), **(r.get("properties") or {})}
               for r in resp.json().get("results", [])]
    return {"source": "hubspot", "from": object_type, "to": to_object_type, "results": results}
