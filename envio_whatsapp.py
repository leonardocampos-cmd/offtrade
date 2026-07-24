#ENVIO WHATSAPP - ABAIXO DA TABELA (Evolution API)
import json
import os
from datetime import datetime
from conferencia_preco import df
from whatsapp_evolution import enviar_whatsapp

NUMERO = "5521974972433"                 # <- número destino (DDI+DDD, sem + ou espaços)

REGISTRO_JSON = "pedidos_enviados.json"

# Carrega pedidos já enviados
pedidos_enviados = set()
if os.path.exists(REGISTRO_JSON) and os.path.getsize(REGISTRO_JSON) > 0:
    with open(REGISTRO_JSON, "r", encoding="utf-8") as f:
        pedidos_enviados = set(json.load(f))

# Filtra apenas pedidos abaixo da tabela ainda não enviados
df_abaixo = df[df['STATUS_CONFERENCIA'] == 'ABAIXO DA TABELA'].copy()
df_abaixo['_chave'] = df_abaixo['NUMPED'].astype(str) + "_" + df_abaixo['CODPROD'].astype(str)
df_novos = df_abaixo[~df_abaixo['_chave'].isin(pedidos_enviados)]

if df_novos.empty:
    print("Nenhum pedido novo abaixo da tabela. Nada enviado.")
else:
    hoje_str = datetime.today().strftime('%d/%m/%Y')
    linhas = [f"⚠️ *CONFERÊNCIA DE PREÇOS - {hoje_str}*",
              f"Pedidos vendidos ABAIXO DA TABELA:\n"]

    for _, row in df_novos.iterrows():
        linhas.append(
            f"• *Pedido:* {row['NUMPED']}\n"
            f"  *Vendedor:* {row['NOME']}\n"
            f"  *Cliente:* {row['CLIENTE']}\n"
            f"  *Produto:* {row['DESCRICAO']}\n"
            f"  *Preço Vendido:* R$ {float(row['PVENDA']):.2f}\n"
            f"  *Menor Ref.:* R$ {float(row['MENOR_VALOR']):.2f}\n"
            f"  *Margem:* {row['MARGEM']}\n"
        )

    mensagem = "\n".join(linhas)

    response = enviar_whatsapp(NUMERO, mensagem)

    if response.status_code in (200, 201):
        # Registra os pedidos enviados no JSON
        pedidos_enviados.update(df_novos['_chave'].tolist())
        with open(REGISTRO_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(pedidos_enviados), f, ensure_ascii=False, indent=2)
        print(f"Mensagem enviada para {NUMERO} com {len(df_novos)} pedido(s) abaixo da tabela.")
        print(f"Registro salvo em '{REGISTRO_JSON}' ({len(pedidos_enviados)} chave(s) no total).")
    else:
        print(f"Erro ao enviar: {response.status_code} - {response.text}")