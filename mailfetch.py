"""Strictly read-only IMAP access.

The mailbox is opened with EXAMINE (readonly=True) and bodies are fetched with
BODY.PEEK, so nothing is deleted, moved, flagged or marked as read.
"""
import email
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
UID_RE = re.compile(rb"UID (\d+)")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


@dataclass
class Mail:
    key: str
    from_name: str
    from_addr: str
    reply_to: str
    subject: str
    date: datetime | None
    text: str
    links: list = field(default_factory=list)        # (href, visible text)
    attachments: list = field(default_factory=list)  # file names
    auth_results: str = ""


def decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


class _HTMLText(HTMLParser):
    """Minimal HTML -> text converter that also collects links."""

    BLOCK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [], []
        self._href, self._linktext, self._skip = None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._linktext = []
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._linktext).split())))
            self._href = None
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._linktext.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = re.sub(r"[ \t\r\f\v\xa0]+", " ", t)
        t = re.sub(r"\n[ ]*", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("latin-1", errors="replace")


def parse_message(raw: bytes, key: str) -> Mail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(msg.get("From", ""))
    _, reply_to = parseaddr(msg.get("Reply-To", ""))

    plain, html_parts, attachments = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename or part.get_content_disposition() == "attachment":
            attachments.append(decode(filename) or "(unbenannt)")
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html_parts.append(_part_text(part))

    html_text, links = "", []
    if html_parts:
        parser = _HTMLText()
        for h in html_parts:
            parser.feed(h)
        html_text, links = parser.text(), parser.links

    text = "\n".join(plain).strip() or html_text
    if not links:
        links = [(u, u) for u in URL_RE.findall(text)]

    dt = None
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()  # local time
    except Exception:
        pass

    return Mail(
        key=key,
        from_name=decode(name),
        from_addr=(addr or "").lower(),
        reply_to=(reply_to or "").lower(),
        subject=decode(msg.get("Subject", "")) or "(kein Betreff)",
        date=dt,
        text=text,
        links=links,
        attachments=attachments,
        auth_results=" ".join(decode(h) for h in (msg.get_all("Authentication-Results") or [])),
    )


def _imap_date(d: date) -> str:
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"  # locale independent


def fetch_mails(server, port, user, password, folder, days, max_count, known_keys, progress=None):
    """Return (keys in folder newest first, list of Mail objects not yet in known_keys)."""
    conn = imaplib.IMAP4_SSL(server, int(port), ssl_context=ssl.create_default_context(), timeout=30)
    try:
        conn.login(user, password)
        mailbox = f'"{folder}"' if " " in folder else folder
        typ, _ = conn.select(mailbox, readonly=True)  # EXAMINE: read-only
        if typ != "OK":
            raise RuntimeError(f"Ordner '{folder}' nicht gefunden")

        since = _imap_date(date.today() - timedelta(days=int(days)))
        typ, data = conn.uid("search", None, "SINCE", since)
        uids = data[0].split() if data and data[0] else []
        uids = uids[-int(max_count):]
        if not uids:
            return [], []

        # Message-IDs in one round trip to identify already analysed mails
        typ, data = conn.uid("fetch", b",".join(uids).decode(), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        uid_to_key = {}
        for item in data or []:
            if isinstance(item, tuple):
                m = UID_RE.search(item[0])
                if m:
                    mid = (email.message_from_bytes(item[1]).get("Message-ID") or "").strip()
                    uid_to_key[m.group(1)] = mid
        def key_for(uid: bytes) -> str:
            return uid_to_key.get(uid) or f"uid:{folder}:{uid.decode()}"

        newest_first = list(reversed(uids))
        ordered = [key_for(u) for u in newest_first]
        missing = [u for u in newest_first if key_for(u) not in known_keys]

        new_mails = []
        for i, uid in enumerate(missing):
            if progress:
                progress(i, len(missing))
            typ, data = conn.uid("fetch", uid.decode(), "(BODY.PEEK[])")
            raw = next((it[1] for it in data or [] if isinstance(it, tuple)), None)
            if raw:
                new_mails.append(parse_message(raw, key_for(uid)))
        return ordered, new_mails
    finally:
        try:
            conn.logout()
        except Exception:
            pass
