"""
Gera a aba "Planilha de Agendamento" de agendamento_data.js a partir de
CONTROLE AGENDAMENTOS.xlsx (Drive) — controle manual de agendamento de
entrega por NF (cliente, CNPJ, valor, data de agendamento, status). Só a
filial 4 da CRC (ANTIGO SISTEMA = 'CRC - 4') entra nesta página; as demais
filiais/empresas da planilha ficam de fora por enquanto.

Roda como step independente de email_pedidos.py (que gera a outra aba,
"Pedidos por E-mail x Faturado") — cada um faz merge no mesmo
agendamento_data.js sem sobrescrever o que o outro já gravou.

Padronização de nome do RCA (pedido do usuário em 2026-08-26, "padronizar
os nomes... pegando pelo número, coloca o nome"): a coluna VENDEDOR de
ANTIGO/NOVO é texto livre digitado à mão, com várias grafias da MESMA
pessoa (ex: "ANGELO NEVES SUZART" / "ANGELO NEVES" / "ANGELO SUZART" — 3
variantes achadas na planilha real). A aba "RCA" (CODUSUR + NOME) é a
referência canônica — resolve o texto livre pro nome oficial via
_resolver_vendedor(), em 3 níveis de confiança (exato -> subconjunto de
palavras, só se achar 1 candidato único -> fuzzy só com nota ≥0.90 e folga
≥0.10 pro 2º colocado). NUNCA junta duas pessoas diferentes por engano:
testado contra a planilha real e essa regra corretamente rejeita casos
como "IVANILDO MAIA" (fuzzy ingênuo juntava com "WANDO MACHADO", pessoa
errada) e "JORGE" sozinho (ambíguo entre 2 RCAs reais, "JORGE LUIZ" e
"JORGE MACIEL") — nesses casos fica com o texto original, sem inventar,
só reportado em 'avisos_vendedor_sem_match' pro usuário revisar/completar
a aba RCA se quiser.
"""
import difflib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

import baixar_planilhas_drive as _bpd

DATA_PATH = Path(__file__).parent / 'agendamento_data.js'


def _caminho_local_fallback() -> str:
    return r"G:\Drives compartilhados\Off Trade\CONTROLE AGENDAMENTOS.xlsx"


def _parse_valor(v) -> float:
    """A coluna VALOR mistura formatos na planilha: string tipo 'R$ 1.234,56'
    (formato BR, precisa trocar . por milhar e , por decimal) e número puro
    já convertido pelo Excel/pandas (ex: 394.19999999999993, erro de ponto
    flutuante). Tratar os dois como string cegamente destrói o segundo caso
    (o replace('.', '') vira '39419999999999993' — bug confirmado em
    2026-07-23, gerou 'Valor Agendado' de R$ 209 quatrilhões no KPI)."""
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    limpo = re.sub(r'[^\d,.-]', '', str(v)).replace('.', '').replace(',', '.')
    try:
        return float(limpo)
    except ValueError:
        return 0.0


def _s(v) -> str:
    return '' if pd.isna(v) else str(v).strip()


def _cod(v) -> str:
    """Código de cliente vem como float por causa de NaN na coluna (ex: 6940.0)."""
    if pd.isna(v):
        return ''
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip()


def _normalizar_nome(nome: str) -> str:
    s = str(nome or '').upper().strip()
    s = re.sub(r'\s*-\s*OFF\s*TRADE\s*$', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _carregar_rca_lookup(caminho) -> list:
    df_rca = pd.read_excel(caminho, sheet_name='RCA')
    df_rca.columns = [str(c).strip().upper() for c in df_rca.columns]
    lookup = []
    for _, r in df_rca.iterrows():
        nome = _s(r['NOME'])
        if not nome:
            continue
        norm = _normalizar_nome(nome)
        lookup.append({
            'codusur': _cod(r['CODUSUR']),
            'nome': nome,
            'norm': norm,
            'palavras': set(norm.split()),
        })
    return lookup


def _resolver_vendedor(bruto: str, rca_lookup: list, avisos: set) -> tuple:
    """(codusur, nome_canonico) pro texto livre de VENDEDOR — ver docstring
    do módulo pros 3 níveis de confiança. Sem match confiante, devolve
    ('', bruto) — mantém o texto original em vez de arriscar juntar duas
    pessoas diferentes, e registra em `avisos` pra revisão manual."""
    norm = _normalizar_nome(bruto)
    if not norm:
        return '', ''

    for r in rca_lookup:
        if r['norm'] == norm:
            return r['codusur'], r['nome']

    palavras = set(norm.split())
    candidatos_subset = [r for r in rca_lookup if palavras and palavras <= r['palavras']]
    if len(candidatos_subset) == 1:
        return candidatos_subset[0]['codusur'], candidatos_subset[0]['nome']

    scores = sorted(
        ((difflib.SequenceMatcher(None, norm, r['norm']).ratio(), r) for r in rca_lookup),
        key=lambda t: t[0], reverse=True,
    )
    if scores and scores[0][0] >= 0.90 and (len(scores) < 2 or scores[0][0] - scores[1][0] >= 0.10):
        return scores[0][1]['codusur'], scores[0][1]['nome']

    avisos.add(bruto)
    return '', bruto


def _merge_write(patch: dict):
    existing = {}
    if DATA_PATH.exists():
        raw = DATA_PATH.read_text(encoding='utf-8')
        m = re.search(r'=\s*(\{.*\});\s*$', raw.strip(), re.DOTALL)
        if m:
            try:
                existing = json.loads(m.group(1))
            except json.JSONDecodeError:
                existing = {}
    existing.update(patch)
    existing['atualizado_em'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        f.write(f"const AGENDAMENTO_DATA = {json.dumps(existing, ensure_ascii=False, indent=2)};\n")


SHEETS = ['ANTIGO', 'NOVO']  # 'RCA' (lookup) e 'INFO' não são dados de agendamento


def main():
    caminho = _bpd.com_fallback(_bpd.caminho_controle_agendamentos, _caminho_local_fallback())

    # O arquivo tem 4 abas — ANTIGO e NOVO são conjuntos de dados diferentes,
    # sem NFs em comum (não é uma sobrescrevendo a outra), então as duas
    # precisam ser lidas e somadas pra não perder ~875 linhas CRC-4 que só
    # existem na aba NOVO (confirmado em 2026-07-23: 0 NF em comum entre elas).
    partes = []
    for sheet in SHEETS:
        df_sheet = pd.read_excel(caminho, sheet_name=sheet)
        df_sheet.columns = [str(c).strip().upper() for c in df_sheet.columns]
        print(f"Aba '{sheet}': {len(df_sheet)} linha(s), colunas: {df_sheet.columns.tolist()}")
        partes.append(df_sheet)
    df = pd.concat(partes, ignore_index=True)

    df['_SISTEMA_NORM'] = df.get('SISTEMA', '').fillna('').astype(str).str.strip().str.upper()
    df_crc4 = df[df['_SISTEMA_NORM'] == 'CRC - 4'].copy()

    df_crc4['VALOR_NUM'] = df_crc4['VALOR'].apply(_parse_valor)
    df_crc4['DATA_DT']   = pd.to_datetime(df_crc4['DATA AGENDAMENTO'], errors='coerce')
    df_crc4['STATUS']    = df_crc4['STATUS'].fillna('').astype(str).str.strip().str.upper()
    df_crc4['STATUS LOGISTICA'] = df_crc4.get('STATUS LOGISTICA', '').fillna('').astype(str).str.strip().str.upper()
    df_crc4['VENDEDOR']  = df_crc4['VENDEDOR'].fillna('').astype(str).str.strip()

    # Padroniza o texto livre de VENDEDOR pro nome canônico da aba RCA (ver
    # docstring do módulo) — agrupa por esse nome em vez do texto cru, então
    # "ANGELO NEVES"/"ANGELO SUZART"/"ANGELO NEVES SUZART" viram um grupo só.
    rca_lookup = _carregar_rca_lookup(caminho)
    avisos_sem_match = set()
    _cache_resolucao = {}

    def _resolver_cache(bruto):
        if bruto not in _cache_resolucao:
            _cache_resolucao[bruto] = _resolver_vendedor(bruto, rca_lookup, avisos_sem_match)
        return _cache_resolucao[bruto]

    _resolvido = df_crc4['VENDEDOR'].apply(_resolver_cache)
    df_crc4['CODUSUR_VENDEDOR'] = _resolvido.apply(lambda t: t[0])
    df_crc4['VENDEDOR_CANONICO'] = _resolvido.apply(lambda t: t[1])
    if avisos_sem_match:
        print(f"[AVISO] {len(avisos_sem_match)} vendedor(es) sem match confiante na aba RCA "
              f"(mantidos com o texto original): {sorted(avisos_sem_match)}")

    vendedores_out = []
    for vendedor, grp in df_crc4.groupby('VENDEDOR_CANONICO', sort=False):
        itens = []
        for _, r in grp.iterrows():
            data_dt = r['DATA_DT']
            itens.append({
                'codigo_cliente':          _cod(r.get('CÓDIGO', '')),
                'cliente':                 _s(r.get('CLIENTE', '')),
                'cnpj':                    _s(r.get('CNPJ', '')),
                'filial':                  _s(r.get('FILIAL', '')),
                'valor':                   round(float(r['VALOR_NUM']), 2),
                'nf':                      _s(r.get('NF', '')),
                'data_agendamento':        data_dt.strftime('%d/%m/%Y') if pd.notna(data_dt) else '',
                'data_ord':                data_dt.strftime('%Y-%m-%d') if pd.notna(data_dt) else '',
                'status':                  _s(r['STATUS']),
                'obs':                     _s(r.get('OBS', '')),
                'status_logistica':        _s(r['STATUS LOGISTICA']),
                'justificativa_logistica': _s(r.get('JUSTIFICATIVA LOGISTICA', '')),
            })
        itens.sort(key=lambda i: i['data_ord'], reverse=True)
        codusur = _s(grp['CODUSUR_VENDEDOR'].iloc[0])
        vendedores_out.append({'nome': vendedor or 'Sem Vendedor', 'codusur': codusur, 'itens': itens})

    vendedores_out.sort(key=lambda v: v['nome'])

    _merge_write({'agendamentos': vendedores_out})
    print(f"OK - {len(vendedores_out)} vendedor(es), {len(df_crc4)} linha(s) CRC-4 -> {DATA_PATH}")

    repo_dir = str(Path(__file__).parent)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "agendamento_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza agendamento_data.js (planilha) - {datetime.now().strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK agendamento_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (site) ─────────────────────────────
# Mesma lógica/motivo de email_pedidos.py::_publicar_static (ver comentário
# lá) — os dois escrevem no mesmo agendamento_data.js merged, então os dois
# publicam o estado atual (já mesclado) do arquivo depois de gravar sua parte.
def _publicar_static():
    if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    if not DATA_PATH.exists():
        return
    tmp = os.path.join(destino, ".agendamento_data.js.tmp_publish")
    shutil.copy(DATA_PATH, tmp)
    os.replace(tmp, os.path.join(destino, "agendamento_data.js"))
    print(f"OK - agendamento_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
