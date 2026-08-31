"""
Deploy do login_api.py (validação de senha de vendedor) pra rodar como
serviço systemd próprio na VPS, atrás do nginx em
https://offtrade.duckdns.org/api/auth/.

Serviço separado do vencimento.service (que já responde em /api/) por
pedido explícito — porta e systemd unit próprios, só a rota /api/auth/
do nginx é que direciona pra cá.

- Sincroniza login_api.py pra /opt/login-api.
- Cria .venv próprio com um requirements.txt mínimo.
- Gera o .env remoto com OFFTRADE_RUNTIME=vps e ORACLE_LIB do Linux.
- Instala/atualiza o systemd unit login-api.service (Restart=always).
- Adiciona o location /api/auth/ no nginx (site "offtrade") se ainda não
  existir, e recarrega o nginx.
"""
import io
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
REMOTE_DIR   = "/opt/login-api"
PORT         = 22

HERE = Path(__file__).parent

APP_FILES = ["login_api.py"]

REQUIREMENTS = "flask\noracledb\nsqlalchemy\npandas\npython-dotenv\nrequests\n"

SYSTEMD_UNIT = f"""[Unit]
Description=Login API - validacao de senha de vendedor (Flask)
After=network.target

[Service]
WorkingDirectory={REMOTE_DIR}
ExecStart={REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/login_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

# /api/auth/ tem que vir ANTES de /api/ na ordem em que o script tenta achar
# marcador (embora nginx já resolva por prefixo mais específico independente
# de ordem) — mantém a mesma estrutura de bloco que /vencimento/ e /api/ já
# usam em deploy_vencimento_vps.py.
NGINX_LOCATION_AUTH = f"""
    location /api/auth/ {{
        proxy_pass         http://127.0.0.1:5051;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 40s;
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


def build_remote_env(local_env_text: str) -> str:
    linhas = local_env_text.splitlines()
    linhas = [l for l in linhas if not re.match(r"^\s*(OFFTRADE_RUNTIME|ORACLE_LIB)\s*=", l)]
    linhas.append("OFFTRADE_RUNTIME=vps")
    linhas.append("ORACLE_LIB=/opt/oracle/instantclient_21_1")
    return "\n".join(linhas) + "\n"


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

    print("\n-> Gravando requirements.txt e .env remotos...")
    sftp.putfo(io.BytesIO(REQUIREMENTS.encode()), f"{REMOTE_DIR}/requirements.txt")
    env_local = (HERE / ".env").read_text(encoding="utf-8")
    env_remoto = build_remote_env(env_local)
    sftp.putfo(io.BytesIO(env_remoto.encode()), f"{REMOTE_DIR}/.env")

    print(f"\n-> Instalando dependências em {REMOTE_DIR}/.venv...")
    ssh_run(client, f"test -d {REMOTE_DIR}/.venv || python3 -m venv {REMOTE_DIR}/.venv", check=False)
    ssh_run(client, f"{REMOTE_DIR}/.venv/bin/pip install -q -r {REMOTE_DIR}/requirements.txt")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/login-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable login-api.service", check=False)
    ssh_run(client, "systemctl restart login-api.service")

    print("\n-> Conferindo nginx (adiciona location /api/auth/ se faltando)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    if "location /api/auth/" not in conf_atual:
        conf_novo = conf_atual.replace(marker, NGINX_LOCATION_AUTH + "\n" + marker, 1)
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location /api/auth/ adicionada.")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active login-api.service", check=False)
    ssh_run(client, "curl -s -X POST -H 'Content-Type: application/json' -d '{}' "
                    "-o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:5051/api/auth/login-vendedor", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/auth/login-vendedor")


if __name__ == "__main__":
    deploy()
