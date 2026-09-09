"""
Deploy do backend de Oportunidades de Vendas (Maps) (oportunidades_maps_api.py)
pra rodar como serviço systemd na VPS, atrás do nginx em
https://offtrade.duckdns.org/api/oportunidades-maps/.

Mesmo padrão de deploy_raiox_cliente_api_vps.py: roda DENTRO de
/opt/offtrade-pipeline (usa o .venv já com oracledb/sqlalchemy/pandas/flask/
requests/ORACLE_LIB configurado, ver deploy_pipeline_vps.py). Rodar este
script depois de deploy_pipeline_vps.py (que já sincroniza
oportunidades_maps_api.py, ver PIPELINE_FILES) — aqui só falta o systemd
unit + a location do nginx.
"""
import io
import os
import re
from pathlib import Path

import paramiko
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
PIPELINE_DIR = "/opt/offtrade-pipeline"
PORT         = 22

HERE = Path(__file__).parent
APP_FILES = ["oportunidades_maps_api.py"]

SYSTEMD_UNIT = f"""[Unit]
Description=Oportunidades de Vendas (Maps) - busca sob demanda (Flask)
After=network.target

[Service]
WorkingDirectory={PIPELINE_DIR}
ExecStart={PIPELINE_DIR}/.venv/bin/python {PIPELINE_DIR}/oportunidades_maps_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION = """
    location /api/oportunidades-maps/ {
        proxy_pass         http://127.0.0.1:5060;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
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


def deploy():
    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    sftp = client.open_sftp()

    print("\n-> Sincronizando oportunidades_maps_api.py em /opt/offtrade-pipeline...")
    for fname in APP_FILES:
        sftp.put(str(HERE / fname), f"{PIPELINE_DIR}/{fname}")
        print(f"   {fname}")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/oportunidades-maps-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable oportunidades-maps-api.service", check=False)
    ssh_run(client, "systemctl restart oportunidades-maps-api.service")

    print("\n-> Conferindo nginx (location /api/oportunidades-maps/)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    bloco_existente = re.search(r"    location /api/oportunidades-maps/ \{.*?\n    \}\n", conf_atual, re.DOTALL)
    if bloco_existente:
        conf_novo = conf_atual[:bloco_existente.start()] + NGINX_LOCATION.lstrip("\n") + conf_atual[bloco_existente.end():]
    else:
        conf_novo = conf_atual.replace(marker, NGINX_LOCATION + "\n" + marker, 1)
    if conf_novo != conf_atual:
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location /api/oportunidades-maps/ atualizada.")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active oportunidades-maps-api.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' 'http://127.0.0.1:5060/api/oportunidades-maps/buscar?cidade=Niteroi'", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/oportunidades-maps/buscar?cidade=Niteroi")


if __name__ == "__main__":
    deploy()
