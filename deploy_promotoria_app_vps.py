"""
Deploy do App de Promotoria (promotoria_app_api.py + promotoria_app.html) como
serviço systemd na VPS, atrás do nginx em https://offtrade.duckdns.org/api/promo-app/.

Mesmo desenho de deploy_metas_builder_vps.py: roda DENTRO de /opt/offtrade-pipeline
(reusa .venv, .env e credenciais Oracle já lá). Diferenças:
  - dados do app (SQLite + fotos) ficam em /opt/promo-app-data, FORA do pipeline,
    pra deploy_pipeline_vps.py nunca tocar neles;
  - garante PROMO_GESTOR_SENHA no .env da VPS (copia do .env local se existir);
  - nginx com client_max_body_size maior (upload de foto).
Não sobe pro git nem mexe em outros serviços.
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
DATA_DIR     = "/opt/promo-app-data"
PORT         = 22
HERE         = Path(__file__).parent
APP_FILES    = ["promotoria_app_api.py", "promotoria_app.html"]

SYSTEMD_UNIT = f"""[Unit]
Description=App de Promotoria - API (Flask)
After=network.target

[Service]
WorkingDirectory={PIPELINE_DIR}
Environment=OFFTRADE_RUNTIME=vps
Environment=PROMO_DATA_DIR={DATA_DIR}
ExecStart={PIPELINE_DIR}/.venv/bin/python {PIPELINE_DIR}/promotoria_app_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION = """
    location /api/promo-app/ {
        proxy_pass         http://127.0.0.1:5059;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 25m;   # fotos de celular
        proxy_read_timeout 60s;
    }
"""


def ssh_run(client, cmd, check=True):
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out, err = stdout.read().decode(errors="replace").strip(), stderr.read().decode(errors="replace").strip()
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
    # look_for_keys/allow_agent=False: chave em cache disputa a auth antes da senha (falha intermitente)
    client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20,
                   look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()

    print("\n-> Sincronizando arquivos...")
    for fname in APP_FILES:
        sftp.put(str(HERE / fname), f"{PIPELINE_DIR}/{fname}")
        print(f"   {fname}")
    ssh_run(client, f"mkdir -p {DATA_DIR}")

    print("\n-> Garantindo PROMO_GESTOR_SENHA no .env da VPS...")
    senha = os.getenv("PROMO_GESTOR_SENHA", "")
    _, tem = ssh_run(client, f"grep -q '^PROMO_GESTOR_SENHA=' {PIPELINE_DIR}/.env", check=False)
    if tem == 0:
        print("   já existe.")
    elif senha:
        ssh_run(client, f"printf '\nPROMO_GESTOR_SENHA=%s\n' '{senha}' >> {PIPELINE_DIR}/.env")
        print("   adicionada (mesmo valor do .env local).")
    else:
        raise RuntimeError("PROMO_GESTOR_SENHA ausente no .env local — defina antes do deploy.")

    print("\n-> Instalando serviço systemd...")
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/promo-app-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable promo-app-api.service", check=False)
    ssh_run(client, "systemctl restart promo-app-api.service")

    print("\n-> Conferindo nginx (location /api/promo-app/)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    bloco = re.search(r"    location /api/promo-app/ \{.*?\n    \}\n", conf_atual, re.DOTALL)
    if bloco:
        conf_novo = conf_atual[:bloco.start()] + NGINX_LOCATION.lstrip("\n") + conf_atual[bloco.end():]
    else:
        conf_novo = conf_atual.replace("    location / {", NGINX_LOCATION + "\n    location / {", 1)
    if conf_novo != conf_atual:
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location atualizada.")
    else:
        print("   já configurado.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 2; systemctl is-active promo-app-api.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:5059/api/promo-app/manifest.webmanifest", check=False)
    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/promo-app/")


if __name__ == "__main__":
    deploy()
