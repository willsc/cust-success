"""MCP server: HubSpot CRM — contacts, companies, deals and tickets.

Run it directly (stdio transport):

    python mcp_servers/hubspot_server.py [--source-id N]

The path may be absolute; the server does not care what directory it starts in.

Read-only: every tool here reads from the CRM, none writes to it.

The private-app token comes from the "HubSpot CRM" data source in data/app.db,
falling back to the shared HUBSPOT_TOKEN setting, and finally to a demo portal so
the tools answer something before any token exists.
"""
import os
import sys

# An MCP client launches this file from wherever it happens to be, so put the
# repo root on sys.path before importing anything from it. That makes the direct
# script path work from any directory; `-m mcp_servers.<name>` additionally
# needs the repo root as the working directory (or on PYTHONPATH).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers import _common  # noqa: E402

MCPServer = _common.sdk()

from app import hubspot  # noqa: E402  — after sdk(), which puts the repo on sys.path

ARGS = _common.parse_args(__doc__.splitlines()[0], "CSHUB_HUBSPOT_SOURCE_ID")

INSTRUCTIONS = """HubSpot CRM for a customer-success team.

Four object types are exposed: contacts, companies, deals and tickets. Start with
search_records to find a record by name, email or domain, then use its id with
get_record for full properties, or with list_associated_records to walk the
relationships — the company behind a contact, a customer's open deals, the
support tickets on an account.

Which properties come back is configured per data source; call list_object_types
to see what this portal is set up to return.
"""

server = MCPServer(
    name="hubspot",
    title="HubSpot CRM",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)


def _config() -> dict:
    return _common.config_for("hubspot", ARGS.source_id)


def _read_only():
    from mcp.types import ToolAnnotations
    return ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=True)


READS = _read_only()


@server.tool(annotations=READS)
@_common.guard
def list_object_types() -> dict:
    """The CRM object types available here and the properties each one returns.

    Includes whether a live token is configured — without one every tool answers
    from a small demo portal, which is fine for trying the tools out but must not
    be reported to anyone as real customer data.
    """
    config = _config()
    return {
        "live": hubspot.configured(config),
        "object_types": {
            name: hubspot.properties_for(name, config) for name in hubspot.object_types()
        },
    }


@server.tool(annotations=READS)
@_common.guard
def search_records(object_type: str, query: str = "", limit: int = 20) -> dict:
    """Search or list CRM records.

    Args:
        object_type: One of contacts, companies, deals, tickets.
        query: Free-text search — a name, email address or domain. Omit to list
            the most recent records of that type.
        limit: Maximum records to return (1-100).
    """
    return hubspot.query(object_type, search=query, limit=limit, source_config=_config())


@server.tool(annotations=READS)
@_common.guard
def get_record(object_type: str, record_id: str) -> dict:
    """Read one CRM record by id, with every property this source is set up to return.

    Args:
        object_type: One of contacts, companies, deals, tickets.
        record_id: The record's HubSpot id, from search_records.
    """
    return hubspot.get(object_type, record_id, source_config=_config())


@server.tool(annotations=READS)
@_common.guard
def list_associated_records(object_type: str, record_id: str, to_object_type: str,
                            limit: int = 50) -> dict:
    """Records associated with one record, with their properties.

    Use it to go from a contact to their company, from a company to its open
    deals, or from an account to the support tickets raised against it.

    Args:
        object_type: The type of the record you are starting from.
        record_id: The starting record's HubSpot id.
        to_object_type: The type of related records to return.
        limit: Maximum related records to return (1-100).
    """
    return hubspot.associations(object_type, record_id, to_object_type,
                                limit=limit, source_config=_config())


if __name__ == "__main__":
    _common.run(server)
