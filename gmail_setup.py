"""
Autorização única do Gmail (rodar UMA VEZ no PC).
Abre o navegador para login com a conta do Google e salva token_gmail.json.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CLIENT_CONFIG = {
    "installed": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=0)

token_path = Path(__file__).parent / "token_gmail.json"
token_path.write_text(creds.to_json(), encoding="utf-8")
print(f"[OK] token_gmail.json salvo em {token_path}")
print("Copie este arquivo para a VPS se quiser rodar lá também.")
