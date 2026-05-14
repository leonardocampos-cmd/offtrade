# deploy_vps.py — Copia arquivos do dashboard para a VPS via SCP
import os
import paramiko
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_HOST = os.getenv("VPS_IP",       "147.79.107.137")
VPS_PORT = int(os.getenv("VPS_PORT", "22"))
VPS_USER = os.getenv("VPS_USER",     "root")
VPS_PASS = os.getenv("VPS_PASSWORD", "")
VPS_DIR  = "/var/www/offtrade"

FILES = [
    "metas.html",
    "sp.html",
    "metas_data.js",
    "vendas_data.js",
    "vendas_sp_data.js",
    "entregas_data.js",
]

def deploy():
    base = Path(__file__).parent
    print(f"-> Conectando à VPS {VPS_HOST}...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, password=VPS_PASS)

    sftp = ssh.open_sftp()
    for fname in FILES:
        local = base / fname
        remote = f"{VPS_DIR}/{fname}"
        if local.exists():
            sftp.put(str(local), remote)
            print(f"   OK {fname}")
        else:
            print(f"   SKIP {fname} (não encontrado)")
    sftp.close()
    ssh.close()
    print(f"OK deploy VPS concluido -> http://{VPS_HOST}/")

if __name__ == "__main__":
    deploy()
