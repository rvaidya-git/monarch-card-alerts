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
BOFA_ACCOUNT_NAME = "R\u2019s BofA Credit Card"


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


def is_bofa_eligible_category(tx):
    text = tx_text(tx)

    account_name = (
        str(tx.get("accountName", "")) + " " +
        str(tx.get("displayName", "")) + " " +
        str(tx.get("account", ""))
    ).lower()

    if BOFA_ACCOUNT_NAME.lower() not in account_name:
        return False

    eligible_terms = [
        "online shopping",
        "shopping",
        "ecommerce",
        "e-commerce",
        "grocery",
        "groceries",
        "grocery stores",
        "supermarket",
        "supermarkets",
        "wholesale club",
        "wholesale clubs",
        "warehouse club",
        "warehouse clubs",
        "costco",
        "sams club",
        "sam's club",
        "bj's",
        "bjs wholesale"
    ]

    return any(term in text for term in eligible_terms)


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


def send_email(subject, alerts, period, bofa_total, chase_total):
    resend.api_key = os.environ["RESEND_API_KEY"]

    html = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; max-width: 650px;">
        <h2 style="margin-bottom: 8px;">
            Credit Card Spend Alert
        </h2>

        <div style="color: #666; margin-bottom: 24px;">
            Period: <strong>{period}</strong>
        </div>

        <table
            cellpadding="12"
            cellspacing="0"
            border="0"
            width="100%"
            style="border-collapse: collapse; margin-bottom: 24px;"
        >
            <tr style="background-color: #f5f5f5;">
                <th align="left">Category</th>
                <th align="right">Current Spend</th>
                <th align="right">Threshold</th>
            </tr>

            <tr>
                <td>
                    BofA Online Shopping / Grocery / Wholesale Clubs
                </td>
                <td align="right">
                    ${bofa_total:,.2f}
                </td>
                <td align="right">
                    ${BOFA_THRESHOLD:,.2f}
                </td>
            </tr>

            <tr>
                <td>
                    Chase Freedom Amazon / Chase Travel
                </td>
                <td align="right">
                    ${chase_total:,.2f}
                </td>
                <td align="right">
                    ${CHASE_THRESHOLD:,.2f}
                </td>
            </tr>
        </table>

        <div style="
            background-color: #fff8e1;
            border: 1px solid #ffe082;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 24px;
        ">
            <strong>Triggered Alerts</strong>
            <ul>
                {''.join(f'<li>{alert}</li>' for alert in alerts)}
            </ul>
        </div>

        <div style="font-size: 12px; color: #777;">
            Generated automatically from Monarch Money transaction data.
        </div>
    </div>
    """

    resend.Emails.send({
        "from": os.environ["ALERT_EMAIL_FROM"],
        "to": [os.environ["ALERT_EMAIL_TO"]],
        "subject": subject,
        "html": html,
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

        if is_bofa_eligible_category(tx):
            bofa_total += amount
            bofa_count += 1

        if is_chase_amazon_travel(tx):
            chase_total += amount
            chase_count += 1

    alerts = []

    if bofa_total > BOFA_THRESHOLD:
        alerts.append(
            f"Bank of America online shopping/grocery/wholesale club spend is "
            f"${bofa_total:,.2f}, above the ${BOFA_THRESHOLD:,.2f} threshold "
            f"for {period}."
        )

    if chase_total > CHASE_THRESHOLD:
        alerts.append(
            f"Chase Freedom Amazon/Chase Travel spend is "
            f"${chase_total:,.2f}, above the ${CHASE_THRESHOLD:,.2f} threshold "
            f"for {period}."
        )

    print(f"Period: {period}")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Transactions found: {len(transactions)}")
    print(f"BofA eligible-category total: ${bofa_total:.2f}")
    print(f"BofA matched transactions: {bofa_count}")
    print(f"Chase Amazon/Travel total: ${chase_total:.2f}")
    print(f"Chase matched transactions: {chase_count}")

    if alerts:
        print("Sending alert email.")
        send_email(
            subject=f"Credit card spend alert — {period}",
            alerts=alerts,
            period=period,
            bofa_total=bofa_total,
            chase_total=chase_total,
        )
        print("Email send attempted.")
    else:
        print("No alerts because totals are below thresholds or no transactions matched.")


if __name__ == "__main__":
    asyncio.run(main())