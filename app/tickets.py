"""Ticket taxonomy: queues, request types, and the routing/SLA metadata around them.

The UI builds its form from `field_catalog()` and the bot's tool schemas are
generated from the same lists, so the taxonomy lives here and nowhere else.

TWO THINGS ARE PLACEHOLDERS until Phase 0 agrees them — both are one edit each:
  * QUEUE_REQUEST_TYPES — which request types each queue accepts.
  * QUEUE_CALENDAR      — which nation's bank holidays each queue's clock uses.
"""
from . import sla

QUEUES = [
    "Finance", "Legal", "Sales Admin", "Deployment", "Support", "Training",
    "Product", "InfoSec", "Data Protection", "Onboarding", "Partner", "Marketing", "Exec",
]

REQUEST_TYPES = [
    "amendment", "quote/PO", "contract query", "DSAR/FOI", "security questionnaire",
    "upgrade", "training booking", "trial", "feature request",
]

# Dependent picklist: queue -> the request types it accepts. PLACEHOLDER — agree in Phase 0.
QUEUE_REQUEST_TYPES = {
    "Finance": ["quote/PO", "amendment", "contract query"],
    "Legal": ["contract query", "amendment", "DSAR/FOI"],
    "Sales Admin": ["quote/PO", "amendment", "upgrade", "trial"],
    "Deployment": ["upgrade", "amendment", "trial"],
    "Support": ["amendment", "upgrade", "feature request", "trial"],
    "Training": ["training booking"],
    "Product": ["feature request", "upgrade"],
    "InfoSec": ["security questionnaire"],
    "Data Protection": ["DSAR/FOI", "security questionnaire"],
    "Onboarding": ["training booking", "trial", "upgrade"],
    "Partner": ["quote/PO", "contract query", "trial"],
    "Marketing": ["feature request", "trial"],
    "Exec": ["contract query", "feature request"],
}

# Which published bank holiday list each queue's SLA clock follows. PLACEHOLDER.
QUEUE_CALENDAR = {queue: sla.DEFAULT_CALENDAR for queue in QUEUES}

WAITING_ON = ["Customer", "Internal team", "Third party"]

# Collected at creation time — routing and the SLA clock don't work without them.
REQUIRED_ON_CREATE = ["queue", "request_type", "raised_by", "customer_id"]


def calendar_for(queue: str) -> str:
    return QUEUE_CALENDAR.get(queue, sla.DEFAULT_CALENDAR)


def request_types_for(queue: str) -> list[str]:
    return QUEUE_REQUEST_TYPES.get(queue, REQUEST_TYPES)


def validate(values: dict, *, creating: bool) -> None:
    """Raise ValueError with a message the UI and the bot can both act on."""
    if creating:
        missing = [f for f in REQUIRED_ON_CREATE if not (values.get(f) or "").strip()]
        if missing:
            raise ValueError(f"These fields are required: {', '.join(missing)}")

    queue = (values.get("queue") or "").strip()
    if queue and queue not in QUEUES:
        raise ValueError(f"Unknown queue {queue!r}; must be one of {', '.join(QUEUES)}")

    request_type = (values.get("request_type") or "").strip()
    if request_type:
        if request_type not in REQUEST_TYPES:
            raise ValueError(f"Unknown request type {request_type!r}; "
                             f"must be one of {', '.join(REQUEST_TYPES)}")
        if queue and request_type not in request_types_for(queue):
            raise ValueError(f"{request_type!r} isn't a {queue} request type. "
                             f"{queue} accepts: {', '.join(request_types_for(queue))}")

    waiting_on = (values.get("waiting_on") or "").strip()
    if waiting_on and waiting_on not in WAITING_ON:
        raise ValueError(f"waiting_on must be blank or one of {', '.join(WAITING_ON)}")


def field_catalog() -> dict:
    """Everything the UI needs to render the ticket form and read a ticket's SLA state."""
    return {
        "queues": [
            {"name": q, "request_types": request_types_for(q),
             "calendar": calendar_for(q),
             "calendar_label": sla.CALENDARS[calendar_for(q)]["label"]}
            for q in QUEUES
        ],
        "request_types": REQUEST_TYPES,
        "waiting_on": WAITING_ON,
        "required_on_create": REQUIRED_ON_CREATE,
        "targets": {p: {"response_hours": t[0], "resolution_hours": t[1]}
                    for p, t in sla.TARGETS.items()},
        "business_hours": {"start": sla.BUSINESS_START.strftime("%H:%M"),
                           "end": sla.BUSINESS_END.strftime("%H:%M"),
                           "timezone": "Europe/London"},
        "calendars": sla.calendar_status(),
    }
