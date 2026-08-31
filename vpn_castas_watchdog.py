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
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
ALERTA_NUMERO = "5521992085320"


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
    else:
        print("[WATCHDOG] ppp0 continua ausente após restart — precisa de investigação manual.")
        _alertar("🔴 VPN da CASTAS (redundancia-sp) caiu e o watchdog NÃO conseguiu restabelecer sozinho — precisa checar manualmente.")


if __name__ == "__main__":
    main()
