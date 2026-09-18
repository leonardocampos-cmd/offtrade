"""
Lembrete semanal via WhatsApp pedindo a contagem de estoque de planilhas
específicas do Drive — pedido do usuário em 2026-09-18.

Roda TODO DIA às 9h (cron na VPS), mas só envia mensagem nos dias com
planilha(s) mapeada(s) em CRONOGRAMA — segunda, quinta e sexta têm horário
igual (9h), então um único cron diário resolve; a lógica de "qual dia pede
o quê" fica toda aqui dentro em vez de três cron jobs separados.

Envia pro grupo "Estoque RJ - Rigarr" (mesmo grupo que exportacao_estoque_whatsapp.py
lê), pela instância dedicada "estoque" da Evolution API — via GROUP_JID (o
@g.us estável; o segundo id @lid só aparece em mensagens recebidas, não é
usado aqui pra enviar). Instância "estoque" só existe na Evolution da VPS
(mesma observação de exportacao_estoque_whatsapp.py) — por isso, como aquele
script, este roda com cron próprio na VPS, fora do main.py.
"""
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from whatsapp_evolution import enviar_whatsapp

INSTANCIA = "estoque"
GROUP_JID = "120363021573739336@g.us"

# weekday(): segunda=0 ... domingo=6
CRONOGRAMA = {
    0: [  # segunda
        "Pinati - ESTOQUE.xlsx",
        "RECKITT - ESTOQUE.xlsx",
    ],
    3: [  # quinta
        "ROBINSON CRUSOE ESTOQUE.xlsx",
    ],
    4: [  # sexta
        "ESTOQUE TIAL.xlsx",
    ],
}

PASTA = "Off Trade/Estoque"


def montar_mensagem(planilhas):
    linhas = ["*Pedido de contagem de estoque* 📋", ""]
    if len(planilhas) == 1:
        linhas.append(f"Podem atualizar a contagem da planilha *{planilhas[0]}*?")
    else:
        linhas.append("Podem atualizar a contagem das planilhas abaixo?")
        linhas.extend(f"• *{p}*" for p in planilhas)
    linhas.append("")
    linhas.append(f"_Pasta: {PASTA}_")
    return "\n".join(linhas)


def main():
    hoje = date.today()
    planilhas = CRONOGRAMA.get(hoje.weekday())

    if not planilhas:
        print(f"OK - {hoje.strftime('%d/%m/%Y')} ({hoje.strftime('%A')}) sem planilha agendada, nada a enviar.")
        return

    mensagem = montar_mensagem(planilhas)
    resp = enviar_whatsapp(GROUP_JID, mensagem, instancia=INSTANCIA)

    if resp.status_code < 300:
        print(f"OK - pedido de contagem enviado ({', '.join(planilhas)})")
    else:
        print(f"[AVISO] falha ao enviar pedido de contagem: {resp.status_code} - {resp.text[:200]}")


if __name__ == "__main__":
    main()
