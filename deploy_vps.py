"""
Deploy para VPS Hostinger.
- Sincroniza app.py, utils.py, pages/ (Credito_e_Cadastro, Admin_Metas), .streamlit/
  para /opt/offtrade
- Reinicia o serviço offtrade
"""
import os, sys
import paramiko
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/offtrade"
SERVICE      = "offtrade"
PORT         = 22

HERE = Path(__file__).parent

# Arquivos individuais na raiz
ROOT_FILES = [
    "app.py",
    "utils.py",
    "requirements.txt",
    ".env",
]

# ORACLE_LIB é específico de cada máquina (aqui é um caminho Windows tipo
# "C:\instantclient"; na VPS é este diretório Linux do instantclient). Sincronizar
# o .env local por cima do da VPS sobrescrevia esse valor com o caminho Windows
# e quebrava o Oracle no Streamlit (DPI-1047) — corrigido de volta após o envio.
VPS_ORACLE_LIB = "/opt/oracle/instantclient_21_1"

# Diretórios a sincronizar (recursivo)
SYNC_DIRS = [
    "pages",
    ".streamlit",
]

# Arquivo de metas (só envia se existir localmente) — usado por Admin_Metas.py
OPTIONAL_FILES = [
    "metas_config.json",
]


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


def sync_file(sftp, local: Path, remote: str):
    try:
        sftp.stat(os.path.dirname(remote))
    except FileNotFoundError:
        ssh_run(sftp._transport.open_session(), f"mkdir -p {os.path.dirname(remote)}", check=False)
    sftp.put(str(local), remote)
    print(f"   {local.name} -> {remote}")


def sync_dir(client, sftp, local_dir: Path, remote_dir: str):
    if not local_dir.exists():
        print(f"   [skip] {local_dir} não existe localmente")
        return
    ssh_run(client, f"mkdir -p {remote_dir}", check=False)
    for f in sorted(local_dir.iterdir()):
        if f.is_file() and not f.name.startswith("__"):
            sftp.put(str(f), f"{remote_dir}/{f.name}")
            print(f"   {f.relative_to(HERE)} -> {remote_dir}/{f.name}")


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

    print("\n-> Sincronizando arquivos raiz...")
    for fname in ROOT_FILES:
        local = HERE / fname
        if local.exists():
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            print(f"   {fname} -> {REMOTE_DIR}/{fname}")
        else:
            print(f"   [skip] {fname} não encontrado")

    ssh_run(client, f"sed -i 's|^ORACLE_LIB=.*|ORACLE_LIB={VPS_ORACLE_LIB}|' {REMOTE_DIR}/.env", check=False)

    for fname in OPTIONAL_FILES:
        local = HERE / fname
        if local.exists():
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            print(f"   {fname} -> {REMOTE_DIR}/{fname}")

    print("\n-> Limpando pages/ remoto (evita sobras de páginas removidas)...")
    ssh_run(client, f"rm -f {REMOTE_DIR}/pages/*.py", check=False)

    print("\n-> Sincronizando diretorios...")
    for d in SYNC_DIRS:
        sync_dir(client, sftp, HERE / d, f"{REMOTE_DIR}/{d}")

    print("\n-> Instalando dependências Python (se necessário)...")
    ssh_run(client, f"pip install -q -r {REMOTE_DIR}/requirements.txt", check=False)

    print(f"\n-> Reiniciando servico {SERVICE}...")
    ssh_run(client, f"systemctl restart {SERVICE}")

    status, _ = ssh_run(client, f"systemctl is-active {SERVICE}", check=False)
    if status == "active":
        print(f"\nOK Servico ativo - https://offtrade.duckdns.org")
    else:
        print(f"\n[AVISO] Serviço não está ativo. Verifique: journalctl -u {SERVICE} -n 30")

    sftp.close()
    client.close()


if __name__ == "__main__":
    deploy()
