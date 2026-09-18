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

Além do texto, anexa a própria planilha (pedido do usuário em 2026-09-18,
"mandar a mensagem e o arquivo") — baixada do Drive via
baixar_planilhas_drive.py, porque o caminho local sincronizado
("G:\\Drives compartilhados\\...") não existe na VPS. Falha ao baixar/enviar
uma planilha não impede o envio das outras nem da mensagem de texto.
"""
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from whatsapp_evolution import enviar_whatsapp, enviar_whatsapp_documento
from baixar_planilhas_drive import baixar_arquivo

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

def montar_mensagem(planilhas):
    linhas = ["*Pedido de contagem de estoque* 📋", ""]
    if len(planilhas) == 1:
        linhas.append(f"Podem atualizar a contagem da planilha *{planilhas[0]}*?")
    else:
        linhas.append("Podem atualizar a contagem das planilhas abaixo?")
        linhas.extend(f"• *{p}*" for p in planilhas)
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

    for nome in planilhas:
        try:
            caminho = baixar_arquivo(nome)
        except Exception as e:
            print(f"[AVISO] '{nome}' indisponível no Drive ({str(e)[:150]}) — planilha não anexada.")
            continue
        resp_arquivo = enviar_whatsapp_documento(GROUP_JID, str(caminho), legenda=nome, instancia=INSTANCIA)
        if resp_arquivo.status_code < 300:
            print(f"OK - planilha anexada: {nome}")
        else:
            print(f"[AVISO] falha ao anexar '{nome}': {resp_arquivo.status_code} - {resp_arquivo.text[:200]}")


if __name__ == "__main__":
    main()
