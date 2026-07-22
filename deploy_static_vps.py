"""
Deploy do site estático para VPS Hostinger.
- Sincroniza todo *.html da raiz (exceto exemplo.html) e todo *.js (dados + auth)
  para /opt/offtrade-static.
- Servido pelo nginx com URL limpa (try_files) — não precisa reiniciar serviço.
"""
import os, sys
import paramiko
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/offtrade-static"
PORT         = 22

HERE = Path(__file__).parent

EXCLUDE_HTML = {"exemplo.html"}

# acessos_data.js só existe de verdade na VPS (gerado a partir do log do
# nginx por exportacao_acessos.py, via cron próprio a cada 10 min) — a cópia
# local é só um stub vazio de teste (não há log de nginx fora da VPS).
# Sincronizar isso por cima sobrescreveria os dados reais de acesso.
EXCLUDE_JS = {"acessos_data.js"}


def ssh_run(client, cmd, check=True):
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out  = stdout.read().decode(errors="replace").strip()
    err  = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err and check and code != 0:
        print(f"[stderr] {err}")
    if check and code != 0:
        raise RuntimeError(f"Falhou (cod {code}): {cmd}")
    return out, code


def static_files() -> list[Path]:
    html_files = [f for f in HERE.glob("*.html") if f.name not in EXCLUDE_HTML]
    js_files   = [f for f in HERE.glob("*.js") if f.name not in EXCLUDE_JS]
    return sorted(html_files) + sorted(js_files)


def deploy():
    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    except Exception as e:
        print(f"[ERRO] SSH: {e}")
        sys.exit(1)

    sftp = client.open_sftp()
    ssh_run(client, f"mkdir -p {REMOTE_DIR}", check=False)

    print("\n-> Sincronizando arquivos estáticos...")
    for local in static_files():
        remote = f"{REMOTE_DIR}/{local.name}"
        sftp.put(str(local), remote)
        print(f"   {local.name} -> {remote}")

    sftp.close()
    client.close()
    print(f"\nOK site estático atualizado em {REMOTE_DIR} - https://offtrade.duckdns.org")


if __name__ == "__main__":
    deploy()
