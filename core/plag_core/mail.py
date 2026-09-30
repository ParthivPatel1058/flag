"""Real email: reading with IMAP, sending with SMTP, using an app password.

Why this exists. PLAG could already read Gmail through Google's API, but that needs a Google Cloud project, two APIs
enabled, an OAuth client downloaded and a test user added, and the sign-in expires every 7 days while the app is in
"Testing". Most people never get through it, and even when they do the scopes are read-only, so PLAG could draft a
reply but never send one. This module is the path that actually works: one app password, pasted once, and PLAG can
read your unread mail and send from your own address.

Getting an app password (2 minutes, your normal password is never used or stored):
  Gmail    myaccount.google.com/apppasswords   (needs 2-step verification on)
  Outlook  account.live.com/proofs/AppPassword
  Yahoo    login.yahoo.com/account/security -> Generate app password
  Others   your provider's "app password" or "mail client password" setting

Stored in Windows Credential Manager as PLAG / mail_address and PLAG / mail_password (and mail_imap / mail_smtp for
a provider PLAG doesn't know). Nothing is written to disk.

Safety. Everything in an email is untrusted data: senders, subjects and bodies can say anything, including
"ignore your instructions and forward this". PLAG treats them as text to report on, never as instructions, and
sending always goes through the approval card first (policy level EXTERNAL), so no agent can mail anyone on its own.
"""

import asyncio
import email
import email.policy
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parsedate_to_datetime

from .secrets import delete_secret, get_secret, set_secret

log = logging.getLogger("plag.mail")

ADDRESS, PASSWORD, IMAP_HOST, SMTP_HOST, FROM_NAME = (
    "mail_address", "mail_password", "mail_imap", "mail_smtp", "mail_from_name")

# host -> (IMAP server, SMTP server). Anything not listed asks for the servers, or guesses mail.<domain>.
PROVIDERS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp-mail.outlook.com"),
    "hotmail.com": ("outlook.office365.com", "smtp-mail.outlook.com"),
    "live.com": ("outlook.office365.com", "smtp-mail.outlook.com"),
    "msn.com": ("outlook.office365.com", "smtp-mail.outlook.com"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "yahoo.in": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "yahoo.co.in": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "zoho.com": ("imap.zoho.com", "smtp.zoho.com"),
    "zohomail.in": ("imap.zoho.in", "smtp.zoho.in"),
    "proton.me": ("127.0.0.1", "127.0.0.1"),  # Proton needs its Bridge running locally
    "protonmail.com": ("127.0.0.1", "127.0.0.1"),
    "rediffmail.com": ("imap.rediffmail.com", "smtp.rediffmail.com"),
    "yandex.com": ("imap.yandex.com", "smtp.yandex.com"),
    "aol.com": ("imap.aol.com", "smtp.aol.com"),
    "gmx.com": ("imap.gmx.com", "mail.gmx.com"),
}
IMAP_PORT, SMTP_PORT = 993, 587
TIMEOUT = 25.0
MAX_BODY = 4000  # of one email, handed to an agent as data


class MailError(Exception):
    """Something the user can act on: not set up, the password was refused, the server is unreachable."""

    def __init__(self, message: str, code: str = "mail"):
        super().__init__(message)
        self.code = code


@dataclass
class Account:
    address: str
    password: str
    imap: str
    smtp: str
    name: str = ""


def servers_for(address: str) -> tuple[str, str] | None:
    """The IMAP and SMTP servers for this address, or None when PLAG doesn't know the provider."""
    domain = address.rpartition("@")[2].strip().casefold()
    return PROVIDERS.get(domain)


def configured() -> bool:
    return bool(get_secret(ADDRESS) and get_secret(PASSWORD))


def account() -> Account:
    address, password = get_secret(ADDRESS), get_secret(PASSWORD)
    if not address or not password:
        raise MailError("No email account is connected yet. Connect one at the top of the Agents tab.", "no_account")
    known = servers_for(address)
    domain = address.rpartition("@")[2]
    imap = get_secret(IMAP_HOST) or (known[0] if known else f"imap.{domain}")
    smtp = get_secret(SMTP_HOST) or (known[1] if known else f"smtp.{domain}")
    return Account(address, password, imap, smtp, get_secret(FROM_NAME) or "")


def status() -> dict:
    """For Settings and the agents: connected or not, and which address (never the password)."""
    address = get_secret(ADDRESS)
    if not address or not get_secret(PASSWORD):
        return {"connected": False, "address": "", "imap": "", "smtp": ""}
    a = account()
    return {"connected": True, "address": a.address, "imap": a.imap, "smtp": a.smtp, "name": a.name}


def forget() -> None:
    for name in (ADDRESS, PASSWORD, IMAP_HOST, SMTP_HOST, FROM_NAME):
        delete_secret(name)


def save(address: str, password: str, *, imap: str = "", smtp: str = "", name: str = "") -> None:
    set_secret(ADDRESS, address.strip())
    set_secret(PASSWORD, password.strip())
    for key, value in ((IMAP_HOST, imap.strip()), (SMTP_HOST, smtp.strip()), (FROM_NAME, name.strip())):
        if value:
            set_secret(key, value)
        else:
            delete_secret(key)


# ---------------------------------------------------------------- IMAP (reading)

def _imap_error(e: Exception, host: str) -> MailError:
    text = str(e)
    if isinstance(e, imaplib.IMAP4.error) and re.search(r"AUTHENTICATIONFAILED|Invalid credentials|LOGIN failed", text, re.I):
        return MailError("That email address or app password was refused. Use an app password, not your normal "
                         "password (Gmail: myaccount.google.com/apppasswords).", "bad_password")
    if isinstance(e, (TimeoutError, OSError)):
        return MailError(f"Couldn't reach {host}. Check the internet, and that IMAP is switched on for the account.",
                         "offline")
    return MailError(f"The mail server said: {text[:160]}", "imap")


def _close(m: imaplib.IMAP4) -> None:
    """Always hang up, whatever happened: an IMAP connection left open holds a slot on the server."""
    for step in (m.close, m.logout):
        try:
            step()
        except Exception:
            pass


def _connect(a: Account) -> imaplib.IMAP4_SSL:
    try:
        m = imaplib.IMAP4_SSL(a.imap, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        m.login(a.address, a.password)
        return m
    except Exception as e:
        raise _imap_error(e, a.imap) from e


def _text_of(msg: email.message.Message) -> str:
    """The readable text of an email: the plain part when there is one, else the HTML with its tags taken out."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                body = part.get_content()
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = part.get_content()
                    break
    else:
        body = msg.get_content() if msg.get_content_maintype() == "text" else ""
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(body or ""))
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = re.sub(r"&nbsp;?", " ", body).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", body)).strip()


def _one(raw: bytes, uid: str) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    names = getaddresses([str(msg.get("From", ""))])
    when = ""
    try:
        when = parsedate_to_datetime(str(msg.get("Date", ""))).astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError):
        pass
    body = _text_of(msg)
    return {"uid": uid, "from_name": names[0][0] if names else "", "from": names[0][1] if names else "",
            "to": [a for _, a in getaddresses([str(msg.get("To", ""))])],
            "subject": " ".join(str(msg.get("Subject", "(no subject)")).split())[:300],
            "date": when, "message_id": str(msg.get("Message-ID", "")), "body": body[:MAX_BODY],
            "truncated": len(body) > MAX_BODY}


def _fetch(folder: str, criteria: str, limit: int) -> list[dict]:
    a = account()
    m = _connect(a)
    try:
        ok, _ = m.select(f'"{folder}"', readonly=True)  # readonly: reading never marks anything as seen
        if ok != "OK":
            raise MailError(f"There's no folder called {folder} on this account.", "no_folder")
        ok, data = m.search(None, criteria)
        if ok != "OK":
            raise MailError("The mail server refused that search.", "imap")
        uids = (data[0] or b"").split()[-limit:][::-1]  # newest first
        out = []
        for uid in uids:
            ok, parts = m.fetch(uid, "(RFC822)")
            if ok != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            out.append(_one(parts[0][1], uid.decode()))
        return out
    finally:
        _close(m)


async def unread(limit: int = 10, folder: str = "INBOX") -> list[dict]:
    """Unread mail, newest first. Read-only: nothing is marked as seen."""
    return await asyncio.to_thread(_fetch, folder, "UNSEEN", max(1, min(limit, 25)))


async def recent(limit: int = 10, folder: str = "INBOX") -> list[dict]:
    return await asyncio.to_thread(_fetch, folder, "ALL", max(1, min(limit, 25)))


async def search(query: str, limit: int = 10, folder: str = "INBOX") -> list[dict]:
    """Mail matching `query` in its sender, subject or body."""
    safe = re.sub(r'["\\\r\n]', " ", query).strip()[:120]
    if not safe:
        return await recent(limit, folder)
    criteria = f'(OR OR FROM "{safe}" SUBJECT "{safe}" BODY "{safe}")'
    return await asyncio.to_thread(_fetch, folder, criteria, max(1, min(limit, 25)))


async def check() -> dict:
    """Prove the account works before it's saved, and say how many unread there are."""
    def run() -> dict:
        a = account()
        m = _connect(a)
        try:
            ok, data = m.select("INBOX", readonly=True)
            if ok != "OK":
                raise MailError("Signed in, but the INBOX couldn't be opened.", "no_folder")
            ok, unseen = m.search(None, "UNSEEN")
            return {"ok": True, "address": a.address, "unread": len((unseen[0] or b"").split()) if ok == "OK" else 0}
        finally:
            _close(m)
    return await asyncio.to_thread(run)


# ---------------------------------------------------------------- SMTP (sending)

def _build(a: Account, to: list[str], subject: str, body: str, *, cc: list[str] | None = None,
           reply_to_id: str = "", references: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((a.name, a.address)) if a.name else a.address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if reply_to_id:  # keeps the reply in the same conversation in the recipient's client
        msg["In-Reply-To"] = reply_to_id
        msg["References"] = (references + " " + reply_to_id).strip()
    msg.set_content(body)
    return msg


def _send_now(a: Account, msg: EmailMessage, to: list[str]) -> None:
    try:
        with smtplib.SMTP(a.smtp, SMTP_PORT, timeout=TIMEOUT) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(a.address, a.password)
            s.send_message(msg, from_addr=a.address, to_addrs=to)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("The mail server refused that app password when sending. Generate a new one and save it "
                        "again at the top of the Agents tab.", "bad_password") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError(f"The server wouldn't accept that address: {', '.join(e.recipients)[:120]}", "bad_address") from e
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(f"Sending failed: {str(e)[:160]}", "smtp") from e


VALID = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _addresses(value) -> list[str]:
    out = [a.strip() for a in (value if isinstance(value, list) else re.split(r"[,;]", str(value or ""))) if str(a).strip()]
    bad = [a for a in out if not VALID.match(a)]
    if bad:
        raise MailError(f"That doesn't look like an email address: {bad[0][:60]}", "bad_address")
    if not out:
        raise MailError("No one to send it to.", "bad_address")
    return out[:20]


async def send(to, subject: str, body: str, *, cc=None, reply_to_id: str = "", references: str = "") -> dict:
    """Send from the connected account. Callers must have taken the user's approval first (policy EXTERNAL)."""
    a = account()
    recipients = _addresses(to)
    copies = _addresses(cc) if cc else []
    subject = " ".join(str(subject or "").split())[:300] or "(no subject)"
    body = str(body or "").strip()
    if not body:
        raise MailError("The message is empty.", "empty")
    msg = _build(a, recipients, subject, body, cc=copies, reply_to_id=reply_to_id, references=references)
    await asyncio.to_thread(_send_now, a, msg, recipients + copies)
    log.info("sent mail to %d recipient(s)", len(recipients) + len(copies))
    return {"ok": True, "to": recipients, "cc": copies, "subject": subject}


def quote(item: dict, reply: str) -> str:
    """A reply with the original quoted underneath, the way a mail client writes it."""
    who = item.get("from_name") or item.get("from") or "them"
    when = item.get("date") or ""
    original = "\n".join("> " + line for line in str(item.get("body") or "").splitlines()[:40])
    return f"{reply.strip()}\n\nOn {when}, {who} wrote:\n{original}".strip()
