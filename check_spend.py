import os
import json
import asyncio
from datetime import date

import resend
import pyotp
from monarchmoney import MonarchMoney, RequireMFAException


BOFA_THRESHOLD = 2500
CHASE_THRESHOLD = 1500

# Update this after checking your GitHub Action logs.
CHASE_FREEDOM_ACCOUNT_NAME = "Freedom Card"


def quarter_range():
    today = date.today()
    quarter_start_month = ((today.month - 1) // 3) * 3 + 1
    start = today.replace(month=quarter_start_month, day=1)
    quarter = f"Q{((today.month - 1) // 3) + 1}"
    period = f"{today.year}-{quarter}"
    return start.isoformat(), today.isoformat(), period


def tx_text(tx):
    return json.dumps(tx, default=str).lower()


def tx_amount(tx):
    return abs(float(tx.get("amount", 0) or 0))


def is_bofa_grocery(tx):
    text = tx_text(tx)

    return (
        ("bank of america" in text or "bofa" in text)
        and (
            "grocery" in text
            or "groceries" in text
            or "supermarket" in text
        )
    )


def is_chase_amazon_travel(tx):
    text = tx_text(tx)

    account_name = (
        str(tx.get("accountName", "")) + " " +
        str(tx.get("displayName", "")) + " " +
        str(tx.get("account", ""))
    ).lower()

    if CHASE_FREEDOM_ACCOUNT_NAME.lower() not in account_name:
        return False

    amazon_terms = [
        "amazon",
        "amzn",
        "amazon.com",
        "amazon marketplace",
        "amazon mktp",
        "amzn mktp",
        "amzn digital",
        "amazon digital",
        "amazon prime",
        "prime video",
        "audible",
        "kindle",
        "whole foods"
    ]

    chase_travel_terms = [
        "chase travel",
        "chase ultimate rewards",
        "ultimate rewards travel",
        "ultimate rewards",
        "cxloyalty",
        "cx loyalty",
        "travel rewards",
        "chase rewards travel",
        "jpmc travel",
        "jpmorgan travel"
    ]

    return any(term in text for term in amazon_terms + chase_travel_terms)


def send_email(subject, body):
    resend.api_key = os.environ["RESEND_API_KEY"]

    resend.Emails.send({
        "from": os.environ["ALERT_EMAIL_FROM"],
        "to": [os.environ["ALERT_EMAIL_TO"]],
        "subject": subject,
        "text": body,
    })


async def login_to_monarch(mm):
    email = os.environ["MONARCH_EMAIL"]
    password = os.environ["MONARCH_PASSWORD"]
    mfa_secret = os.environ.get("MONARCH_MFA_SECRET_KEY", "").replace(" ", "").strip()

    try:
        await mm.login(
            email=email,
            password=password,
            save_session=False,
            use_saved_session=False,
        )
    except RequireMFAException:
        if not mfa_secret:
            raise Exception("MONARCH_MFA_SECRET_KEY is missing")

        mfa_code = pyotp.TOTP(mfa_secret).now()

        await mm.multi_factor_authenticate(email, password, mfa_code)


async def main():
    start_date, end_date, period = quarter_range()

    mm = MonarchMoney()
    await login_to_monarch(mm)

    data = await mm.get_transactions(
        start_date=start_date,
        end_date=end_date,
        limit=500,
    )

    transactions = data.get("allTransactions", {}).get("results", [])

    bofa_total = 0
    chase_total = 0
    bofa_count = 0
    chase_count = 0

    for tx in transactions:
        amount = tx_amount(tx)

        if is_bofa_grocery(tx):
            bofa_total += amount
            bofa_count += 1

        if is_chase_amazon_travel(tx):
            chase_total += amount
            chase_count += 1

    alerts = []

    if bofa_total > BOFA_THRESHOLD:
        alerts.append(
            f"Bank of America grocery spend is ${bofa_total:.2f}, "
            f"above the ${BOFA_THRESHOLD} threshold for {period}."
        )

    if chase_total > CHASE_THRESHOLD:
        alerts.append(
            f"Chase Freedom Amazon/Chase Travel spend is ${chase_total:.2f}, "
            f"above the ${CHASE_THRESHOLD} threshold for {period}."
        )

    print(f"Period: {period}")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Transactions found: {len(transactions)}")
    print(f"BofA grocery total: ${bofa_total:.2f}")
    print(f"BofA matched transactions: {bofa_count}")
    print(f"Chase Amazon/Travel total: ${chase_total:.2f}")
    print(f"Chase matched transactions: {chase_count}")

    if alerts:
        print("Sending alert email.")
        send_email(
            subject=f"Credit card spend alert — {period}",
            body="\n".join(alerts),
        )
        print("Email send attempted.")
    else:
        print("No alerts because totals are below thresholds or no transactions matched.")


if __name__ == "__main__":
    asyncio.run(main())