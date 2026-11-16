"""발송: Slack Incoming Webhook → (실패 시) 이메일 → (그것도 없으면) 콘솔 출력."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

log = logging.getLogger(__name__)


def send_slack(text: str, url: str) -> None:
    r = httpx.post(url, json={"text": text}, timeout=15)
    if r.status_code != 200 or r.text.strip() != "ok":
        raise RuntimeError(f"Slack 응답 {r.status_code}: {r.text[:200]}")


def send_email(subject: str, text: str) -> None:
    host, to = os.getenv("SMTP_HOST"), os.getenv("EMAIL_TO")
    if not (host and to):
        raise RuntimeError("SMTP_HOST / EMAIL_TO 미설정")
    user = os.getenv("SMTP_USER", "")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user or to, to
    msg.set_content(text)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        if user:
            s.login(user, os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)


def notify(text: str, cfg: dict, subject: str, dry_run: bool = False) -> dict:
    """사용한 채널과 오류 목록을 반환. channel: slack | email | console | dry_run"""
    if dry_run:
        print(text)
        return {"channel": "dry_run", "errors": []}

    errors = []
    ncfg = cfg.get("notify", {})
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if ncfg.get("slack", True) and url:
        try:
            send_slack(text, url)
            return {"channel": "slack", "errors": errors}
        except Exception as e:
            log.warning("Slack 발송 실패: %s", e)
            errors.append(f"slack: {e}")
    elif ncfg.get("slack", True):
        errors.append("slack: SLACK_WEBHOOK_URL 미설정")

    if ncfg.get("email_fallback", True):
        try:
            send_email(subject, text)
            return {"channel": "email", "errors": errors}
        except Exception as e:
            log.warning("이메일 발송 실패: %s", e)
            errors.append(f"email: {e}")

    print(text)
    return {"channel": "console", "errors": errors}
