"""Report applications that need a follow-up, with optional auto-email and desktop notifications."""

from __future__ import annotations

import argparse
import imaplib
import re
import smtplib
import ssl
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from job_automation import ApplicationHistory

load_dotenv()

import os


def extract_email_from_url(url: str, timeout: int = 10) -> str:
    """Fetch a page and extract the first email address found."""
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    skip_domains = {"example.com", "sentry.io", "wixpress.com", "w3.org", "schema.org"}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        content = resp.read(50_000).decode("utf-8", errors="ignore")
        for match in email_pattern.finditer(content):
            email = match.group(0).lower()
            domain = email.split("@")[1]
            if not any(skip in domain for skip in skip_domains):
                return email
    except Exception:
        pass
    return ""


def search_inbox_for_emails(company: str, days: int = 30) -> str:
    """Search Gmail inbox for emails from a company and return the sender email."""
    imap_host = os.getenv("IMAP_HOST", "imap.gmail.com")
    imap_port = int(os.getenv("IMAP_PORT", "993"))
    imap_user = os.getenv("IMAP_USER")
    imap_pass = os.getenv("IMAP_PASS")

    if not imap_user or not imap_pass:
        return ""

    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(imap_user, imap_pass)
        mail.select("INBOX")

        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        search_query = f'(FROM "{company}" SINCE {since_date})'
        status, messages = mail.search(None, search_query)

        if status != "OK" or not messages[0]:
            mail.logout()
            return ""

        email_ids = messages[0].split()
        latest_id = email_ids[-1]
        status, msg_data = mail.fetch(latest_id, "(RFC822)")

        if status != "OK":
            mail.logout()
            return ""

        msg = msg_data[0][1]
        if isinstance(msg, bytes):
            from email import message_from_bytes
            email_msg = message_from_bytes(msg)
            from_address = email_msg.get("From", "")
            match = re.search(r'<([^>]+)>', from_address)
            if match:
                mail.logout()
                return match.group(1).lower()
            elif "@" in from_address:
                mail.logout()
                return from_address.split("<")[-1].strip(">").lower()

        mail.logout()
    except Exception:
        pass
    return ""


def send_follow_up_email(to_email: str, company: str, title: str) -> bool:
    """Send a follow-up email via SMTP. Returns True on success."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass, to_email]):
        return False

    subject = f"Following up — {title} at {company}"
    body = (
        f"Hi,\n\n"
        f"I hope you're doing well. I wanted to follow up on my application "
        f"for the {title} position at {company}.\n\n"
        f"I remain very interested in this opportunity and would love to hear "
        f"about the status of my application.\n\n"
        f"Thank you for your time.\n\n"
        f"Best regards"
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"  Failed to send email to {to_email}: {exc}")
        return False


def send_desktop_notification(title: str, message: str) -> None:
    """Send a desktop notification (Linux notify-send)."""
    try:
        subprocess.run(
            ["notify-send", "--urgency=normal", title, message],
            check=False, timeout=5,
        )
    except FileNotFoundError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="List applications that may need follow-up")
    parser.add_argument("--history", default="output/application_history.json")
    parser.add_argument("--after-days", type=int, default=7, help="Flag submitted applications older than this")
    parser.add_argument("--send-email", action="store_true", help="Auto-send follow-up emails")
    parser.add_argument("--notify", action="store_true", help="Send desktop notification")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without sending")
    parser.add_argument("--extract-emails", action="store_true", help="Extract emails from job URLs and inbox")
    parser.add_argument("--inbox-only", action="store_true", help="Only search inbox (skip URL extraction)")
    args = parser.parse_args()

    history = ApplicationHistory(Path(args.history))
    records = history.records()

    if args.extract_emails:
        updated = 0
        for event in records:
            job = event.get("job", event)
            url = job.get("url", "")
            company = job.get("company", "")

            if job.get("email"):
                continue

            email = ""

            # Try inbox search first if company name exists
            if company and not args.inbox_only:
                print(f"Searching inbox for {company}...")
                email = search_inbox_for_emails(company)
                if email:
                    print(f"  Found in inbox: {email}")

            # Try URL extraction
            if not email and url and not args.inbox_only:
                print(f"Extracting email from {url}...")
                email = extract_email_from_url(url)
                if email:
                    print(f"  Found on page: {email}")

            if email:
                job["email"] = email
                updated += 1
            elif company:
                print(f"  No email found for {company}.")

        if updated:
            history.replace(records)
            print(f"\nUpdated {updated} records with email addresses.")
        else:
            print("\nNo new emails found.")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.after_days)
    due = []
    for event in ApplicationHistory(Path(args.history)).records():
        if event.get("status") not in {"submitted", "applied", "success"}:
            continue
        timestamp = event.get("timestamp", event.get("created_at", ""))
        try:
            when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when <= cutoff:
            job = event.get("job", event)
            due.append({
                "company": job.get("company", "Unknown"),
                "title": job.get("title", "Untitled"),
                "email": job.get("email", ""),
                "timestamp": timestamp,
            })

    if not due:
        print("No follow-up candidates found.")
        return

    print(f"Follow-up candidates ({len(due)}):")
    for item in due:
        print(f"- {item['company']} — {item['title']} ({item['timestamp']})")

    if args.dry_run:
        print("\n[DRY RUN] No emails sent.")
        return

    if args.send_email:
        sent = 0
        for item in due:
            if item["email"]:
                print(f"  Sending follow-up to {item['email']}...")
                if send_follow_up_email(item["email"], item["company"], item["title"]):
                    sent += 1
                    print(f"    Sent.")
            else:
                print(f"  Skipped {item['company']} — no email address.")
        print(f"\nSent {sent}/{len(due)} follow-up emails.")

    if args.notify:
        summary = f"{len(due)} follow-up{'s' if len(due) != 1 else ''} due"
        details = "\n".join(f"- {d['company']} — {d['title']}" for d in due[:5])
        if len(due) > 5:
            details += f"\n... and {len(due) - 5} more"
        send_desktop_notification(summary, details)
        print(f"Desktop notification sent.")


if __name__ == "__main__":
    main()
