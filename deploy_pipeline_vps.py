"""
Deploy do pipeline ETL (main.py + exportacoes) para rodar direto na VPS,
de hora em hora via cron (seg-sex, horario comercial), sem depender do
PC local ligado.

- Sincroniza main.py + todos os scripts do pipeline + credenciais para
  /opt/offtrade-pipeline (diretorio dedicado, separado do app Streamlit
  em /opt/offtrade e do site estatico em /opt/offtrade-static).
- O .env remoto e' ajustado com OFFTRADE_RUNTIME=vps e SEND_ALERTS=1, para
  que main.py saiba que deve copiar localmente pro /opt/offtrade-static
  (em vez de SSH) e que e' esse lado que deve mandar os alertas.
- pedidos_enviados.json / alertas_rj.json (registros de dedupe de alerta)
  so' sao enviados na PRIMEIRA vez (se ainda nao existirem remotamente),
  pra nao sobrescrever o estado que a VPS for acumulando sozinha.
"""
import os
import re
import sys
from pathlib import Path

import paramiko
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/offtrade-pipeline"
PORT         = 22

HERE = Path(__file__).parent

PIPELINE_FILES = [
    "main.py",
    "utils.py",
    "meta.py",
    "nao_positivados.py",
    "entregas.py",
    "pedidos.py",
    "conferencia_preco.py",
    "envio_whatsapp.py",
    "report_diario_vendedor.py",
    "alerta_logistica_rj.py",
    "baixar_planilhas_drive.py",
    "campanha_crusoe.py",
    "requirements.txt",
    "metas_config.json",
] + sorted(f.name for f in HERE.glob("exportacao_*.py"))

# Credenciais OAuth ja autorizadas localmente (refresh_token evita novo login)
CREDENTIAL_FILES = [
    "credentials_gmail.json",
    "token.json",
    "token_gmail.json",
]

# Registros de dedupe de alerta — so' semeia se ainda nao existir na VPS
SEED_STATE_FILES = [
    "pedidos_enviados.json",
    "alertas_rj.json",
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


def remote_exists(sftp, remote_path: str) -> bool:
    try:
        sftp.stat(remote_path)
        return True
    except FileNotFoundError:
        return False


def build_remote_env(local_env_text: str) -> str:
    """Ajusta o .env local para a VPS: OFFTRADE_RUNTIME=vps, SEND_ALERTS=1,
    ORACLE_LIB apontando pro instant client Linux (o .env local tem o
    caminho C:\\instantclient do Windows, que não existe na VPS)."""
    linhas = local_env_text.splitlines()
    linhas = [l for l in linhas if not re.match(r"^\s*(OFFTRADE_RUNTIME|SEND_ALERTS|ORACLE_LIB)\s*=", l)]
    linhas.append("OFFTRADE_RUNTIME=vps")
    linhas.append("SEND_ALERTS=1")
    linhas.append("ORACLE_LIB=/opt/oracle/instantclient_21_1")
    return "\n".join(linhas) + "\n"


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

    print("\n-> Sincronizando scripts do pipeline...")
    for fname in PIPELINE_FILES:
        local = HERE / fname
        if local.exists():
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            print(f"   {fname} -> {REMOTE_DIR}/{fname}")
        else:
            print(f"   [skip] {fname} não encontrado")

    print("\n-> Sincronizando credenciais (tokens OAuth ja autorizados)...")
    for fname in CREDENTIAL_FILES:
        local = HERE / fname
        if local.exists():
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            print(f"   {fname} -> {REMOTE_DIR}/{fname}")
        else:
            print(f"   [skip] {fname} não encontrado localmente")

    print("\n-> Semeando registros de dedupe de alerta (só se ainda não existirem na VPS)...")
    for fname in SEED_STATE_FILES:
        local = HERE / fname
        remote_path = f"{REMOTE_DIR}/{fname}"
        if not local.exists():
            print(f"   [skip] {fname} não encontrado localmente")
            continue
        if remote_exists(sftp, remote_path):
            print(f"   [skip] {fname} já existe na VPS — mantendo estado acumulado lá")
            continue
        sftp.put(str(local), remote_path)
        print(f"   {fname} -> {remote_path} (semeado)")

    print("\n-> Gerando .env remoto (OFFTRADE_RUNTIME=vps, SEND_ALERTS=1)...")
    env_local = (HERE / ".env").read_text(encoding="utf-8")
    env_remoto = build_remote_env(env_local)
    sftp.putfo(__import__("io").BytesIO(env_remoto.encode("utf-8")), f"{REMOTE_DIR}/.env")
    print(f"   .env -> {REMOTE_DIR}/.env")

    print(f"\n-> Instalando dependências Python em {REMOTE_DIR}/.venv...")
    ssh_run(client, f"test -d {REMOTE_DIR}/.venv || python3 -m venv {REMOTE_DIR}/.venv", check=False)
    ssh_run(client, f"{REMOTE_DIR}/.venv/bin/pip install -q -r {REMOTE_DIR}/requirements.txt", check=False)

    sftp.close()
    client.close()
    print(f"\nOK pipeline sincronizado em {REMOTE_DIR}")
    print("Lembrete: configurar o cron em root (0 8-18 * * 1-5) apontando pro main.py desse diretório.")


if __name__ == "__main__":
    deploy()
