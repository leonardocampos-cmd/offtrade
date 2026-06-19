"""
Autorização única do Gmail (rodar UMA VEZ no PC).

PASSO A PASSO:
  1. Acesse: console.cloud.google.com → APIs & Services → Credentials
  2. Clique em "Create Credentials" → "OAuth 2.0 Client ID"
  3. Tipo: "Desktop app"  (NÃO escolha Web application)
  4. Baixe o JSON gerado e salve como  credentials_gmail.json  nesta pasta
  5. Rode:  python gmail_setup.py
  6. Faça login no navegador que abrir
  7. Copie o  token_gmail.json  gerado para a VPS se necessário
"""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES     = ["https://www.googleapis.com/auth/gmail.modify"]
BASE       = Path(__file__).parent
CREDS_FILE = BASE / "credentials_gmail.json"
TOKEN_FILE = BASE / "token_gmail.json"

if not CREDS_FILE.exists():
    raise FileNotFoundError(
        "\n[ERRO] credentials_gmail.json não encontrado.\n\n"
        "Siga os passos no topo deste arquivo para criar um OAuth 2.0 Client ID\n"
        "do tipo 'Desktop app' e baixar o JSON do Google Cloud Console."
    )

flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
creds = flow.run_local_server(port=0)

TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
print(f"\n[OK] token_gmail.json salvo em {TOKEN_FILE}")
print("Copie este arquivo para a VPS se quiser rodar lá também.")
