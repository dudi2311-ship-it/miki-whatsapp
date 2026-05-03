"""One-time OAuth setup script to mint a refresh token with the scopes miki needs.

Run this locally (not on Render). It opens a browser, you sign in with the
personal Google account (dudi2311@gmail.com), approve the requested scopes,
and the script prints a new refresh token. Paste it into .env as
GOOGLE_REFRESH_TOKEN, replacing the old one.

Required: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CLIENT_CONFIG = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def main():
    flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )
    print("\n=== SUCCESS ===")
    print(f"Refresh token:\n{creds.refresh_token}\n")
    print("Granted scopes:")
    for s in creds.scopes or []:
        print(f"  - {s}")
    print("\nPaste the refresh token into .env as GOOGLE_REFRESH_TOKEN, then redeploy.")


if __name__ == "__main__":
    main()
