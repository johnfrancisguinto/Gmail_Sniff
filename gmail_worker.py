import os
import json
import gspread
import pandas as pd

from datetime import datetime

from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build


# ==================================================
# CONFIG
# ==================================================

SPREADSHEET_ID = "1Zx9yhlJb4gr8yKec7owh3xhwG36azWXhx4eK5WIYPR4"

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify"
]


# ==================================================
# GOOGLE SHEETS AUTH
# ==================================================

def authenticate_sheets():

    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

    return gspread.authorize(creds)


client = authenticate_sheets()


# ==================================================
# GMAIL AUTH
# ==================================================

def authenticate_gmail():

    # LOCAL VERSION
    token_data = json.loads(os.environ["GMAIL_TOKEN"])
    
    creds = UserCredentials.from_authorized_user_info(
        token_data,
        GMAIL_SCOPES
    )

    service = build(
        "gmail",
        "v1",
        credentials=creds
    )

    return service


# ==================================================
# EMAIL FETCH
# ==================================================

def fetch_unread_emails(service):

    query = (
        'is:unread '
        '(subject:"BMS Log Tool" OR subject:"FQC Log Tool")'
    )

    results = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=20
    ).execute()

    return results.get("messages", [])


# ==================================================
# EMAIL DETAILS
# ==================================================

def get_email_data(service, msg_id):

    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=["Subject", "Date"]
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])

    subject = ""
    email_date = ""

    for h in headers:

        if h["name"] == "Subject":
            subject = h["value"]

        if h["name"] == "Date":
            email_date = h["value"]

    email_time = pd.to_datetime(
        email_date,
        errors="coerce"
    )

    if pd.isna(email_time):
        email_time = datetime.now()

    return subject, email_time


# ==================================================
# SUBJECT PARSING
# ==================================================

def extract_from_subject(subject, email_time):

    """
    Expected format:

    Scanner - ABC123456 - PASS
    BMS - ABC123456 - FAIL
    FQC - ABC123456 - PASS
    """

    try:

        parts = [p.strip() for p in subject.split("-")]

        if len(parts) != 3:
            return None

        station = parts[0]
        serial_number = parts[1]
        result = parts[2].upper()

        if result not in ["PASS", "FAIL"]:
            return None

        return {
            "datetime": email_time.strftime("%Y-%m-%d %H:%M:%S"),
            "station": station,
            "serial_number": serial_number,
            "results": result
        }

    except Exception as e:

        print(f"Subject parse failed: {e}")
        return None


# ==================================================
# ROUTING
# ==================================================

def route_to_sheet(station):

    station = station.strip()

    bike_stations = [
        "Scanner",
        "MBB Config",
        "PREL",
        "FQC Log Tool Scan"
    ]

    bcb_stations = [
        "BAT0",
        "BAT2/3",
        "BMS"
    ]

    if station in bike_stations:
        return "Bike_line"

    elif station in bcb_stations:
        return "BCB_line"

    return "CII_line"


# ==================================================
# DUPLICATE CHECK
# ==================================================

def is_duplicate(sheet_name, data):

    try:

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(sheet_name)

        records = sheet.get_all_records()

        if not records:
            return False

        df = pd.DataFrame(records)

        df.columns = [
            c.strip().lower().replace(" ", "_")
            for c in df.columns
        ]

        required_cols = [
            "datetime",
            "station",
            "serial_number",
            "results"
        ]

        if not all(
            col in df.columns
            for col in required_cols
        ):
            return False

        match = df[
            (df["datetime"].astype(str) == str(data["datetime"])) &
            (df["station"].astype(str) == str(data["station"])) &
            (df["serial_number"].astype(str) == str(data["serial_number"])) &
            (df["results"].astype(str) == str(data["results"]))
        ]

        return not match.empty

    except Exception as e:

        print(f"Duplicate check error: {e}")
        return False


# ==================================================
# APPEND DATA
# ==================================================

def append_to_sheet(sheet_name, data):

    try:

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(sheet_name)

        sheet.append_row([
            data["datetime"],
            "FQC",
            data["serial_number"],
            data["results"]
        ])

        print(
            f"Added: "
            f"{data['serial_number']} | "
            f"{data['station']} | "
            f"{data['results']}"
        )

    except Exception as e:

        print(f"Append failed: {e}")


# ==================================================
# MARK AS READ
# ==================================================

def mark_as_read(service, msg_id):

    try:

        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={
                "removeLabelIds": ["UNREAD"]
            }
        ).execute()

    except Exception as e:

        print(f"Mark read failed: {e}")


# ==================================================
# MAIN PROCESSOR
# ==================================================

def process_emails():

    try:

        service = authenticate_gmail()

        messages = fetch_unread_emails(service)

        if not messages:

            print("No unread emails found.")
            return

        print(f"Found {len(messages)} unread emails.")

        for msg in messages:

            msg_id = msg["id"]

            try:

                subject, email_time = get_email_data(
                    service,
                    msg_id
                )

                data = extract_from_subject(
                    subject,
                    email_time
                )

                if not data:

                    mark_as_read(
                        service,
                        msg_id
                    )
                    continue

                sheet_name = route_to_sheet(
                    data["station"]
                )

                if not is_duplicate(
                    sheet_name,
                    data
                ):

                    append_to_sheet(
                        sheet_name,
                        data
                    )

                else:

                    print(
                        f"Duplicate skipped: "
                        f"{data['serial_number']}"
                    )

                mark_as_read(
                    service,
                    msg_id
                )

            except Exception as e:

                print(
                    f"Failed processing "
                    f"email {msg_id}: {e}"
                )

    except Exception as e:

        print(f"Worker error: {e}")


# ==================================================
# ENTRY POINT
# ==================================================

if __name__ == "__main__":

    print("====================================")
    print("Starting Gmail Worker")
    print("====================================")

    process_emails()

    print("====================================")
    print("Finished")
    print("====================================")
