import os
import json
from pathlib import Path
from getpass import getpass
from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

load_dotenv()

TOKENSTORE = os.path.expanduser("~/.garminconnect")

def init_api():
    # Try saved tokens first
    try:
        client = Garmin()
        client.login(TOKENSTORE)
        print("Logged in using saved tokens.")
        return client
    except (GarminConnectAuthenticationError, GarminConnectConnectionError):
        print("No saved tokens — logging in fresh.")

    # Fresh login
    email = os.getenv("GARMIN_EMAIL") or input("Email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass("Password: ")

    try:
        client = Garmin(
            email=email,
            password=password,
            prompt_mfa=lambda: input("MFA code (check your email): ").strip(),
        )
        client.login(TOKENSTORE)
        print(f"Login successful. Tokens saved to {TOKENSTORE}")
        return client

    except GarminConnectTooManyRequestsError:
        print("Rate limited by Garmin. Wait and try again later.")
    except GarminConnectAuthenticationError:
        print("Wrong credentials.")
    except GarminConnectConnectionError as e:
        print(f"Connection error: {e}")

    return None

if __name__ == "__main__":
    client = init_api()
    if client:
        print("Connected to Garmin successfully!")
