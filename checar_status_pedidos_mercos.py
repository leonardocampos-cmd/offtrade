"""
checar_status_pedidos_mercos.py — aviso automático por WhatsApp quando o
status de um pedido do Mercos muda (faturado/corte/cancelado), rodando no
cron da VPS em vez de no navegador.

Substitui o antigo _checarMudancasStatus() de pedidos_mercos.html (JS
client-side, setInterval de 3min) — pedido do usuário em 2026-09-11: só
funcionava de verdade com a aba aberta em primeiro plano (celular suspende
o timer assim que a aba vai pra segundo plano), então na prática quase
nunca disparava (achado real: vendedora marcou o checkbox, WhatsApp nunca
chegou pro vendedor do pedido).

Deploy: mesmo diretório/venv leve de pedidos_mercos_api.py
(/opt/pedidos-mercos-api, sem Oracle) — ver deploy_pedidos_mercos_api_vps.py.
Cron próprio (5 min), fora do main.py, mesmo padrão de
exportacao_pedidos_bloqueados.py/exportacao_meta.py.

Fluxo:
  1. Lê zapi_config_servidor.json (mesmo dir — gravado por
     pedidos_mercos_api.py::config_zapi, sincronizado da página quando a
     vendedora salva a config no modal ⚙️ WhatsApp). Sem config válida ou
     auto_avisar desligado: encerra sem fazer nada (mesmo opt-in de antes).
  2. Lê pedidos_mercos_data.js do disco (mesmo arquivo que
     gerar_pedidos_mercos_data.py já regrava a cada 30min em
     /opt/offtrade-static — não consulta Oracle de novo aqui).
  3. Compara contra status_visto.json (mesmo dir) — pedido cujo status
     mudou pra um dos que disparam aviso desde a última execução manda
     WhatsApp (texto + PDF resumo quando faturado) via
     http://127.0.0.1:5056/api/pedidos-mercos/enviar-whatsapp (endpoint já
     existente, resolve telefone do vendedor + repassa pra Z-API).
  4. Regrava status_visto.json com o status atual de todo pedido.

Uso: python checar_status_pedidos_mercos.py [caminho_pedidos_mercos_data.js]
Sem argumento, usa /opt/offtrade-static/pedidos_mercos_data.js quando
OFFTRADE_RUNTIME=vps, ou ./pedidos_mercos_data.js (relativo a este
arquivo) fora da VPS — pra rodar contra uma cópia baixada do site sem
precisar estar na VPS.
"""
import json
import os
import re
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

ZAPI_CONFIG_PATH = HERE / "zapi_config_servidor.json"
STATUS_VISTO_PATH = HERE / "status_visto.json"
ENDPOINT_ENVIAR = "http://127.0.0.1:5056/api/pedidos-mercos/enviar-whatsapp"

STATUS_COM_COMPARACAO = {"integral", "corte", "excesso"}
STATUS_COM_AVISO = STATUS_COM_COMPARACAO | {"cancelado", "cancelado_spon"}


def _caminho_pedidos_data():
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    if RUNTIME == "vps":
        return Path("/opt/offtrade-static/pedidos_mercos_data.js")
    return HERE / "pedidos_mercos_data.js"


def _ler_data_js(caminho, nome_var):
    """pedidos_mercos_data.js é "const NOME = {...};" — não é JSON puro
    (pode ter NaN, que só é válido como JS), mesmo formato de todo _data.js
    do projeto. Substitui NaN por null antes do json.loads (mesma técnica
    usada em vários exportacao_*.py pra ler _data.js de volta)."""
    texto = caminho.read_text(encoding="utf-8")
    m = re.search(rf"const\s+{nome_var}\s*=\s*(\{{.*\}});\s*$", texto.strip(), re.DOTALL)
    if not m:
        raise ValueError(f"Não consegui extrair {nome_var} de {caminho}")
    bruto = re.sub(r"\bNaN\b", "null", m.group(1))
    return json.loads(bruto)


def _ler_json_seguro(caminho, default):
    if not caminho.exists():
        return default
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return default


def _gravar_json_atomico(caminho, dados):
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


# ── Formatação (porta de pedidos_mercos.html) ───────────────────────────────

def _fmt_qt(v):
    if v is None:
        return "—"
    return f"{float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".").rstrip("0").rstrip(",")


def _fmt_brl(v):
    v = float(v or 0)
    return "R$ " + f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_cnpj(v):
    if not v:
        return "—"
    s = str(v).zfill(14)
    if len(s) != 14:
        return str(v)
    return f"{s[0:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:14]}"


def _cortados_por_codigo(p):
    return {str(ic["codprod"]): ic for ic in (p.get("itens_cortados") or [])}


def _item_status_texto(p, ic):
    if ic:
        return f"Cortado: {_fmt_qt(ic['qt_cortada'])} un (faturou {_fmt_qt(ic['qt_faturada'])})"
    if p.get("status_spon") in STATUS_COM_COMPARACAO:
        return "OK"
    if p.get("status_spon") == "montado":
        return "Aguardando faturamento"
    if p.get("status_spon") == "cancelado_spon":
        return "Cancelado"
    return "Sem dado no SPON"


def _mensagem_faturamento(p):
    """Porta exata de pedidos_mercos.html::_mensagemFaturamento."""
    nome_vendedor = p.get("vendedor") or p.get("cod_vendedor") or ""
    saudacao = f"Olá {nome_vendedor}, " if nome_vendedor else "Olá, "
    status = p.get("status_spon")
    if status in ("cancelado", "cancelado_spon"):
        motivo = f" Motivo: {p['motivo_cancelamento']}." if p.get("motivo_cancelamento") else ""
        onde = "no sistema (SPON/Winthor)" if status == "cancelado_spon" else "no Mercos"
        return f"{saudacao}o pedido #{p['numped']} foi CANCELADO {onde}.{motivo}"

    linhas = [f"{saudacao}seu pedido #{p['numped']} foi faturado."]
    cortados = _cortados_por_codigo(p)
    itens_cortados = [it for it in (p.get("itens") or []) if str(it["codprod"]) in cortados]
    if itens_cortados:
        linhas += ["", "Houve os seguintes cortes:"]
        for it in itens_cortados:
            ic = cortados[str(it["codprod"])]
            linhas.append(
                f"- {it['descricao']}: cortado {_fmt_qt(ic['qt_cortada'])} un "
                f"(faturou {_fmt_qt(ic['qt_faturada'])} de {_fmt_qt(it['qt'])} pedidos)"
            )
    return "\n".join(linhas)


# fpdf2 usa as fontes-núcleo padrão (Helvetica) com encoding Latin-1 puro —
# não inclui travessão "—"/reticências "…"/aspas curvas, que aparecem em
# texto vindo do Winthor (achado real testando: crashava com "Character
# is outside the range of characters supported by the font"). Normaliza
# pra equivalente ASCII antes de desenhar em vez de embutir uma fonte
# Unicode (manteria o serviço leve, sem fonte extra pra empacotar/deployar).
_PDF_SUBSTITUICOES = {
    "—": "-", "–": "-", "…": "...",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}


def _pdf_safe(texto):
    s = str(texto if texto is not None else "")
    for de, para in _PDF_SUBSTITUICOES.items():
        s = s.replace(de, para)
    return s.encode("latin-1", "replace").decode("latin-1")


def _construir_pdf_pedido(p):
    """Porta de pedidos_mercos.html::_construirPdfPedido (jsPDF) pra fpdf2 —
    mesmo conteúdo (cliente/CNPJ/representada/vendedor + tabela de itens com
    status SPON/corte + total), layout best-effort, não pixel-a-pixel."""
    from fpdf import FPDF

    doc = FPDF(orientation="L", unit="mm", format="A4")
    doc.add_page()
    doc.set_font("Helvetica", "B", 14)
    doc.cell(0, 8, _pdf_safe(f"Pedido #{p['numped']} — {p['data']}"), new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Helvetica", "", 10)
    for linha in [
        f"Cliente: {p.get('cliente', '')}",
        f"CNPJ: {_fmt_cnpj(p.get('cnpj'))}",
        f"Representada: {p.get('representada', '')}",
        f"Vendedor: {p.get('cod_vendedor', '')}" + (f" - {p['vendedor']}" if p.get("vendedor") else ""),
    ]:
        doc.cell(0, 6, _pdf_safe(linha), new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)

    colunas = [("Código", 14, 20), ("Produto", 38, 110), ("Qtd", 25, 20),
               ("Preço", 25, 20), ("Subtotal", 25, 20), ("Faturado no SPON", 50, 25)]
    doc.set_font("Helvetica", "B", 10)
    x = 14
    for label, w, _ in colunas:
        doc.set_xy(x, doc.get_y())
        doc.cell(w, 6, _pdf_safe(label))
        x += w
    doc.ln(6)
    doc.set_font("Helvetica", "", 10)

    cortados = _cortados_por_codigo(p)
    for it in (p.get("itens") or []):
        if doc.get_y() > 190:
            doc.add_page()
        status = _item_status_texto(p, cortados.get(str(it["codprod"])))
        valores = [
            str(it["codprod"]), str(it["descricao"])[:55], _fmt_qt(it["qt"]),
            _fmt_brl(it["preco_liquido"]), _fmt_brl(it["subtotal"]),
        ]
        x = 14
        y = doc.get_y()
        for valor, (_, w, _cw) in zip(valores, colunas):
            doc.set_xy(x, y)
            doc.cell(w, 6, _pdf_safe(valor))
            x += w
        doc.set_xy(x, y)
        doc.set_font("Helvetica", "", 8)
        doc.cell(colunas[-1][1], 6, _pdf_safe(status))
        doc.set_font("Helvetica", "", 10)
        doc.ln(6)

    doc.ln(2)
    doc.set_font("Helvetica", "B", 10)
    doc.cell(0, 6, _pdf_safe(f"Total: {_fmt_brl(p.get('subtotal_pedido'))}"))

    return bytes(doc.output())


def _enviar_aviso(p, cfg):
    corpo = {
        "cod_vendedor": p.get("cod_vendedor"),
        "mensagem": _mensagem_faturamento(p),
        "zapi_instance": cfg["instance"],
        "zapi_token": cfg["token"],
        "zapi_client_token": cfg["client_token"],
    }
    if p.get("status_spon") in STATUS_COM_COMPARACAO:
        try:
            import base64
            pdf_bytes = _construir_pdf_pedido(p)
            corpo["pdf_base64"] = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()
            corpo["pdf_filename"] = f"pedido_{p['numped']}.pdf"
        except Exception as e:
            print(f"[AVISO] PDF do pedido {p['numped']} falhou ({str(e)[:150]}) — mandando só a mensagem.")

    try:
        resp = requests.post(ENDPOINT_ENVIAR, json=corpo, timeout=30)
        d = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if d.get("ok"):
            print(f"OK enviado — pedido {p['numped']} ({p.get('status_spon')}) pro vendedor {p.get('cod_vendedor')}")
        else:
            print(f"[AVISO] envio do pedido {p['numped']} falhou: {d.get('motivo', resp.text[:200])}")
    except Exception as e:
        print(f"[AVISO] envio do pedido {p['numped']} falhou (exceção): {str(e)[:200]}")


def main():
    cfg = _ler_json_seguro(ZAPI_CONFIG_PATH, {})
    if not cfg.get("auto_avisar") or not cfg.get("instance") or not cfg.get("token") or not cfg.get("client_token"):
        print("Aviso automático desligado ou sem config Z-API — nada a fazer.")
        return

    caminho_data = _caminho_pedidos_data()
    if not caminho_data.exists():
        print(f"[AVISO] {caminho_data} não existe — nada a fazer.")
        return
    dados = _ler_data_js(caminho_data, "PEDIDOS_MERCOS_DATA")
    pedidos = dados.get("pedidos") or []

    visto = _ler_json_seguro(STATUS_VISTO_PATH, {})
    primeira_vez = len(visto) == 0

    novo_visto = {}
    enviados = 0
    for p in pedidos:
        numped = str(p["numped"])
        status_atual = p.get("status_spon")
        anterior = visto.get(numped)
        if not primeira_vez and anterior and anterior != status_atual and status_atual in STATUS_COM_AVISO:
            _enviar_aviso(p, cfg)
            enviados += 1
        novo_visto[numped] = status_atual

    _gravar_json_atomico(STATUS_VISTO_PATH, novo_visto)
    if primeira_vez:
        print(f"Primeira execução — baseline gravado com {len(novo_visto)} pedido(s), nenhum aviso mandado.")
    else:
        print(f"OK — {enviados} aviso(s) mandado(s), baseline atualizado com {len(novo_visto)} pedido(s).")


if __name__ == "__main__":
    main()
