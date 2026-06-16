"""
Verificacao read-only do status do deploy na VPS (sem alterar nada):
status do servico systemd, ultimas linhas de log e teste HTTP local.
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
SERVICE      = "offtrade"
PORT         = 22


def ssh_run(client, cmd):
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out  = stdout.read().decode(errors="replace").strip()
    err  = stderr.read().decode(errors="replace").strip()
    return out, err, code


def main():
    print(f"-> Conectando à VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    except Exception as e:
        print(f"[ERRO] Não foi possível conectar via SSH: {e}")
        sys.exit(1)

    print("\n=== systemctl status offtrade ===")
    out, err, _ = ssh_run(client, f"systemctl status {SERVICE} --no-pager -l")
    print(out or err)

    print("\n=== últimas 30 linhas de log ===")
    out, err, _ = ssh_run(client, f"journalctl -u {SERVICE} -n 30 --no-pager")
    print(out or err)

    print("\n=== teste HTTP local (porta 8501) ===")
    out, err, _ = ssh_run(client, "curl -s -o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:8501")
    print(out or err)

    print("\n=== uptime / disco ===")
    out, _, _ = ssh_run(client, "uptime && df -h / ")
    print(out)

    client.close()


if __name__ == "__main__":
    main()
