"""
Deploy do backend de Construção de Metas (metas_builder_api.py) pra rodar
como serviço systemd na VPS, atrás do nginx em
https://offtrade.duckdns.org/api/metas-builder/.

Ao contrário de pedidos_mercos_api.py (venv leve, própria pasta
/opt/pedidos-mercos-api), este serviço roda DENTRO de /opt/offtrade-pipeline
— usa o mesmo .venv (já tem oracledb/sqlalchemy/pandas/flask/ORACLE_LIB
configurado, ver deploy_pipeline_vps.py) e as mesmas credenciais do Google
Drive (token.json, pra ler METAS RJ.xlsx via baixar_planilhas_drive.py) já
sincronizadas lá. Rodar este script depois de deploy_pipeline_vps.py (que
já sincroniza metas_builder_api.py/metas_builder.html, ver PIPELINE_FILES) —
aqui só falta o systemd unit + a location do nginx.
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
APP_FILES = ["metas_builder_api.py"]

SYSTEMD_UNIT = f"""[Unit]
Description=Construcao de Metas - API (Flask)
After=network.target

[Service]
WorkingDirectory={PIPELINE_DIR}
ExecStart={PIPELINE_DIR}/.venv/bin/python {PIPELINE_DIR}/metas_builder_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION = """
    location /api/metas-builder/ {
        proxy_pass         http://127.0.0.1:5057;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        # /calcular consulta Oracle (historico + cobertura de base) pra
        # varios RCAs de uma vez — folga acima do default de 60s.
        proxy_read_timeout 90s;
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

    print("\n-> Sincronizando metas_builder_api.py em /opt/offtrade-pipeline (garantia, caso deploy_pipeline_vps.py não tenha rodado ainda)...")
    for fname in APP_FILES:
        sftp.put(str(HERE / fname), f"{PIPELINE_DIR}/{fname}")
        print(f"   {fname}")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/metas-builder-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable metas-builder-api.service", check=False)
    ssh_run(client, "systemctl restart metas-builder-api.service")

    print("\n-> Conferindo nginx (location /api/metas-builder/)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    bloco_existente = re.search(r"    location /api/metas-builder/ \{.*?\n    \}\n", conf_atual, re.DOTALL)
    if bloco_existente:
        conf_novo = conf_atual[:bloco_existente.start()] + NGINX_LOCATION.lstrip("\n") + conf_atual[bloco_existente.end():]
    else:
        conf_novo = conf_atual.replace(marker, NGINX_LOCATION + "\n" + marker, 1)
    if conf_novo != conf_atual:
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location /api/metas-builder/ atualizada.")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active metas-builder-api.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' 'http://127.0.0.1:5057/api/metas-builder/rcas?estado=RJ'", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/metas-builder/rcas?estado=RJ")


if __name__ == "__main__":
    deploy()
