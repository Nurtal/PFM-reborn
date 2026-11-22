"""Turn a raw e-mail into what the helpdesk keeps: pure, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import cast

MAX_BODY = 100_000
TICKET_TAG = re.compile(r"\[#(\d{1,9})\]")


@dataclass(frozen=True)
class Incoming:
    sender: str
    subject: str
    body: str
    message_id: str
    ticket_number: int | None
    automatic: bool  # auto-replies, bounces, mailing lists: never answered


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        if tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return content.strip()[:MAX_BODY]


def parse(raw: bytes) -> Incoming:
    msg = message_from_bytes(raw[: MAX_BODY * 20], policy=policy.default)
    msg = cast(EmailMessage, msg)  # policy.default always builds EmailMessage
    subject = " ".join(str(msg.get("Subject", "")).split())[:300]
    tag = TICKET_TAG.search(subject)
    auto = (
        str(msg.get("Auto-Submitted", "no")).lower() != "no"
        or bool(msg.get("List-Id"))
        or str(msg.get("Precedence", "")).lower() in {"bulk", "junk", "list", "auto_reply"}
        or "mailer-daemon" in str(msg.get("From", "")).lower()
    )
    return Incoming(
        sender=parseaddr(str(msg.get("From", "")))[1].lower()[:320],
        subject=subject or "(no subject)",
        body=_body(msg),
        message_id=str(msg.get("Message-ID", ""))[:998],
        ticket_number=int(tag.group(1)) if tag else None,
        automatic=auto,
    )
