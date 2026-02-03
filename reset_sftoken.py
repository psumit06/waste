import os
import re
import time
import sys
import csv
import requests
from playwright.sync_api import sync_playwright, TimeoutError

# ---------------- CONFIG ---------------- #

CSV_FILE = os.environ.get("SF_USER_CSV", "sf_users.csv")

SF_PASSWORD = os.environ["SF_PASSWORD"]
SF_SANDBOX_URL = os.environ["SF_SANDBOX_URL"]
SF_DOMAIN = os.environ["SF_DOMAIN"]

MAILSAC_API_KEY = os.environ["MAILSAC_API_KEY"]

MAILSAC_HEADERS = {
    "Mailsac-Key": MAILSAC_API_KEY,
    "Accept": "application/json",
}

POLL_INTERVAL = 5
OTP_TIMEOUT = 90
TOKEN_TIMEOUT = 120


# ---------------- HELPERS ---------------- #

def log(msg):
    print(f"[+] {msg}", flush=True)


def load_users_from_csv():
    with open(CSV_FILE, newline="") as f:
        reader = csv.DictReader(f)
        users = list(reader)

    if not users:
        raise RuntimeError("CSV contains no users")

    return users


def get_latest_message(email):
    r = requests.get(
        f"https://mailsac.com/api/addresses/{email}/messages",
        headers=MAILSAC_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    messages = r.json()
    return messages[0] if messages else None


def get_message_text(email, message_id):
    r = requests.get(
        f"https://mailsac.com/api/text/{email}/{message_id}",
        headers=MAILSAC_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.text


def wait_for_email(email, match_fn, timeout):
    start = time.time()

    while time.time() - start < timeout:
        msg = get_latest_message(email)

        if msg:
            body = get_message_text(email, msg["_id"])
            if match_fn(msg, body):
                return body

        time.sleep(POLL_INTERVAL)

    raise RuntimeError("Timed out waiting for email")


def fetch_otp(email):
    log(f"Waiting for OTP email in {email}...")

    body = wait_for_email(
        email,
        lambda msg, txt: (
            "salesforce" in msg["from"][0]["address"].lower()
            and re.search(r"\b\d{6}\b", txt)
        ),
        OTP_TIMEOUT,
    )

    otp = re.search(r"\b\d{6}\b", body).group(0)
    log(f"OTP received: {otp}")
    return otp


def fetch_security_token(email):
    log(f"Waiting for security token email in {email}...")

    body = wait_for_email(
        email,
        lambda msg, txt: (
            "token" in (msg.get("subject") or "").lower()
            or re.search(r"security token", txt, re.I)
        ),
        TOKEN_TIMEOUT,
    )

    match = re.search(r"token\s+is\s+([A-Za-z0-9]+)", body, re.I)
    if not match:
        raise RuntimeError("Token not found in email")

    token = match.group(1)
    log(f"New token: {token}")
    return token


# ---------------- MAIN ---------------- #

def rotate_user(user):
    sf_username = user["username"]
    mailsac_email = user["email"]

    log("=" * 60)
    log(f"Rotating token for: {sf_username}")
    log(f"Inbox: {mailsac_email}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto(SF_SANDBOX_URL)

        page.fill("#username", sf_username)
        page.fill("#password", SF_PASSWORD)
        page.click("#Login")

        log("Waiting for MFA screen...")

        try:
            page.wait_for_selector(
                'input[type="tel"], input[name="otp"]', timeout=30000
            )
        except TimeoutError:
            log("❌ MFA screen not detected — skipping user.")
            browser.close()
            return

        otp = fetch_otp(mailsac_email)

        page.fill('input[type="tel"], input[name="otp"]', otp)
        page.click('button:has-text("Verify"), input[value="Verify"]')

        page.wait_for_url(re.compile("lightning.force.com"), timeout=60000)

        reset_url = (
            f"https://{SF_DOMAIN}.lightning.force.com/"
            "settings/personal/ResetApiToken/home"
        )

        log("Navigating to reset token page...")
        page.goto(reset_url)

        page.wait_for_selector('button:has-text("Reset")', timeout=30000)
        page.click('button:has-text("Reset")')

        try:
            page.wait_for_selector('button:has-text("OK")', timeout=5000)
            page.click('button:has-text("OK")')
        except TimeoutError:
            pass

        token = fetch_security_token(mailsac_email)

        log(f"🎉 TOKEN ROTATED for {sf_username}")
        log(token)

        browser.close()


def main():
    users = load_users_from_csv()
    log(f"Loaded {len(users)} users from CSV.")

    for user in users:
        try:
            rotate_user(user)
        except Exception as e:
            log(f"❌ FAILED for {user['username']}: {e}")


if __name__ == "__main__":
    main()
