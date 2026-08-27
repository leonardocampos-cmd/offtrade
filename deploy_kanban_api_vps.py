"""
Deploy do kanban_api.py (CRUD do Kanban Mercos) pra rodar como serviço
systemd próprio na VPS, atrás do nginx em
https://offtrade.duckdns.org/api/kanban/.

Mesmo padrão de deploy_login_api_vps.py (serviço/porta/venv próprios) —
mas sem Oracle/`.env` nenhum, esse serviço só lê/escreve um JSON local.

- Sincroniza kanban_api.py pra /opt/kanban-api.
- Cria .venv próprio com um requirements.txt mínimo (só Flask).
- Instala/atualiza o systemd unit kanban-api.service (Restart=always).
- Adiciona o location /api/kanban/ no nginx (site "offtrade") se ainda não
  existir, e recarrega o nginx.
"""
import io
import os
from pathlib import Path

import paramiko
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/kanban-api"
PORT         = 22

HERE = Path(__file__).parent

APP_FILES = ["kanban_api.py"]

REQUIREMENTS = "flask\n"

SYSTEMD_UNIT = f"""[Unit]
Description=Kanban Mercos API - CRUD de tarefas (Flask)
After=network.target

[Service]
WorkingDirectory={REMOTE_DIR}
Environment=KANBAN_DATA_PATH={REMOTE_DIR}/kanban_data.json
ExecStart={REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/kanban_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION_KANBAN = f"""
    location /api/kanban/ {{
        proxy_pass         http://127.0.0.1:5054;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 20s;
    }}
"""


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


def deploy():
    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    sftp = client.open_sftp()

    print("\n-> Criando diretório remoto...")
    ssh_run(client, f"mkdir -p {REMOTE_DIR}", check=False)

    print("\n-> Sincronizando app...")
    for fname in APP_FILES:
        sftp.put(str(HERE / fname), f"{REMOTE_DIR}/{fname}")
        print(f"   {fname}")

    print("\n-> Gravando requirements.txt...")
    sftp.putfo(io.BytesIO(REQUIREMENTS.encode()), f"{REMOTE_DIR}/requirements.txt")

    print(f"\n-> Instalando dependências em {REMOTE_DIR}/.venv...")
    ssh_run(client, f"test -d {REMOTE_DIR}/.venv || python3 -m venv {REMOTE_DIR}/.venv", check=False)
    ssh_run(client, f"{REMOTE_DIR}/.venv/bin/pip install -q -r {REMOTE_DIR}/requirements.txt")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/kanban-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable kanban-api.service", check=False)
    ssh_run(client, "systemctl restart kanban-api.service")

    print("\n-> Conferindo nginx (adiciona location /api/kanban/ se faltando)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    if "location /api/kanban/" not in conf_atual:
        conf_novo = conf_atual.replace(marker, NGINX_LOCATION_KANBAN + "\n" + marker, 1)
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location /api/kanban/ adicionada.")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active kanban-api.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:5054/api/kanban/cards", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/kanban/cards")


if __name__ == "__main__":
    deploy()
