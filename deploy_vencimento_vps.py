"""
Deploy do Controle de Vencimento (controle_vencimento.py) pra rodar como
serviço systemd persistente na VPS, atrás do nginx em
https://offtrade.duckdns.org/vencimento/.

- Sincroniza controle_vencimento.py + templates/ pra /opt/vencimento
  (diretório próprio, separado do /opt/offtrade-pipeline que é só
  batch/cron, e do /opt/offtrade que é o Streamlit).
- Cria .venv próprio com um requirements.txt mínimo (não precisa de
  streamlit/paramiko/etc do projeto inteiro).
- Gera o .env remoto com OFFTRADE_RUNTIME=vps e ORACLE_LIB do Linux.
- Instala/atualiza o systemd unit vencimento.service (Restart=always).
- Adiciona o location /vencimento/ no nginx (site "offtrade") se ainda
  não existir, e recarrega o nginx.
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
REMOTE_DIR   = "/opt/vencimento"
PORT         = 22

HERE = Path(__file__).parent

APP_FILES = ["controle_vencimento.py"]
TEMPLATE_FILES = ["vencimento_registrar.html", "vencimento_listagem.html", "vencimento_editar.html"]

REQUIREMENTS = "flask\noracledb\nsqlalchemy\npandas\npython-dotenv\nrequests\n"

SYSTEMD_UNIT = f"""[Unit]
Description=Controle de Vencimento (Flask)
After=network.target

[Service]
WorkingDirectory={REMOTE_DIR}
ExecStart={REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/controle_vencimento.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION = """
    location = /vencimento {
        return 301 /vencimento/;
    }

    location /vencimento/ {
        proxy_pass         http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
"""

# /api/ — mesmo serviço Flask (vencimento.service), usado hoje pra
# login.html reportar erro de login sem expor a chave da Evolution API no
# navegador (ver controle_vencimento.py, blueprint api_bp).
NGINX_LOCATION_API = """
    location /api/ {
        proxy_pass         http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 15s;
    }
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

    print("\n-> Criando diretórios remotos...")
    ssh_run(client, f"mkdir -p {REMOTE_DIR}/templates", check=False)

    print("\n-> Sincronizando app...")
    for fname in APP_FILES:
        sftp.put(str(HERE / fname), f"{REMOTE_DIR}/{fname}")
        print(f"   {fname}")
    for fname in TEMPLATE_FILES:
        sftp.put(str(HERE / "templates" / fname), f"{REMOTE_DIR}/templates/{fname}")
        print(f"   templates/{fname}")

    print("\n-> Gravando requirements.txt e .env remotos...")
    sftp.putfo(io.BytesIO(REQUIREMENTS.encode()), f"{REMOTE_DIR}/requirements.txt")
    env_local = (HERE / ".env").read_text(encoding="utf-8")
    env_remoto = build_remote_env(env_local)
    sftp.putfo(io.BytesIO(env_remoto.encode()), f"{REMOTE_DIR}/.env")

    print(f"\n-> Instalando dependências em {REMOTE_DIR}/.venv...")
    ssh_run(client, f"test -d {REMOTE_DIR}/.venv || python3 -m venv {REMOTE_DIR}/.venv", check=False)
    ssh_run(client, f"{REMOTE_DIR}/.venv/bin/pip install -q -r {REMOTE_DIR}/requirements.txt")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/vencimento.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable vencimento.service", check=False)
    ssh_run(client, "systemctl restart vencimento.service")

    print("\n-> Conferindo nginx (adiciona locations /vencimento/ e /api/ se faltando)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    conf_novo = conf_atual
    mudou = False
    if "/vencimento/" not in conf_novo:
        conf_novo = conf_novo.replace(marker, NGINX_LOCATION + "\n" + marker, 1)
        print("   location /vencimento/ adicionada.")
        mudou = True
    if "location /api/" not in conf_novo:
        conf_novo = conf_novo.replace(marker, NGINX_LOCATION_API + "\n" + marker, 1)
        print("   location /api/ adicionada.")
        mudou = True
    if mudou:
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active vencimento.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:5050/vencimento/", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/vencimento/")


if __name__ == "__main__":
    deploy()
