"""
Deploy para VPS Hostinger.
- Primeira execução: instala deps Python, cria serviço systemd, configura Nginx
- Execuções seguintes: sincroniza arquivos e reinicia o serviço
"""
import os
import sys
import paramiko
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP", "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER", "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/offtrade"
SERVICE      = "offtrade"
PORT         = 22

# Arquivos copiados a cada deploy
SYNC_FILES = ["app.py", ".env"]

SYSTEMD_UNIT = f"""\
[Unit]
Description=Offtrade Streamlit
After=network.target

[Service]
User=root
WorkingDirectory={REMOTE_DIR}
EnvironmentFile={REMOTE_DIR}/.env
ExecStart=/usr/local/bin/streamlit run {REMOTE_DIR}/app.py \\
    --server.port 8501 \\
    --server.headless true \\
    --server.enableCORS false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

NGINX_CONF = """\
server {
    listen 80;
    server_name offtrade.duckdns.org _;

    location / {
        proxy_pass         http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_read_timeout 86400;
    }
}
"""

PIP_PACKAGES = (
    "streamlit oracledb pandas requests "
    "streamlit-oauth streamlit-cookies-controller python-dotenv"
)


def ssh_run(client, cmd, check=True):
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out  = stdout.read().decode().strip()
    err  = stderr.read().decode().strip()
    if out:
        print(out)
    if err and (check and code != 0):
        print(f"[stderr] {err}")
    if check and code != 0:
        raise RuntimeError(f"Falhou (cod {code}): {cmd}")
    return out, code


def setup_ambiente(client, sftp):
    print("   Atualizando apt e instalando python3-pip...")
    ssh_run(client, "apt-get update -qq && apt-get install -y python3-pip python3-venv -qq")

    print("   Instalando pacotes Python...")
    ssh_run(client, f"pip3 install --quiet {PIP_PACKAGES}")

    print("   Criando serviço systemd...")
    with sftp.open("/etc/systemd/system/offtrade.service", "w") as f:
        f.write(SYSTEMD_UNIT)
    ssh_run(client, "systemctl daemon-reload && systemctl enable offtrade")

    print("   Configurando Nginx...")
    with sftp.open("/etc/nginx/sites-available/offtrade", "w") as f:
        f.write(NGINX_CONF)
    ssh_run(client,
        "ln -sf /etc/nginx/sites-available/offtrade /etc/nginx/sites-enabled/offtrade "
        "&& rm -f /etc/nginx/sites-enabled/default",
        check=False,
    )
    ssh_run(client, "nginx -t && systemctl reload nginx")
    print("   Setup concluído.")


def deploy():
    print(f"-> Conectando à VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    except Exception as e:
        print(f"[ERRO] Não foi possível conectar via SSH: {e}")
        sys.exit(1)

    sftp = client.open_sftp()

    # Criar diretório remoto se necessário
    ssh_run(client, f"mkdir -p {REMOTE_DIR}", check=False)

    # Detectar se é primeira vez (sem serviço instalado)
    _, code = ssh_run(client, f"systemctl is-enabled {SERVICE}", check=False)
    primeiro_deploy = code != 0

    # Sincronizar arquivos
    print("-> Sincronizando arquivos para VPS...")
    here = Path(__file__).parent
    for fname in SYNC_FILES:
        local = here / fname
        if local.exists():
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            print(f"   {fname} -> {REMOTE_DIR}/{fname}")
        else:
            print(f"   [AVISO] {fname} não encontrado localmente, pulando.")

    if primeiro_deploy:
        print("-> Primeiro deploy — configurando ambiente na VPS...")
        setup_ambiente(client, sftp)

    print("-> Reiniciando serviço offtrade...")
    ssh_run(client, f"systemctl restart {SERVICE}")

    status, _ = ssh_run(client, f"systemctl is-active {SERVICE}", check=False)
    if status == "active":
        print(f"OK Streamlit ativo em http://offtrade.duckdns.org")
    else:
        print(f"[AVISO] Serviço não está ativo. Verifique: journalctl -u {SERVICE} -n 30")

    sftp.close()
    client.close()


if __name__ == "__main__":
    deploy()
