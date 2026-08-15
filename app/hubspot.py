"""HubSpot CRM access via a private-app token, with built-in demo data when unconfigured."""
import httpx

from . import config

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


def _token(source_config: dict | None = None) -> str:
    """Per-source token, falling back to the HUBSPOT_TOKEN environment variable."""
    if source_config and source_config.get("token"):
        return source_config["token"]
    return config.HUBSPOT_TOKEN


def configured(source_config: dict | None = None) -> bool:
    return bool(_token(source_config))


def test_connection(source_config: dict) -> dict:
    if not configured(source_config):
        return {"ok": False, "message": "No access token set — the bot will use demo CRM data."}
    try:
        result = query("contacts", limit=1, source_config=source_config)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Connected to HubSpot ({len(result['results'])} record read back)."}


def properties_for(object_type: str, source_config: dict | None = None) -> list[str]:
    """Default properties, overridable per source (comma-separated in config)."""
    custom = (source_config or {}).get(f"{object_type}_properties") or ""
    extra = [p.strip() for p in custom.split(",") if p.strip()]
    return list(dict.fromkeys(DEFAULT_PROPERTIES[object_type] + extra))


def query(object_type: str, search: str = "", limit: int = 20,
          source_config: dict | None = None) -> dict:
    """Search or list CRM objects. object_type: contacts | companies | deals | tickets."""
    object_type = object_type.lower().strip()
    if object_type not in DEFAULT_PROPERTIES:
        raise ValueError(f"object_type must be one of {sorted(DEFAULT_PROPERTIES)}")
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
