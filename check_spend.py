import os
import json
import asyncio
from datetime import date

import resend
import pyotp
from monarchmoney import MonarchMoney, RequireMFAException

from pathlib import Path

STATE_FILE = Path("alert_state.json")
BOFA_THRESHOLD = 2
CHASE_THRESHOLD = 1

# Update this after checking your GitHub Action logs.
CHASE_FREEDOM_ACCOUNT_NAME = "Freedom Card"
BOFA_ACCOUNT_NAME = "R\u2019s BofA Credit Card"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    
    
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


def is_chase_q3_bonus_category(tx):
    text = tx_text(tx)

    # FIRST: Ensure transaction belongs to the Chase Freedom card.
    account_name = (
        str(tx.get("accountName", "")) + " " +
        str(tx.get("displayName", "")) + " " +
        str(tx.get("account", ""))
    ).lower()

    if CHASE_FREEDOM_ACCOUNT_NAME.lower() not in account_name:
        return False

    # Q3 2026 Chase Freedom rotating categories:
    # July 1 - September 30, 2026

    gas_ev_terms = [
        "gas station",
        "gas stations",
        "gasoline",
        "fuel",
        "service station",
        "ev charging",
        "electric vehicle charging",
        "charging station",
        "chargepoint",
        "electrify america",
        "evgo"
    ]

    public_transit_terms = [
        "public transit",
        "transit",
        "train",
        "rail",
        "subway",
        "metro",
        "bus",
        "ferry",
        "toll",
        "tolls",
        "toll bridge",
        "highway toll",
        "parking",
        "parking lot",
        "parking garage",
        "garage"
    ]

    live_entertainment_terms = [
        "live entertainment",
        "concert",
        "concerts",
        "sporting event",
        "sporting events",
        "sports ticket",
        "ticketmaster",
        "seatgeek",
        "stubhub",
        "live nation",
        "zoo",
        "zoos",
        "amusement park",
        "amusement parks",
        "theme park",
        "theme parks"
    ]

    united_way_terms = [
        "united way",
        "unitedway"
    ]

    eligible_terms = (
        gas_ev_terms
        + public_transit_terms
        + live_entertainment_terms
        + united_way_terms
    )

    return any(term in text for term in eligible_terms)


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
                    Chase Freedom Bonus Category Transactions
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

    # Keep this available in case MFA is re-enabled later.
    mfa_secret = (
        os.environ.get("MONARCH_MFA_SECRET_KEY", "")
        .replace(" ", "")
        .strip()
    )

    try:
        await mm.login(
            email=email,
            password=password,
            save_session=False,
            use_saved_session=False,
        )

        print("Logged into Monarch without MFA.")

    except RequireMFAException:
        print("Monarch requires MFA.")

        if not mfa_secret:
            raise Exception(
                "Monarch requires MFA, but MONARCH_MFA_SECRET_KEY is missing."
            )

        mfa_code = pyotp.TOTP(mfa_secret).now()

        await mm.multi_factor_authenticate(
            email,
            password,
            mfa_code
        )

        print("Logged into Monarch using MFA.")


async def main():
    start_date, end_date, period = quarter_range()
    state = load_state()
    
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

        if is_chase_q3_bonus_category(tx):
            chase_total += amount
            chase_count += 1

    alerts_to_send = []

    bofa_key = f"{period}:BOFA"
    chase_key = f"{period}:CHASE"

    print(f"Period: {period}")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Transactions found: {len(transactions)}")
    print(f"BofA eligible-category total: ${bofa_total:,.2f}")
    print(f"BofA matched transactions: {bofa_count}")
    print(f"Chase quarterly bonus total: ${chase_total:,.2f}")
    print(f"Chase matched transactions: {chase_count}")
    print(f"BofA alert key: {bofa_key}")
    print(f"Chase alert key: {chase_key}")

    if bofa_total > BOFA_THRESHOLD and not state.get(bofa_key):
        alerts_to_send.append(
            f"Bank of America online shopping/grocery/wholesale club spend is "
            f"${bofa_total:,.2f}, above the ${BOFA_THRESHOLD:,.2f} threshold "
            f"for {period}."
        )
        state[bofa_key] = True
        print("BofA alert triggered.")

    elif bofa_total > BOFA_THRESHOLD and state.get(bofa_key):
        print("BofA is over threshold, but alert was already sent for this period.")

    if chase_total > CHASE_THRESHOLD and not state.get(chase_key):
        alerts_to_send.append(
            f"Chase Freedom Quarterly Bonus Category spend is "
            f"${chase_total:,.2f}, above the ${CHASE_THRESHOLD:,.2f} threshold "
            f"for {period}."
        )
        state[chase_key] = True
        print("Chase alert triggered.")

    elif chase_total > CHASE_THRESHOLD and state.get(chase_key):
        print("Chase is over threshold, but alert was already sent for this period.")

    if alerts_to_send:
        print("Sending alert email.")
        send_email(
            subject=f"Credit card spend alert — {period}",
            alerts=alerts_to_send,
            period=period,
            bofa_total=bofa_total,
            chase_total=chase_total,
        )
        print("Email send attempted.")
    else:
        print("No new alerts to send.")

    save_state(state)

if __name__ == "__main__":
    asyncio.run(main())