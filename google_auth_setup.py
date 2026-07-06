"""
Autorizacao unica do Google Drive (rodar uma vez no seu PC).
Abre o navegador para login com a conta @rigarr.com.br e salva
token.json com o refresh_token, que sera copiado para a VPS depois.

Usa o client OAuth em cred_drive.json (tipo "Web application" no Google
Cloud Console). Como e' um client Web, o redirect URI precisa bater
exatamente com o cadastrado no Console — por isso a porta é FIXA
(PORT abaixo). Cadastre "http://localhost:{PORT}/" como URI de redirecionamento
autorizado no OAuth Client antes de rodar este script.
"""
import ssl
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# Nesta rede, o handshake TLS 1.3 com googleapis.com falha de forma
# intermitente (SSLEOFError) — provavelmente inspeção de tráfego que não lida
# bem com TLS 1.3. Forçar no máximo TLS 1.2 resolve. urllib3 monta o proprio
# SSLContext (nao usa ssl.create_default_context), entao o patch tem que ser
# na propria classe SSLContext. So' afeta este script (autorização local
# unica), não o resto do pipeline.
_orig_init = ssl.SSLContext.__new__
def _patched_new(cls, *args, **kwargs):
    ctx = _orig_init(cls, *args, **kwargs)
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx
ssl.SSLContext.__new__ = _patched_new

PORT = 8765

CRED_FILE = Path(__file__).parent / "cred_drive.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
creds = flow.run_local_server(port=PORT)

token_path = Path(__file__).parent / "token.json"
token_path.write_text(creds.to_json(), encoding="utf-8")
print(f"[OK] token.json salvo em {token_path}")
print("Guarde este arquivo com cuidado — ele dá acesso de leitura ao Drive compartilhado.")
