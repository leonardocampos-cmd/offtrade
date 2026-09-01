"""
Deploy do backend de Pedidos Mercos (pedidos_mercos_api.py) pra rodar como
serviço systemd persistente na VPS, atrás do nginx em
https://offtrade.duckdns.org/api/pedidos-mercos/.

A página em si (pedidos_mercos.html) é estática e vai pelo deploy_static_vps.py
normal, junto com o resto do site — aqui só sobe a API que resolve o
telefone do vendedor (cadastro de colaborador da Mercos) e retransmite o
envio pra Z-API (com o token que cada usuário logado guarda no próprio
navegador — nunca persistido aqui).

- Sincroniza pedidos_mercos_api.py + mercos_api.py + whatsapp_evolution.py
  pra /opt/pedidos-mercos-api.
- Cria .venv próprio com um requirements.txt mínimo (sem Oracle — essa API
  não consulta banco nenhum).
- Gera o .env remoto com OFFTRADE_RUNTIME=vps.
- Instala/atualiza o systemd unit pedidos-mercos-api.service (Restart=always).
- Adiciona o location /api/pedidos-mercos/ no nginx (site "offtrade") se
  ainda não existir, e recarrega o nginx.
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
REMOTE_DIR   = "/opt/pedidos-mercos-api"
PORT         = 22

HERE = Path(__file__).parent

APP_FILES = ["pedidos_mercos_api.py", "mercos_api.py"]

REQUIREMENTS = "flask\nrequests\npython-dotenv\n"

SYSTEMD_UNIT = f"""[Unit]
Description=Pedidos Mercos - API (Flask)
After=network.target

[Service]
WorkingDirectory={REMOTE_DIR}
ExecStart={REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/pedidos_mercos_api.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_LOCATION = """
    location /api/pedidos-mercos/ {
        proxy_pass         http://127.0.0.1:5056;
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


def build_remote_env(local_env_text: str) -> str:
    linhas = local_env_text.splitlines()
    linhas = [l for l in linhas if not re.match(r"^\s*OFFTRADE_RUNTIME\s*=", l)]
    linhas.append("OFFTRADE_RUNTIME=vps")
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
    sftp.putfo(io.BytesIO(SYSTEMD_UNIT.encode()), "/etc/systemd/system/pedidos-mercos-api.service")
    ssh_run(client, "systemctl daemon-reload")
    ssh_run(client, "systemctl enable pedidos-mercos-api.service", check=False)
    ssh_run(client, "systemctl restart pedidos-mercos-api.service")

    print("\n-> Conferindo nginx (adiciona location /api/pedidos-mercos/ se faltando)...")
    nginx_conf = "/etc/nginx/sites-available/offtrade"
    with sftp.open(nginx_conf) as f:
        conf_atual = f.read().decode("utf-8")
    marker = "    location / {"
    if "location /api/pedidos-mercos/" not in conf_atual:
        conf_novo = conf_atual.replace(marker, NGINX_LOCATION + "\n" + marker, 1)
        sftp.putfo(io.BytesIO(conf_novo.encode()), nginx_conf)
        ssh_run(client, "nginx -t")
        ssh_run(client, "systemctl reload nginx")
        print("   location /api/pedidos-mercos/ adicionada.")
    else:
        print("   já configurado, nada a fazer.")

    print("\n-> Verificando serviço...")
    ssh_run(client, "sleep 1; systemctl is-active pedidos-mercos-api.service", check=False)
    ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:5056/api/pedidos-mercos/enviar-whatsapp", check=False)

    sftp.close()
    client.close()
    print("\nOK -> https://offtrade.duckdns.org/api/pedidos-mercos/enviar-whatsapp")


if __name__ == "__main__":
    deploy()
