"""
Gera raiox_vendedores_data.js — estrutura das equipes de campo OFF TRADE em
todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON): times (Key
Account / Atacarejo / Convenience / Outros — mapeamento oficial é só de
RJ, vendedores de outros estados caem em "Outros" com o estado identificado),
quantidade de vendedores por time e distribuição de clientes atendidos por
região (cidade). CODUSUR não é único entre bases — cada vendedor é
identificado pela chave composta "ESTADO-RCA".
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

BASES = [
    {"estado": "RJ", "engine": engine},
    {"estado": "ES", "engine": engine},
    {"estado": "SP", "engine": engine_spon},
    {"estado": "MG", "engine": engine_mgon},
]
_SCHEMA_POR_ENGINE = {"RJ": "CRC", "ES": "CRC", "SP": "SPON", "MG": "MGON"}

# RCA -> (nome, time). Fonte: campanha_crusoe.py — lista oficial confirmada
# com o usuário em 08/07/2026 (ver memória project-campanhas-times). Só
# cobre o RJ; vendedores de outros estados entram como "Outros".
TIMES_RJ = {
    275: ("Maria Luiza", "KEY_ACCOUNT"),
    158: ("Jose Marcelo Cardoso", "KEY_ACCOUNT"),
    144: ("Diogo Raposo", "ATACAREJO"),
    153: ("Angelo Neves Suzart", "ATACAREJO"),
    412: ("Barbara Cabral", "ATACAREJO"),
    419: ("Natali de Oliveira", "ATACAREJO"),
    439: ("Mateus Cardoso", "ATACAREJO"),
    450: ("Leandro Souza", "ATACAREJO"),
    471: ("Ana Clara Fassano", "ATACAREJO"),
    156: ("Marilena Tragel", "CONVENIENCE"),
    378: ("Fabio Valotti", "CONVENIENCE"),
    379: ("Jorge Maciel", "CONVENIENCE"),
    431: ("Adeilson Gonçalvez", "CONVENIENCE"),
}

TIME_LABEL = {
    "KEY_ACCOUNT": "Key Account",
    "ATACAREJO": "Atacarejo (Pequeno e médio varejo)",
    "CONVENIENCE": "Convenience (Varejo tradicional e Conveniência)",
    "OUTROS": "Outros / sem time definido",
}

_vendedores_partes, _clientes_partes = [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng = base["estado"], base["engine"]
    schema = _SCHEMA_POR_ENGINE[estado]
    try:
        # RJ e ES compartilham o schema CRC — sem o filtro de ESTADO, as duas
        # iterações trariam o mesmo roster de vendedores/clientes duplicado.
        v = carregar_dados(
            f"SELECT CODUSUR, NOME FROM {schema}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = '{estado}'",
            eng, f"raiox_vendedores_off_trade_{estado}"
        )
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vendedores_partes.append(v)

        c = carregar_dados(f"""
            SELECT C.CODCLI, COALESCE(C.MUNICENT,'') CIDADE, C.CODUSUR1, C.CODUSUR2
            FROM {schema}.PCCLIENT C
            LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
            LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
            WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
              AND (U1.ESTADO = '{estado}' OR U2.ESTADO = '{estado}')
        """, eng, f"raiox_vendedores_clientes_{estado}")
        c.columns = c.columns.str.upper()
        c['ESTADO'] = estado
        _clientes_partes.append(c)
        print(f"  OK {estado}: {len(v)} vendedores, {len(c)} clientes")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vendedores_partes, ignore_index=True) if _vendedores_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'ESTADO'])
_nomes_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
todas_chaves = sorted(_nomes_por_chave)

clientes = pd.concat(_clientes_partes, ignore_index=True) if _clientes_partes else pd.DataFrame(columns=['CODCLI', 'CIDADE', 'CODUSUR1', 'CODUSUR2', 'ESTADO'])
clientes['CIDADE'] = clientes['CIDADE'].fillna('').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
clientes['CLIENTE_KEY'] = clientes['ESTADO'] + '-' + clientes['CODCLI'].astype(str)


def _clientes_da_chave(estado, rca):
    return clientes[(clientes['ESTADO'] == estado) & ((clientes['CODUSUR1'] == rca) | (clientes['CODUSUR2'] == rca))]


times = {}
for estado, rca in todas_chaves:
    if estado == "RJ" and rca in TIMES_RJ:
        nome, time_key = TIMES_RJ[rca]
    else:
        nome, time_key = _nomes_por_chave.get((estado, rca), f"RCA {rca}"), "OUTROS"
    cli_v = _clientes_da_chave(estado, rca)
    cidades = (
        cli_v.groupby('CIDADE')['CLIENTE_KEY'].nunique()
        .sort_values(ascending=False)
    )
    vendedor = {
        'rca': int(rca),
        'estado': estado,
        'chave': f"{estado}-{rca}",
        'nome': nome,
        'total_clientes': int(cli_v['CLIENTE_KEY'].nunique()),
        'cidades': [
            {'cidade': c or 'N/D', 'clientes': int(n)}
            for c, n in cidades.items()
        ],
    }
    times.setdefault(time_key, []).append(vendedor)

resultado_times = []
for time_key in ["KEY_ACCOUNT", "ATACAREJO", "CONVENIENCE", "OUTROS"]:
    vendedores = sorted(times.get(time_key, []), key=lambda v: v['nome'])
    total_clientes_time = sum(v['total_clientes'] for v in vendedores)
    # Regiões cobertas pelo time inteiro (soma das cidades de todos os vendedores)
    regioes = {}
    for v in vendedores:
        for c in v['cidades']:
            regioes[c['cidade']] = regioes.get(c['cidade'], 0) + c['clientes']
    regioes_ordenadas = sorted(regioes.items(), key=lambda kv: kv[1], reverse=True)

    resultado_times.append({
        'time': time_key,
        'label': TIME_LABEL[time_key],
        'qtd_vendedores': len(vendedores),
        'total_clientes': total_clientes_time,
        'vendedores': vendedores,
        'regioes': [{'cidade': c or 'N/D', 'clientes': n} for c, n in regioes_ordenadas],
    })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'times': resultado_times,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_vendedores_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_VENDEDORES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

total_vend = sum(t['qtd_vendedores'] for t in resultado_times)
print(f"OK raiox_vendedores_data.js — {total_vend} vendedores em {len(resultado_times)} times")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_vendedores_data.js", "raiox_vendedores.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_vendedores_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
