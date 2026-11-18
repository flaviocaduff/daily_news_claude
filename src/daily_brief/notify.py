"""Notification channels. Each one is active only when its environment variables are set."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

log = logging.getLogger(__name__)


def _ntfy(title: str, text: str, url: str) -> bool:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    server = os.getenv("NTFY_SERVER") or "https://ntfy.sh"
    # Priority high = "time-sensitive" on iOS, so the push gets through Focus modes; default priority was dropped.
    headers = {"Title": title.encode("utf-8"), "Tags": "newspaper", "Priority": os.getenv("NTFY_PRIORITY") or "high"}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open brief, {url}"
    httpx.post(f"{server.rstrip('/')}/{topic}", content=text.encode("utf-8"), headers=headers, timeout=15).raise_for_status()
    return True


def _telegram(title: str, text: str, url: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    body = f"{title}\n{text}" + (f"\n{url}" if url else "")
    httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": body}, timeout=15).raise_for_status()
    return True


def _email(title: str, text: str, url: str) -> bool:
    host, to = os.getenv("SMTP_HOST"), os.getenv("EMAIL_TO")
    if not (host and to):
        return False
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER", "")
    msg["To"] = to
    msg.set_content(f"{text}\n\n{url}")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


CHANNELS = {"ntfy": _ntfy, "telegram": _telegram, "email": _email}


def notify_all(title: str, text: str, url: str) -> list[str]:
    sent = []
    for name, fn in CHANNELS.items():
        try:
            if fn(title, text, url):
                sent.append(name)
        except Exception as e:
            log.error("notify via %s failed: %s", name, e)
    if not sent:
        log.warning("no notification channel configured (set NTFY_TOPIC, TELEGRAM_* or SMTP_*/EMAIL_TO)")
    return sent
