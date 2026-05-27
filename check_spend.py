import os
import json
import asyncio
from datetime import date

import resend
from monarchmoney import MonarchMoney


BOFA_THRESHOLD = 2500
CHASE_THRESHOLD = 1500


def quarter_range():
    today = date.today()
    quarter_start_month = ((today.month - 1) // 3) * 3 + 1
    start = today.replace(month=quarter_start_month, day=1)
    quarter = f"Q{((today.month - 1) // 3) + 1}"
    period = f"{today.year}-{quarter}"
    return start.isoformat(), today.isoformat(), period

def tx_blob(tx):
    return json.dumps(tx, default=str).lower()


def tx_amount(tx):
    return abs(float(tx.get("amount", 0) or 0))


def is_bofa_grocery(tx):
    text = tx_blob(tx)

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


async def main():
    start_date, end_date, period = quarter_range()

    mm = MonarchMoney()

    await mm.login(
        email=os.environ["MONARCH_EMAIL"],
        password=os.environ["MONARCH_PASSWORD"],
        mfa_secret_key=os.environ.get("MONARCH_MFA_SECRET_KEY"),
        save_session=False,
        use_saved_session=False,
    )

    data = await mm.get_transactions(
        start_date=start_date,
        end_date=end_date,
        limit=500,
    )

    transactions = data.get("allTransactions", {}).get("results", [])

    for tx in transactions[:20]:
    print(json.dumps({
        "account": tx.get("account"),
        "accountName": tx.get("accountName"),
        "displayName": tx.get("displayName"),
        "merchantName": tx.get("merchantName"),
        "merchant_name": tx.get("merchant_name"),
        "originalStatement": tx.get("originalStatement"),
    }, indent=2, default=str))

    bofa_total = 0
    chase_total = 0

    for tx in transactions:
        amount = tx_amount(tx)

        if is_bofa_grocery(tx):
            bofa_total += amount

        if is_chase_amazon_travel(tx):
            chase_total += amount

    alerts = []

    if bofa_total > BOFA_THRESHOLD:
        alerts.append(
            f"BofA grocery spend is ${bofa_total:.2f}, "
            f"above ${BOFA_THRESHOLD}"
        )

    if chase_total > CHASE_THRESHOLD:
        alerts.append(
            f"Chase Freedom Amazon/Travel spend is "
            f"${chase_total:.2f}, above ${CHASE_THRESHOLD}"
        )

    if alerts:
        send_email(
            subject=f"Credit card spend alert — {period}",
            body="\n".join(alerts)
        )

    print({
        "period": period,
        "bofa_total": bofa_total,
        "chase_total": chase_total,
        "alerts": alerts,
    })


if __name__ == "__main__":
    asyncio.run(main())