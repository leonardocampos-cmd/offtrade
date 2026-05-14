# deploy_vps.py — Copia arquivos do dashboard para a VPS via SCP
import paramiko
from pathlib import Path

VPS_HOST = "SEU_IP_AQUI"
VPS_PORT = 22
VPS_USER = "root"
VPS_KEY  = r"C:\Users\LeonardoCampos\.ssh\id_rsa"   # caminho da chave privada
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
    ssh.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, key_filename=VPS_KEY)

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
    print("OK deploy VPS concluído.")

if __name__ == "__main__":
    deploy()
