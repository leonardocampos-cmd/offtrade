#ENVIO WHATSAPP - ABAIXO DA TABELA (Evolution API)
import requests
from datetime import datetime
from conferencia_preco import df

# Configurações da Evolution API
EVOLUTION_URL = "http://localhost:8083"
EVOLUTION_KEY = "429683C4C977415CAAFCCE10F7D57E11"
INSTANCE      = "bees"                          # <- nome da instância criada no painel
NUMERO        = "120363420862939914"                 # <- número destino (DDI+DDD, sem + ou espaços)

# Filtra apenas pedidos abaixo da tabela
df_abaixo = df[df['STATUS_CONFERENCIA'] == 'ABAIXO DA TABELA'].copy()

if df_abaixo.empty:
    print("Nenhum pedido abaixo da tabela hoje. Nada enviado.")
else:
    hoje_str = datetime.today().strftime('%d/%m/%Y')
    linhas = [f"⚠️ *CONFERÊNCIA DE PREÇOS - {hoje_str}*",
              f"Pedidos vendidos ABAIXO DA TABELA:\n"]

    for _, row in df_abaixo.iterrows():
        linhas.append(
            f"• *Pedido:* {row['NUMPED']}\n"
            f"  *Vendedor:* {row['NOME']}\n"
            f"  *Cliente:* {row['CLIENTE']}\n"
            f"  *Produto:* {row['DESCRICAO']}\n"
            f"  *Preço Vendido:* R$ {float(row['PVENDA']):.2f}\n"
            f"  *Menor Ref.:* R$ {float(row['MENOR_VALOR']):.2f}\n"
            f"  *Diferença:* -{row['PORCENTAGEM DIFERENÇA']}\n"
        )

    mensagem = "\n".join(linhas)

    url = f"{EVOLUTION_URL}/message/sendText/{INSTANCE}"
    headers = {
        "apikey": EVOLUTION_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": NUMERO,
        "text": mensagem
    }

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code in (200, 201):
        print(f"Mensagem enviada para {NUMERO} com {len(df_abaixo)} pedido(s) abaixo da tabela.")
    else:
        print(f"Erro ao enviar: {response.status_code} - {response.text}")