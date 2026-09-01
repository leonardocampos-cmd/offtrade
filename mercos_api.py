"""Acesso direto (sem navegador) aos relatórios da Mercos, via engenharia
reversa das chamadas que o próprio painel (app.mercos.com) faz — não é uma
API oficial/documentada, pode mudar sem aviso (mesma ressalva/abordagem já
usada em canhoto_digital.py pro Canhoto Digital). Descoberto inspecionando
as requisições de rede do painel logado em 2026-08-31, a pedido do usuário
("preciso de uma alternativa" ao export manual — ver gerar_pedidos_mercos_data.py).

Substitui os passos manuais (login em app.mercos.com > Indicadores >
Relatórios > Exportar Excel > salvar em Downloads) por chamadas HTTP puras,
autenticadas por sessão — funciona em qualquer lugar, inclusive na VPS
(sem precisar de Chrome/Selenium instalado lá).

Dois relatórios diferentes, dois mecanismos diferentes:
- "Vendas detalhadas" (painel novo, React): POST JSON em
  /api-online/indicadores/vendas/exportar/?xls=1
- "Produtos por pedido" (painel antigo): GET com query string em
  /relatorios/produtos_detalhado/?...&xls=1 — trunca em 5000 linhas, por
  isso busca em pedaços de ~5 meses (mesma limitação que já existia no
  fluxo manual, ver gerar_pedidos_mercos_data.py).
"""
import os
import re
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://app.mercos.com"
EMPRESA_ID = os.getenv("MERCOS_EMPRESA_ID", "424258")

_STATUS_TODOS_PEDIDOS = "2"  # confirmado nas duas chamadas capturadas


def login() -> requests.Session:
    usuario = os.getenv("MERCOS_USER")
    senha = os.getenv("MERCOS_PASS")
    if not usuario or not senha:
        raise RuntimeError("MERCOS_USER/MERCOS_PASS não configurados no .env")
    sessao = requests.Session()
    sessao.headers["User-Agent"] = "Mozilla/5.0 (offtrade-pipeline)"
    # GET antes do POST — pega o(s) cookie(s) de sessão/CSRF que o form de
    # login normalmente depende (mesmo padrão de qualquer app Django).
    sessao.get(f"{BASE_URL}/login", timeout=30)
    resp = sessao.post(
        f"{BASE_URL}/login",
        data={"usuario": usuario, "senha": senha},
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()
    # /login é a mesma casca de SPA servida tanto logado quanto deslogado
    # (roteamento é client-side, resp.url nunca muda) — o único jeito
    # confiável de saber se autenticou é o cookie MUserID aparecer (achado
    # testando em 2026-08-31: checar a URL sempre dava falso negativo).
    if "MUserID" not in sessao.cookies:
        raise RuntimeError("Login na Mercos falhou — usuário/senha incorretos ou fluxo de login mudou")
    return sessao


def baixar_vendas_detalhadas(sessao: requests.Session, data_inicial: date, data_final: date) -> bytes:
    """'Vendas detalhadas' — um pedido por linha. Painel novo (React), POST
    com corpo JSON (achado via interceptação de XHR, 2026-08-31)."""
    body = {
        "data_inicial": data_inicial.strftime("%Y-%m-%d"),
        "data_final": data_final.strftime("%Y-%m-%d"),
        "status": [_STATUS_TODOS_PEDIDOS],
        "tipos_de_pedido_ids": [], "status_custom_ids": [], "representadas_ids": [],
        "equipes_ids": [], "vendedores_ids": [], "plataformas": [], "clientes_ids": [],
        "cidade_cliente": None, "estados_clientes": [], "regioes_clientes": [],
        "tags_clientes_ids": [], "redes_clientes_ids": [], "segmentos_clientes_ids": [],
        "condicoes_pagamento_ids": [], "formas_pagamentos_ids": [],
        "ordenacao": ["1", "DESC"], "pagina": 0,
        "colunas_selecionadas": [
            "data_emissao", "numero", "representada_nome_fantasia", "cliente_razao_social",
            "cliente_nome_fantasia", "vendedor", "condicao_pagamento", "total_pedido", "cliente_cnpj",
        ],
    }
    resp = sessao.post(
        f"{BASE_URL}/{EMPRESA_ID}/api-online/indicadores/vendas/exportar/",
        params={"xls": 1}, json=body, timeout=90,
    )
    resp.raise_for_status()
    return resp.content


def baixar_produtos_por_pedido(sessao: requests.Session, data_inicial: date, data_final: date) -> bytes:
    """'Produtos por pedido' — um item de pedido por linha. Painel antigo,
    GET com query string (achado via network log, 2026-08-31). Trunca em
    5000 linhas do lado da Mercos — quem chama decide o tamanho da janela."""
    params = {
        "representada": "", "equipesColaboradores": "u", "colaborador": "",
        "equipe": "", "tipo_de_pedido": "", "status_custom": "",
        "status_pedido": _STATUS_TODOS_PEDIDOS, "segmento": "", "cliente": "", "produto": "",
        "periodo_inicial": data_inicial.strftime("%d/%m/%Y"),
        "periodo_final": data_final.strftime("%d/%m/%Y"),
        "tipo": 1, "pdf": 0, "xls": 1,
    }
    resp = sessao.get(
        f"{BASE_URL}/{EMPRESA_ID}/relatorios/produtos_detalhado/",
        params=params, timeout=90,
    )
    resp.raise_for_status()
    return resp.content


def _janelas_semestrais(data_inicial: date, data_final: date):
    """'Produtos por pedido' trunca em 5000 linhas — quebra o período em
    janelas de ~150 dias (mesma janela que o usuário já usava manualmente,
    'um arquivo por semestre'), pra cada chamada individual ficar abaixo
    do limite mesmo em meses de pico de pedidos."""
    cursor = data_inicial
    while cursor <= data_final:
        fim_janela = min(cursor + timedelta(days=150), data_final)
        yield cursor, fim_janela
        cursor = fim_janela + timedelta(days=1)


def baixar_produtos_por_pedido_periodo_completo(sessao: requests.Session, data_inicial: date, data_final: date) -> list[bytes]:
    """Todo o período, em pedaços — uma lista de conteúdos .xls (um por
    janela), pra chamar quem for montar os pedidos igual já faz hoje com
    múltiplos arquivos relatorio_sem*.xls."""
    return [
        baixar_produtos_por_pedido(sessao, ini, fim)
        for ini, fim in _janelas_semestrais(data_inicial, data_final)
    ]


_RE_CARD_NOME  = re.compile(r'js-nome-colaborador">\s*([^<\n]+)')
_RE_CARD_FONE  = re.compile(r'icon-phone"></i>\s*([^<\n]+)')


def buscar_colaboradores_telefones(sessao: requests.Session) -> dict[str, str]:
    """Telefone de cada vendedor da Mercos ("colaborador"), pra mandar
    WhatsApp direto pra ele (pedido do usuário em 2026-09-01: o vendedor não
    tem cadastro no Winthor — são vendedores da própria SPON, não do time
    OFF TRADE — então o único cadastro de telefone que existe é o da própria
    Mercos). Não tem endpoint de API pra isso (achado em 2026-09-01
    inspecionando o painel: é uma página server-rendered comum, sem XHR) —
    faz o mesmo scrape server-side que _montar_pedidos já faz com o
    relatório de produtos.

    Chave = MESMO formato de gerar_pedidos_mercos_data.py::p['cod_vendedor']
    ("008" pra "008 - Marcos", ou o nome inteiro pra colaborador sem código
    tipo "WENEO RICARDO") — dá pra cruzar direto sem tabela de tradução."""
    resp = sessao.get(f"{BASE_URL}/{EMPRESA_ID}/colaboradores/", timeout=30)
    resp.raise_for_status()
    html = resp.text
    telefones = {}
    for bloco in re.split(r'<div id="card_colaborador_\d+"', html)[1:]:
        nome_m = _RE_CARD_NOME.search(bloco)
        fone_m = _RE_CARD_FONE.search(bloco)
        if not nome_m or not fone_m:
            continue
        criador = nome_m.group(1).strip()
        cod_vend, _, _nome_vend = criador.partition(" - ")
        digitos = re.sub(r"\D", "", fone_m.group(1))
        if len(digitos) < 10:
            continue  # fone incompleto/vazio no cadastro — ignora
        if not digitos.startswith("55"):
            digitos = "55" + digitos
        telefones[cod_vend.strip()] = digitos
    return telefones
