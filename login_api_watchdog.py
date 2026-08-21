"""
Watchdog do login-api.service — roda via cron a cada poucos minutos na VPS.

O login_api.py (Flask, valida a senha do vendedor direto no Oracle) já teve
um caso real (2026-08-19) de ficar travado numa conexão Oracle morta
(ORA-03113/DPY-4011 reaproveitada pelo connection pool) — toda tentativa de
login por senha ficava pendurada até estourar 504 no nginx, sem o processo
crashar (então o Restart=always do systemd nunca disparava sozinho). Só foi
notado porque um vendedor (Angelo, RCA 153) reportou não conseguir entrar.

Esse watchdog chama o próprio endpoint (localhost:5051, sem passar pelo
nginx) com um RCA inexistente e timeout curto — isso já exercita a mesma
consulta às 4 bases Oracle que trava no incidente real, sem depender de
credencial de ninguém. Se não responder a tempo, reinicia o serviço via
systemd e avisa por WhatsApp.
"""
import subprocess
import sys
from pathlib import Path

import requests

BASE = Path(__file__).parent
TIMEOUT_SEG = 10
SERVICE = "login-api.service"
ALERTA_NUMERO = "5521992085320"


def _alertar(msg):
    try:
        sys.path.insert(0, str(BASE))
        from whatsapp_evolution import enviar_whatsapp
        enviar_whatsapp(ALERTA_NUMERO, msg)
    except Exception:
        pass


def _login_api_saudavel() -> bool:
    try:
        resp = requests.post(
            "http://localhost:5051/api/auth/login-vendedor",
            json={"rca": "0", "email": "watchdog@local", "senha": "x"},
            timeout=TIMEOUT_SEG,
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    if _login_api_saudavel():
        return

    print(f"[WATCHDOG] {SERVICE} não respondeu em {TIMEOUT_SEG}s — reiniciando.")
    subprocess.run(["systemctl", "restart", SERVICE], check=False)
    _alertar(
        "⚠️ Login de vendedor (login-api) estava travado — "
        "serviço reiniciado automaticamente pelo watchdog."
    )
    print(f"[WATCHDOG] {SERVICE} reiniciado.")


if __name__ == "__main__":
    main()
