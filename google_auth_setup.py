"""
Autorizacao unica do Google Drive (rodar uma vez no seu PC).
Abre o navegador para login com a conta @rigarr.com.br e salva
token.json com o refresh_token, que sera copiado para a VPS depois.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

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

token_path = Path(__file__).parent / "token.json"
token_path.write_text(creds.to_json(), encoding="utf-8")
print(f"[OK] token.json salvo em {token_path}")
print("Guarde este arquivo com cuidado — ele dá acesso de leitura ao Drive compartilhado.")
