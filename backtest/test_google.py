import os
import datetime
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from colorama import Fore, Style
import colorama

colorama.init()

# The scope defines what we are allowed to do. This allows reading calendar events.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def test_google_connection():
    print(Fore.CYAN + "[Ciel System]: Initiating Google OAuth Handshake..." + Style.RESET_ALL)
    
    base_dir = Path(__file__).resolve().parent.parent
    creds_path = base_dir / 'credentials.json'
    token_path = base_dir / 'ciel_data' / 'calendar_token.json'

    if not creds_path.exists():
        print(Fore.RED + f"[Fatal]: {creds_path} not found. Please ensure it is in the root directory." + Style.RESET_ALL)
        return

    creds = None
    # 1. Check if we already have a saved token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # 2. If no valid token, force the user to log in via browser
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print(Fore.YELLOW + "Awaiting Master's authorization in the browser..." + Style.RESET_ALL)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the token so we don't have to log in again
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            print(Fore.GREEN + "[Ciel System]: Token securely saved to vault." + Style.RESET_ALL)

    # 3. Test the connection by fetching upcoming events
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Get current time in UTC (timezone-aware, avoids utcnow deprecation)
        now = datetime.datetime.now(datetime.UTC).isoformat().replace('+00:00', 'Z')
        print(Fore.CYAN + "\nFetching your next 5 upcoming events..." + Style.RESET_ALL)
        
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=5, singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])

        if not events:
            print(Fore.YELLOW + "No upcoming events found in your calendar." + Style.RESET_ALL)
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            print(f"- {start} : {summary}")
            
        print(Fore.GREEN + "\n[Ciel System]: Connection to Google Calendar API is ABSOLUTELY FLAWLESS!" + Style.RESET_ALL)

    except Exception as e:
        print(Fore.RED + f"[Ciel System Error]: Connection failed. Details: {e}" + Style.RESET_ALL)

if __name__ == '__main__':
    test_google_connection()