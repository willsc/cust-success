"""MCP servers that expose this app's connectors to any MCP client.

Two stdio servers live here:

    mcp_servers.ms365_server    Outlook mail and calendar over Microsoft Graph
    mcp_servers.hubspot_server  HubSpot CRM

Both read their credentials from the same place the web app does — the data
source rows in data/app.db, falling back to the shared MS_*/HUBSPOT_* settings —
so configuring a source once on the Sources tab configures it for both.
"""
