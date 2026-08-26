# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é este projeto

OfftradeHub — dashboard comercial da operação Off Trade (Rigarr/CRC e distribuidoras
parceiras: thekings, CASTAS, GARRIDO, SPON, MGON, BLENDED). Um pipeline Python lê várias
bases Oracle (ERP Winthor, um schema por empresa/distribuidora) e planilhas do Google
Drive, e gera dois produtos:

1. **Site estático** (`*.html` + `*_data.js` na raiz) — páginas HTML puras (sem
   framework/bundler) que só importam um `<nome>_data.js` gerado pelo pipeline
   (`const NOME_DATA = {...}`) e renderizam tudo em JS no navegador. `index.html` é o
   hub com links pra cada página.
2. **App Streamlit** (`app.py`, `utils.py`, `app_pages/*.py`) — só as telas que precisam
   de formulário/gravação (crédito e cadastro, objetivos), com login Google OAuth.

Não há build step, bundler, linter ou suíte de testes configurada neste repo — os
scripts Python são executados diretamente e as páginas HTML são servidas como estão.

## Como rodar localmente

```bash
python main.py                          # roda o pipeline completo (todas as exportações)
python exportacao_<nome>.py             # roda só uma exportação isolada (gera <nome>_data.js)
streamlit run app.py                    # sobe o app Streamlit localmente
```

Não existe comando de lint/test/build — não invente um. Pra validar uma exportação,
rode o script isolado e confira o `*_data.js` gerado (ou importe as funções num REPL,
ver `montar_*`/`main()` de cada `exportacao_*.py`).

## Arquitetura do pipeline (`main.py`)

`main.py` é o orquestrador: chama, em sequência, ~20 scripts `exportacao_*.py` (a
maioria via `subprocess.run(..., timeout=...)`, alguns via `import` direto), cada um
gerando seu próprio `<nome>_data.js`. Cada etapa é isolada em `try/except` — uma
exportação falhando **não** derruba o pipeline inteiro, só deixa aquele `_data.js`
desatualizado (rastreado por `exportacao_status_paginas.py` → `status_paginas_data.js`,
consumido por `status_paginas.html`).

Padrão de cada `exportacao_*.py`:
- Consulta uma ou mais bases Oracle (ver `meta.py`) via `carregar_dados`/`carregar_paralelo`.
- Monta um dict `payload` com `atualizado_em` (timestamp) + `fontes_indisponiveis` (bases
  que falharam) + os dados propriamente ditos.
- Escreve `const NOME_DATA = {json...};` em `nome_data.js` (escrita atômica: arquivo
  `.tmp` + `os.replace`, pra não deixar a página lendo um JSON pela metade).
- Ao final, dá `git add/commit/push` do próprio `_data.js` (função replicada em cada
  script, não centralizada — é o padrão estabelecido, não refatorar sem necessidade).

**Lock de execução única**: `main.py` usa `.pipeline.lock` (PID + timestamp) porque roda
em dois lugares independentes (Tarefa Agendada local, de hora em hora, e cron da VPS em
horário comercial) — sem o lock, duas execuções concorrentes corrompiam `_data.js`/git.

**Scripts fora do `main.py` de propósito**, cada um com cron próprio (VPS): `exportacao_meta.py`
(+ `exportacao_es/mg/sp.py`) a cada 15 min, `exportacao_pedidos_bloqueados.py` a cada 5 min.
Esses se auto-publicam direto em `/opt/offtrade-static` (função `_publicar_static()` em
cada um) porque `deploy_static_vps.py` os exclui de propósito (`EXCLUDE_JS`) — sincronizar
por cima sobrescreveria dado fresco da VPS com uma cópia local desatualizada.

## Multi-base Oracle (`meta.py`)

`meta.py` define um `engine` SQLAlchemy por base (`engine`=CRC, `engine_theking`,
`engine_castas`, `engine_garrido`, `engine_spon`, `engine_mgon`, `engine_blended`) e duas
funções centrais de resiliência, usadas por praticamente todo `exportacao_*.py`:

- `carregar_dados(query, engine, nome_tabela)` — 3 tentativas com timeout forçado por
  thread daemon (nem `pool_pre_ping` nem `expire_time` bastam quando o Oracle derruba a
  sessão em silêncio); marca a engine como "morta" pro resto do processo após esgotar
  tentativas, pra não pagar 3×20s de novo em cada chamada seguinte à mesma fonte.
- `carregar_paralelo(chamadas)` — roda várias `(query, engine, nome)` em paralelo
  (`ThreadPoolExecutor`), pra uma fonte lenta/travada não bloquear as outras.

Convenção em todo exportador: uma fonte indisponível vira `[AVISO] ... ignorado` +
entrada em `fontes_indisponiveis` no payload (nunca derruba a exportação inteira) — as
páginas HTML mostram esse aviso via `fontes_alert.js` (incluído com
`fontes_status_data.js`, gerado por `exportacao_meta.py`).

Nem toda base tem as mesmas views/colunas — checar comentários em cada
`exportacao_*.py` antes de assumir uma query igual funciona em outro schema (ex:
`thekings` não tem várias views que `CRC` tem).

## Front-end estático (`*.html`)

Sem framework: cada página é um HTML autocontido com um `<script src="nome_data.js">`
antes do `<style>`/`<script>` inline, que lê `const NOME_DATA` e renderiza a tabela em JS
puro (filtros/ordenação/paginação são todos client-side). Padrão comum entre páginas
(ver `pedidos_bloqueados.html`, `inadimplencia.html` como referência ao criar uma nova):
tema escuro (`:root` com `--bg/--card/--accent/...`), header com título + timestamp,
KPIs, controles de filtro (`<select>`/busca), tabela ordenável por clique no `<th>`,
exportação CSV client-side, `sticky_hscroll.js` pro cabeçalho da tabela grudar ao rolar.

**Autenticação**: incluir `<script src="auth.js"></script>` no `<head>` torna a página
"só gestor" — `auth.js` valida cookie/token e redireciona pra `login.html` se a pessoa
não estiver na lista de gestores (mesma lista mantida em paralelo em `utils.py`,
`EMAILS_ADMIN`). Páginas voltadas pro vendedor comum (ex: pedir limite/cadastro) usam
outro fluxo de auth, não esse gate.

## Três alvos de deploy — não confundir

| Script | Destino VPS | O que sincroniza |
|---|---|---|
| `deploy_pipeline_vps.py` | `/opt/offtrade-pipeline` | `main.py` + todos os `exportacao_*.py` + `.html`/`auth.js` — pra rodar o pipeline direto na VPS, sem depender do PC local |
| `deploy_static_vps.py` | `/opt/offtrade-static` | todo `*.html` + `*.js` da raiz (exceto `EXCLUDE_HTML`/`EXCLUDE_JS`) — servido pelo nginx em offtrade.duckdns.org |
| `deploy_vps.py` | `/opt/offtrade` | `app.py`, `utils.py`, `app_pages/`, `.streamlit/` — reinicia o serviço `offtrade` (systemd) |

`/opt/offtrade-pipeline` **não é um clone git** — é sincronizado sob demanda via SFTP por
`deploy_pipeline_vps.py`. Todo script que roda na VPS (`git_commit_push` em `utils.py`,
ou o bloco de git em cada `exportacao_*.py`) checa se `.git` existe antes de tentar
commitar, pra não falhar nesse ambiente.

## Variáveis de ambiente (`.env`, não versionado)

Credenciais Oracle (`VPN_USER`/`VPN_PASSWORD`, `CRC_USER`/`CRC_PASSWORD`,
`SPON_USER`/`SPON_PASSWORD`), DSNs (`DSN_CRC`, `DSN_TK`, `DNS_SP`, `DNS_MG`), OAuth Google
(`GOOGLE_CLIENT_ID`/`SECRET`), WhatsApp (Evolution API: `EVOLUTION_BASE_URL`/`INSTANCE`/`KEY`),
acesso à VPS (`VPS_IP`/`VPS_USER`/`VPS_PASSWORD`), `ORACLE_LIB` (caminho do Instant
Client — diferente em Windows local vs. VPS Linux, ver `build_remote_env()` em
`deploy_pipeline_vps.py`), `SEND_ALERTS` e `OFFTRADE_RUNTIME` (`"local"` ou `"vps"` — muda
comportamento de deploy/publish em vários scripts, ver `main.py`).
