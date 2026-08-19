"""MCP server: Outlook mail and calendar across the team's Microsoft 365 mailboxes.

Run it directly (stdio transport):

    python mcp_servers/ms365_server.py [--source-id N] [--allow-writes]

The path may be absolute; the server does not care what directory it starts in.

Reading is always available. Sending — replying to a customer, sending a new
message — only registers with --allow-writes, so a client that just wants to
read a mailbox cannot email anyone by accident.

Credentials come from the "Microsoft 365 Mail" data source in data/app.db,
falling back to the shared MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET /
MS_MAILBOX / MS_MAILBOXES settings, and finally to a demo tenant so every tool
answers something before any credential exists.
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

from app import ms365  # noqa: E402  — after sdk(), which puts the repo on sys.path

ARGS = _common.parse_args(__doc__.splitlines()[0], "CSHUB_MS365_SOURCE_ID",
                          writes="send mail (reply_to_mail, send_mail)")

INSTRUCTIONS = """Outlook mail and calendar for a customer-success team's shared mailboxes.

One Microsoft 365 tenant can hold several team mailboxes (support@, renewals@,
escalations@). Every tool takes an optional `mailbox` argument; leave it out to use
the team's default mailbox, and call list_mailboxes first if a request names a
different one or you need to look across several.

Message ids belong to the mailbox they were read from — always pass the same
mailbox to read_mail that you passed to search_mail.
"""

server = MCPServer(
    name="ms365",
    title="Microsoft 365 — Outlook mail & calendar",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)


def _config() -> dict:
    return _common.config_for("ms365_mail", ARGS.source_id)


@server.tool()
@_common.guard
def list_mailboxes() -> dict:
    """List the Microsoft 365 mailboxes this server can read.

    Call this before reading mail from anything other than the default mailbox.
    `restricted: true` means the team pinned an explicit allowlist and any other
    address will be refused; `restricted: false` means any mailbox the app
    registration can reach may be passed by address.
    """
    return ms365.list_mailboxes(_config())


@server.tool()
@_common.guard
def list_folders(mailbox: str = "") -> dict:
    """List the mail folders in a mailbox, with item and unread counts.

    Use it to find a folder id to scope search_mail to, beyond the well-known
    names ('inbox', 'sentitems', 'archive', 'drafts').
    """
    return ms365.list_folders(mailbox=mailbox, source_config=_config())


@server.tool()
@_common.guard
def search_mail(query: str = "", mailbox: str = "", folder: str = "inbox",
                limit: int = 10, unread_only: bool = False) -> dict:
    """Search or list messages in one mailbox.

    Returns id, subject, sender, recipients, date, read state and a preview for
    each message — use read_mail for the full body.

    Args:
        query: Free-text search over the message. Omit to list the most recent messages.
        mailbox: Mailbox address to read; omit for the team's default mailbox.
        folder: Well-known folder name or folder id. Pass 'all' to search the whole mailbox.
        limit: Maximum messages to return (1-50).
        unread_only: Only unread messages. Ignored when `query` is set, which Graph
            will not combine with a filter.
    """
    return ms365.list_messages(search=query, folder=folder, limit=limit,
                               source_config=_config(), mailbox=mailbox,
                               unread_only=unread_only)


@server.tool()
@_common.guard
def read_mail(message_id: str, mailbox: str = "") -> dict:
    """Read one message in full, including its body, cc list and conversation id.

    Args:
        message_id: Id from search_mail.
        mailbox: The mailbox the message was found in; omit for the default.
    """
    return ms365.read_message(message_id, source_config=_config(), mailbox=mailbox)


@server.tool()
@_common.guard
def list_calendar_events(mailbox: str = "", start: str = "", end: str = "",
                         days: int = 7, limit: int = 25) -> dict:
    """List calendar events for a mailbox over a time window, recurrences expanded.

    Args:
        mailbox: Mailbox whose calendar to read; omit for the default.
        start: ISO-8601 start of the window. Defaults to now.
        end: ISO-8601 end of the window. Defaults to `days` after the start.
        days: Window length in days when `end` is omitted.
        limit: Maximum events to return (1-100).
    """
    return ms365.list_events(mailbox=mailbox, start=start, end=end, days=days,
                             limit=limit, source_config=_config())


@server.tool()
@_common.guard
def check_availability(addresses: list[str], start: str = "", end: str = "",
                       days: int = 1, interval: int = 30) -> dict:
    """Free/busy for people or rooms — for finding a slot for a customer call.

    The addresses asked about are not limited to the team's own mailboxes.

    Args:
        addresses: Email addresses to check.
        start: ISO-8601 start of the window. Defaults to now.
        end: ISO-8601 end of the window. Defaults to `days` after the start.
        days: Window length in days when `end` is omitted.
        interval: Minutes per slot in the availability view (5-1440).
    """
    return ms365.free_busy(addresses, start=start, end=end, days=days,
                           interval=interval, source_config=_config())


def _register_write_tools() -> None:
    """Tools that send mail. Registered only under --allow-writes."""
    from mcp.types import ToolAnnotations

    sends = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                            openWorldHint=True)

    @server.tool(annotations=sends)
    @_common.guard
    def reply_to_mail(message_id: str, body: str, mailbox: str = "",
                      reply_all: bool = False) -> dict:
        """Send a reply to a message. This really emails the customer.

        Show the draft to the person you are working for and get their approval
        before calling this.

        Args:
            message_id: Id from search_mail.
            mailbox: The mailbox to reply from; must be the one the message is in.
            body: The reply text, exactly as approved.
            reply_all: Reply to every recipient rather than just the sender.
        """
        return ms365.reply_message(message_id, body, source_config=_config(),
                                   mailbox=mailbox, reply_all=reply_all)

    @server.tool(annotations=sends)
    @_common.guard
    def send_mail(to: list[str], subject: str, body: str, cc: list[str] | None = None,
                  mailbox: str = "", html: bool = False) -> dict:
        """Send a new message from a team mailbox. This really emails the recipients.

        Show the draft and get approval before calling this.

        Args:
            to: Recipient addresses.
            subject: Subject line.
            body: Message body, exactly as approved.
            cc: Addresses to copy.
            mailbox: Mailbox to send from; omit for the default.
            html: Treat `body` as HTML rather than plain text.
        """
        return ms365.send_message(to, subject, body, cc=cc, source_config=_config(),
                                  mailbox=mailbox, html=html)


if ARGS.allow_writes:
    _register_write_tools()


if __name__ == "__main__":
    _common.run(server)
