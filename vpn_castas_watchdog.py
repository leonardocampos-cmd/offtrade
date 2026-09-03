"""
Watchdog da VPN da CASTAS — roda via cron a cada poucos minutos na VPS.

A VPN "redundancia-sp" (L2TP sobre IPsec, strongswan-starter + xl2tpd/pppd,
interface ppp0) é o único caminho pra CASTAS (10.131.62.0/24) — ver memória
do projeto "banco CASTAS: limitação de rede". Já caiu duas vezes (2026-08-28
e 2026-08-31) com o mesmo sintoma: strongswan-starter/xl2tpd continuam
"active" no systemd (então Restart=always nunca dispara sozinho), mas o
pppd fica em loop falhando "Failed to open /dev/pts/N: No such file or
directory" (devpts sem pty numerado alocado) e o ppp0/rota pra CASTAS nunca
sobe. Sem isso, CASTAS só voltava a atualizar quando alguém notava o alerta
de fonte fora do ar e reiniciava manualmente.

Esse watchdog só checa se a interface ppp0 existe (é exatamente o que falta
no incidente real — não precisa nem tentar alcançar o Oracle da CASTAS). Se
não existir, reinicia strongswan-starter + xl2tpd (mesma ordem que resolveu
manualmente nas duas vezes) e avisa por WhatsApp. Causa raiz do devpts
quebrado continua sem investigar — isso só contorna, igual da vez passada.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
ALERTA_NUMERO = "5521992085320"
ESTADO_PATH = BASE / ".vpn_castas_watchdog_state.json"
# Sem isso, uma queda prolongada do lado REMOTO (fora do nosso controle,
# nenhum restart local resolve) mandava um WhatsApp idêntico a cada 5 min
# pra sempre — achado real em 2026-09-03: ~16 alertas repetidos numa queda
# de mais de 1h (200.246.200.166 inalcançável, watchdog tentando à toa).
COOLDOWN_ALERTA_FALHA = timedelta(hours=1)


def _ler_estado():
    try:
        return json.loads(ESTADO_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _salvar_estado(estado):
    try:
        ESTADO_PATH.write_text(json.dumps(estado), encoding='utf-8')
    except Exception:
        pass


def _alertar(msg):
    try:
        sys.path.insert(0, str(BASE))
        from whatsapp_evolution import enviar_whatsapp
        enviar_whatsapp(ALERTA_NUMERO, msg)
    except Exception:
        pass


def _ppp0_existe() -> bool:
    return subprocess.run(
        ["ip", "addr", "show", "ppp0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def main():
    if _ppp0_existe():
        return

    print("[WATCHDOG] ppp0 (VPN redundancia-sp/CASTAS) não existe — reiniciando strongswan-starter + xl2tpd.")
    subprocess.run(["systemctl", "restart", "strongswan-starter"], check=False)
    subprocess.run(["systemctl", "restart", "xl2tpd"], check=False)
    time.sleep(6)

    if _ppp0_existe():
        print("[WATCHDOG] ppp0 voltou.")
        _alertar("⚠️ VPN da CASTAS (redundancia-sp) tinha caído — reiniciada automaticamente pelo watchdog.")
        # Reseta o cooldown — uma queda FUTURA (mesmo daqui a poucos minutos)
        # deve alertar de novo na hora, não ficar presa ao cooldown desta.
        _salvar_estado({})
    else:
        estado = _ler_estado()
        ultimo = estado.get('ultimo_alerta_falha')
        agora = datetime.now()
        pode_alertar = not ultimo or (agora - datetime.fromisoformat(ultimo)) >= COOLDOWN_ALERTA_FALHA
        if pode_alertar:
            print("[WATCHDOG] ppp0 continua ausente após restart — precisa de investigação manual.")
            _alertar("🔴 VPN da CASTAS (redundancia-sp) caiu e o watchdog NÃO conseguiu restabelecer sozinho — precisa checar manualmente.")
            _salvar_estado({'ultimo_alerta_falha': agora.isoformat()})
        else:
            print("[WATCHDOG] ppp0 continua ausente após restart — dentro do cooldown de alerta, não reenviando WhatsApp.")


if __name__ == "__main__":
    main()
