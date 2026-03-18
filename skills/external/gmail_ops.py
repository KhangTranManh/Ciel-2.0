import inspect
from pathlib import Path
from langchain_google_community import GmailToolkit
from langchain_google_community.gmail.utils import (
    build_resource_service,
    get_gmail_credentials,
)


def _get_gmail_credentials_compat(token_file: Path, credentials_file: Path):
    """Support both old and typo'd argument names across package versions."""
    params = inspect.signature(get_gmail_credentials).parameters
    common_kwargs = {
        "token_file": str(token_file),
        "scopes": ["https://mail.google.com/"],
    }

    if "client_secrets_file" in params:
        return get_gmail_credentials(
            **common_kwargs,
            client_secrets_file=str(credentials_file),
        )

    if "client_sercret_file" in params:
        return get_gmail_credentials(
            **common_kwargs,
            client_sercret_file=str(credentials_file),
        )

    raise TypeError("Unsupported get_gmail_credentials signature in current langchain-google-community version.")

def get_gmail_tools() -> list:
    """Initialize and return the full arsenal of Gmail tools."""
    try:
        base_dir = Path(__file__).resolve().parent.parent.parent
        credentials_file = base_dir / "credentials.json"
        
        # We use a separate token file for Gmail to avoid crossing wires with the Calendar
        token_file = base_dir / "ciel_data" / "gmail_token.json"

        if not credentials_file.exists():
            print("[Ciel Fatal] Missing credentials.json. Gmail armory locked.")
            return []

        # Ensure the secure vault directory exists
        token_file.parent.mkdir(parents=True, exist_ok=True)

        # This powerful LangChain utility handles the entire OAuth flow.
        # It will open the browser once, then save the token for silent background use forever.
        credentials = _get_gmail_credentials_compat(token_file, credentials_file)

        # Build the API bridge and load the toolkit
        api_resource = build_resource_service(credentials=credentials)
        toolkit = GmailToolkit(api_resource=api_resource)
        
        # Returns a list of BaseTool objects
        return toolkit.get_tools()

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Gmail toolkit: {e}")
        return []