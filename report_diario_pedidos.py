"""
Report diário por e-mail da página de pedidos (https://offtrade.duckdns.org/pedidos.html)
— anexa DUAS planilhas .xlsx, uma por estado (RJ e SP), cada uma com as 4
abas da página (Pedidos Feitos, Faturados, Cortados/Cancelados, Produtos
Cortados). Cada aba replica exatamente a mesma linha/coluna do botão
"Exportar CSV" de pedidos.html (uma linha por item do pedido, não por
pedido — ver ABAS/exportarCSV em pedidos.html).

Autenticação: mesmo token_gmail.json (OAuth, escopo gmail.modify) já usado
por email_pedidos.py / alerta_logistica_rj.py pra ler e-mails — esse escopo
também autoriza o endpoint de envio (users.messages.send).

Só RJ e SP, um arquivo por estado (campo 'estado' de cada pedido/item) —
pedido explícito do usuário em 2026-08-10, mesmo dia em que pediu pra rodar
isso na VPS em vez de local.

Usuário testou e confirmou em 2026-08-10 (recebeu no próprio e-mail) —
TEST_MODE=False, envia pra lista real desde então.
"""
import base64
import json
import re
import io
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE        = Path(__file__).parent
TOKEN_GMAIL = BASE / "token_gmail.json"
PEDIDOS_JS  = BASE / "pedidos_data.js"
ESTOQUE_JS  = BASE / "estoque_data.js"
SCOPES      = ["https://www.googleapis.com/auth/gmail.modify"]

PAGINA_URL = "https://offtrade.duckdns.org/pedidos.html"

ESTADOS_INCLUIDOS = ['RJ', 'SP']

TEST_MODE   = False
EMAIL_TESTE = "leonardo.campos@rigarr.com.br"
DESTINATARIOS = [
    "giovani.cabral@rigarr.com.br",
    "allan.correa@rigarr.com.br",
    "marcus.tanamachi@rigarr.com.br",
    "daniel.diniz@rigarr.com.br",
]
COPIA = "offtrade@rigarr.com.br, danielle.soares@rigarr.com.br"

# (chave do payload, campo extra, label do campo extra, nome da aba)
_ABAS_PEDIDOS = [
    ('pedidos_feitos', 'sistema',    'Sistema',           'Pedidos Feitos'),
    ('faturados',       'status_log', 'Status Logística',  'Faturados'),
    ('cancelados',       'motivo',    'Motivo',            'Cortados-Cancelados'),
]


def _carregar_js(path, var_name):
    raw = path.read_text(encoding='utf-8')
    m = re.search(rf'{var_name}\s*=\s*(\{{.*\}});?\s*$', raw.strip(), re.DOTALL)
    if not m:
        raise RuntimeError(f"Não achei {var_name} em {path.name}")
    return json.loads(m.group(1))


def _linhas_pedidos(pedidos, campo_extra, label_extra):
    linhas = []
    for p in pedidos:
        itens = p.get('itens') or [{'desc': '', 'qt': '', 'val': ''}]
        for it in itens:
            linhas.append({
                'Pedido':        p.get('numped'),
                'NF':            p.get('numnota'),
                'Data':          p.get('data'),
                'Vendedor':      (p.get('nome') or '').replace(' - OFF TRADE', ''),
                'Estado':        p.get('estado'),
                'Cliente':       p.get('cliente'),
                label_extra:     p.get(campo_extra),
                'Produto':       it.get('desc'),
                'Qtd':           it.get('qt'),
                'Valor Produto': it.get('val'),
                'Total Pedido':  p.get('total'),
            })
    return pd.DataFrame(linhas)


def _indice_estoque():
    if not ESTOQUE_JS.exists():
        return {}
    idx = {}
    for p in _carregar_js(ESTOQUE_JS, 'ESTOQUE_DATA').get('produtos', []):
        try:
            fil, prod = int(p['codfilial']), int(p['codprod'])
        except (TypeError, ValueError, KeyError):
            continue
        idx[(str(p.get('empresa') or '').upper(), fil, prod)] = p.get('qtestoque')
    return idx


def _linhas_produtos_cortados(produtos, estoque_idx):
    linhas = []
    for p in produtos:
        try:
            fil, prod = int(p.get('codfilial')), int(p.get('codprod'))
            saldo = estoque_idx.get((str(p.get('sistema') or '').upper(), fil, prod))
        except (TypeError, ValueError):
            saldo = None
        linhas.append({
            'Data':          p.get('data'),
            'Vendedor':      (p.get('nome') or '').replace(' - OFF TRADE', ''),
            'Estado':        p.get('estado'),
            'Cliente':       p.get('cliente'),
            'Pedido':        p.get('numped'),
            'Produto':       p.get('desc'),
            'Indústria':     p.get('industria'),
            'Qtd Cortada':   p.get('qtd_cortada_total'),
            'Qtd Pedida':    p.get('qt_original'),
            'Valor Cortado': p.get('valor_cortado'),
            'Saldo Estoque': saldo,
        })
    return pd.DataFrame(linhas)


def _filtrar_estado(lista, estado):
    return [p for p in lista if (p.get('estado') or '').strip().upper() == estado]


def _resumo(tabelas_pedidos, cortados_filtrados):
    """tabelas_pedidos: lista de (aba, pedidos_filtrados) — um item por aba de
    pedido (Pedidos Feitos/Faturados/Cortados-Cancelados). Conta e soma por
    pedido (não por item, senão duplicaria valor entre os produtos)."""
    linhas = []
    for aba, pedidos in tabelas_pedidos:
        if aba == 'Cortados-Cancelados':
            # 'total' do pedido é sempre 0 aqui (nada foi faturado) — o valor
            # que importa é o cortado, somado a partir dos itens.
            valor = sum(it.get('valor_cortado') or 0 for p in pedidos for it in (p.get('itens') or []))
            label_valor = f'{aba} — Valor Cortado'
        else:
            valor = sum(p.get('total') or 0 for p in pedidos)
            label_valor = f'{aba} — Valor Total'
        linhas.append({'Métrica': f'{aba} — Qtd Pedidos', 'Valor': len(pedidos)})
        linhas.append({'Métrica': label_valor, 'Valor': round(valor, 2)})
    linhas.append({'Métrica': 'Produtos Cortados — Qtd Itens', 'Valor': len(cortados_filtrados)})
    linhas.append({
        'Métrica': 'Produtos Cortados — Qtd Total Cortada',
        'Valor': sum(p.get('qtd_cortada_total') or 0 for p in cortados_filtrados),
    })
    linhas.append({
        'Métrica': 'Produtos Cortados — Valor Total Cortado',
        'Valor': round(sum(p.get('valor_cortado') or 0 for p in cortados_filtrados), 2),
    })
    return pd.DataFrame(linhas)


def montar_tabelas(payload, estoque_idx, estado):
    """Retorna dict ordenado {nome_aba: DataFrame} — usado tanto pro .xlsx
    (anexo do e-mail) quanto pro .html (preview local), pra não duplicar a
    lógica de filtro/formatação entre os dois formatos."""
    tabelas_pedidos = []
    for chave, campo_extra, label_extra, aba in _ABAS_PEDIDOS:
        pedidos_filtrados = _filtrar_estado(payload.get(chave, []), estado)
        tabelas_pedidos.append((aba, pedidos_filtrados))

    cortados_filtrados = _filtrar_estado(payload.get('produtos_cortados', []), estado)

    tabelas = {'Resumo': _resumo(tabelas_pedidos, cortados_filtrados)}
    for (chave, campo_extra, label_extra, aba), (_, pedidos_filtrados) in zip(_ABAS_PEDIDOS, tabelas_pedidos):
        tabelas[aba] = _linhas_pedidos(pedidos_filtrados, campo_extra, label_extra)
    tabelas['Produtos Cortados'] = _linhas_produtos_cortados(cortados_filtrados, estoque_idx)
    return tabelas


def montar_planilha(tabelas):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        for aba, df in tabelas.items():
            df.to_excel(writer, sheet_name=aba, index=False)
    return buf.getvalue()


def montar_html(tabelas, estado, hoje_str):
    secoes = []
    for aba, df in tabelas.items():
        secoes.append(f"<h2>{aba}</h2>\n" + df.to_html(index=False, na_rep='', border=0))
    corpo = "\n".join(secoes)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Report Diário de Pedidos — {estado} — {hoje_str}</title>
<style>
  body {{ background:#0f1117; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; padding:24px; }}
  h1 {{ color:#3b82f6; font-size:1.3rem; }}
  h2 {{ color:#f5c518; font-size:1rem; margin-top:32px; border-bottom:1px solid #2d3144; padding-bottom:6px; }}
  table {{ border-collapse:collapse; width:100%; font-size:.85rem; margin-top:10px; }}
  th, td {{ padding:6px 10px; border-bottom:1px solid #2d3144; text-align:left; white-space:nowrap; }}
  th {{ background:#22263a; color:#94a3b8; text-transform:uppercase; font-size:.72rem; }}
  tr:hover td {{ background:#1a1d27; }}
</style>
</head>
<body>
<h1>Report Diário de Pedidos — {estado} — {hoje_str}</h1>
{corpo}
</body>
</html>
"""


def _get_service():
    if not TOKEN_GMAIL.exists():
        raise FileNotFoundError(
            "token_gmail.json não encontrado. Execute gmail_setup.py uma vez "
            "para autorizar o acesso ao Gmail."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_GMAIL), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_GMAIL.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Token Gmail inválido ou sem refresh_token. "
                "Rode gmail_setup.py novamente para reautorizar."
            )
    return build("gmail", "v1", credentials=creds)


def enviar_report(service, destinatarios, arquivos, hoje_str, cc=None):
    """arquivos: lista de (estado, xlsx_bytes)."""
    msg = MIMEMultipart()
    msg["Subject"] = f"Report Diário de Pedidos — {hoje_str}"
    msg["To"] = ", ".join(destinatarios)
    if cc:
        msg["Cc"] = cc
    estados_txt = " e ".join(estado for estado, _ in arquivos)
    corpo = (
        f"Segue em anexo o report diário de pedidos ({estados_txt}), um arquivo por "
        "estado, cada um com 5 abas: Resumo, Pedidos Feitos, Faturados, "
        "Cortados-Cancelados e Produtos Cortados.\n\n"
        f"Página completa e sempre atualizada: {PAGINA_URL}\n"
    )
    msg.attach(MIMEText(corpo, "plain"))
    for estado, xlsx_bytes in arquivos:
        anexo = MIMEApplication(xlsx_bytes, _subtype="xlsx")
        nome_arquivo = f"pedidos_{estado}_{hoje_str.replace('/', '-')}.xlsx"
        anexo.add_header("Content-Disposition", "attachment", filename=nome_arquivo)
        msg.attach(anexo)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def gerar_previews(enviar=False):
    """Monta as tabelas, salva os HTMLs de preview localmente (RJ e SP) e,
    só se enviar=True, manda o e-mail de fato com os anexos .xlsx.

    Padrão é enviar=False — pedido do usuário em 2026-08-12 pra sempre
    revisar o conteúdo (via os HTMLs) antes de qualquer envio real."""
    payload = _carregar_js(PEDIDOS_JS, 'PEDIDOS_DATA')
    estoque_idx = _indice_estoque()
    hoje_str = date.today().strftime('%d/%m/%Y')
    hoje_arquivo = hoje_str.replace('/', '-')

    tabelas_por_estado = {
        estado: montar_tabelas(payload, estoque_idx, estado)
        for estado in ESTADOS_INCLUIDOS
    }

    html_paths = []
    for estado, tabelas in tabelas_por_estado.items():
        html_path = BASE / f"report_pedidos_{estado}_{hoje_arquivo}.html"
        html_path.write_text(montar_html(tabelas, estado, hoje_str), encoding='utf-8')
        html_paths.append(html_path)
        print(f"Preview salvo: {html_path}")

    if not enviar:
        print("Envio NÃO disparado (enviar=False) — confira os HTMLs acima antes de rodar com enviar=True.")
        return html_paths

    arquivos = [(estado, montar_planilha(tabelas)) for estado, tabelas in tabelas_por_estado.items()]
    destinatarios = [EMAIL_TESTE] if TEST_MODE else DESTINATARIOS
    cc = None if TEST_MODE else COPIA
    service = _get_service()
    enviar_report(service, destinatarios, arquivos, hoje_str, cc=cc)
    print(f"OK - report enviado para: {', '.join(destinatarios)}"
          f"{f' (cc: {cc})' if cc else ''} ({', '.join(e for e, _ in arquivos)})")
    if TEST_MODE:
        print("[TEST_MODE=True] Lista real (Giovani/Allan/Marcus/Daniel) NÃO foi usada.")
    return html_paths


def main():
    gerar_previews(enviar=True)


if __name__ == "__main__":
    main()
