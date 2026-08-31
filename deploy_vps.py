"""
Deploy para VPS Hostinger.
- Sincroniza app.py, utils.py, app_pages/ (Preco_Promo), .streamlit/ para
  /opt/offtrade — Credito_e_Cadastro saiu do Streamlit em 2026-08-30 (agora é
  HTML estático + credito_cadastro_api.py, ver deploy_credito_cadastro_vps.py)
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
# "app_pages", não "pages": Streamlit auto-detecta QUALQUER pasta "pages/" e
# monta o menu lateral sozinho com ela, ignorando a lista construída em
# app.py via st.navigation()/st.Page() — foi exatamente essa auto-detecção
# concorrente que impedia esconder "Admin Objetivos"/"app" do menu por
# usuário (confirmado em 2026-08-04: st.navigation() nunca chegava a rodar
# de novo pra link direto numa subpágina, então a pasta pages/ "vencia").
SYNC_DIRS = [
    "app_pages",
    ".streamlit",
]

# Arquivo de metas (só envia se existir localmente) — usado por Admin_Objetivos.py
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

    print("\n-> Limpando app_pages/ remoto (evita sobras de páginas removidas)...")
    ssh_run(client, f"rm -f {REMOTE_DIR}/app_pages/*.py", check=False)
    print("-> Removendo pages/ antigo (nome mágico do Streamlit — não pode sobrar)...")
    ssh_run(client, f"rm -rf {REMOTE_DIR}/pages", check=False)

    print("\n-> Sincronizando diretorios...")
    for d in SYNC_DIRS:
        sync_dir(client, sftp, HERE / d, f"{REMOTE_DIR}/{d}")

    print("\n-> Instalando dependências Python (se necessário)...")
    # offtrade.service roda via /opt/offtrade/.venv/bin/streamlit (ver
    # ExecStart do systemd unit) — "pip install" sem caminho resolvia pro
    # pip do sistema (/usr/bin/pip), instalando num Python que o serviço
    # nunca usa. openpyxl (e qualquer dependência nova) ficava faltando no
    # venv de verdade mesmo já estando no requirements.txt — bug real
    # reportado pelo usuário em 2026-08-26 ("Import openpyxl failed" ao
    # tentar ler planilha em Preco_Promo.py).
    ssh_run(client, f"{REMOTE_DIR}/.venv/bin/pip install -q -r {REMOTE_DIR}/requirements.txt", check=False)

    print("\n-> Ajustando posse dos arquivos pro usuário do serviço...")
    # sftp.put() aqui roda como root (VPS_USER), então todo arquivo publicado
    # fica root:root sem escrita pra grupo/outros — mas offtrade.service roda
    # como User=ubuntu (systemd unit), que não conseguia criar/gravar nenhum
    # arquivo novo em REMOTE_DIR (ex: preco_promo.json, metas_config.json)
    # mesmo lendo normalmente. PermissionError real reportado pelo usuário em
    # 2026-08-26 (Preco_Promo.py::_save() -> "Permission denied:
    # '/opt/offtrade/preco_promo.json.tmp'").
    ssh_run(client, f"chown -R ubuntu:ubuntu {REMOTE_DIR}", check=False)

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
