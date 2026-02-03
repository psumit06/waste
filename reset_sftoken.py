import os
import re
import time
import sys
import requests
from playwright.sync_api import sync_playwright, TimeoutError

# ---------------- CONFIG ---------------- #

SF_USERNAME = os.environ["SF_USERNAME"]
SF_PASSWORD = os.environ["SF_PASSWORD"]
SF_SANDBOX_URL = os.environ["SF_SANDBOX_URL"]
SF_DOMAIN = os.environ["SF_DOMAIN"]

MAILSAC_API_KEY = os.environ["MAILSAC_API_KEY"]
MAILSAC_EMAIL = os.environ["MAILSAC_EMAIL"]

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


def get_latest_message():
    r = requests.get(
        f"https://mailsac.com/api/addresses/{MAILSAC_EMAIL}/messages",
        headers=MAILSAC_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    messages = r.json()
    return messages[0] if messages else None


def get_message_text(message_id):
    r = requests.get(
        f"https://mailsac.com/api/text/{MAILSAC_EMAIL}/{message_id}",
        headers=MAILSAC_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.text


def wait_for_email(match_fn, timeout):
    start = time.time()

    while time.time() - start < timeout:
        msg = get_latest_message()

        if msg:
            body = get_message_text(msg["_id"])
            if match_fn(msg, body):
                return body

        time.sleep(POLL_INTERVAL)

    raise RuntimeError("Timed out waiting for email")


def fetch_otp():
    log("Waiting for OTP email...")

    body = wait_for_email(
        lambda msg, txt: (
            "salesforce" in msg["from"][0]["address"].lower()
            and re.search(r"\b\d{6}\b", txt)
        ),
        OTP_TIMEOUT,
    )

    otp = re.search(r"\b\d{6}\b", body).group(0)
    log(f"OTP received: {otp}")
    return otp


def fetch_security_token():
    log("Waiting for security token email...")

    body = wait_for_email(
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

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        log("Opening Salesforce login page...")
        page.goto(SF_SANDBOX_URL)

        page.fill("#username", SF_USERNAME)
        page.fill("#password", SF_PASSWORD)
        page.click("#Login")

        log("Waiting for MFA screen...")
        try:
            page.wait_for_selector(
                'input[type="tel"], input[name="otp"]', timeout=30000
            )
        except TimeoutError:
            log("MFA screen not detected — exiting.")
            sys.exit(1)

        otp = fetch_otp()

        page.fill('input[type="tel"], input[name="otp"]', otp)
        page.click('button:has-text("Verify"), input[value="Verify"]')

        log("OTP submitted.")

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

        token = fetch_security_token()

        log("\nSUCCESS — TOKEN RESET COMPLETE")
        log(token)

        browser.close()


if __name__ == "__main__":
    main()
