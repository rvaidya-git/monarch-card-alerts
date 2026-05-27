import os
import json
import asyncio
from datetime import date

import resend
from monarchmoney import MonarchMoney


BOFA_THRESHOLD = 2500
CHASE_THRESHOLD = 1500


def month_range():
    today = date.today()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat(), today.strftime("%Y-%m")


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
    text = tx_blob(tx)

    return (
        ("chase freedom" in text or "chase" in text)
        and (
            "amazon" in text
            or "amzn" in text
            or "chase travel" in text
            or "ultimate rewards travel" in text
        )
    )


def send_email(subject, body):
    resend.api_key = os.environ["RESEND_API_KEY"]

    resend.Emails.send({
        "from": os.environ["ALERT_EMAIL_FROM"],
        "to": [os.environ["ALERT_EMAIL_TO"]],
        "subject": subject,
        "text": body,
    })


async def main():
    start_date, end_date, month = month_range()

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
            subject=f"Credit card spend alert — {month}",
            body="\n".join(alerts)
        )

    print({
        "month": month,
        "bofa_total": bofa_total,
        "chase_total": chase_total,
        "alerts": alerts,
    })


if __name__ == "__main__":
    asyncio.run(main())