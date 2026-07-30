# Gmail sem reautenticação manual (Service Account + Domain-wide Delegation)

Hoje `alerta_logistica_rj.py` autentica via OAuth "installed app" (`token_gmail.json`,
gerado por `gmail_setup.py`). Enquanto o app estiver em modo **Testing** no Google
Cloud Console, o refresh token expira sozinho a cada 7 dias — por isso o alerta
quebra periodicamente com `invalid_grant: Token has been expired or revoked`.

O código já suporta os dois modos (`alerta_logistica_rj.py:_get_service()`): se
`service_account_gmail.json` existir e `GMAIL_DELEGATE_USER` estiver setado no
`.env`, ele usa a service account (nunca expira, sem login). Senão, cai no fluxo
OAuth de sempre. Falta só criar as credenciais — passos abaixo, só um
administrador do Google Workspace (`rigarr.com.br`) consegue fazer.

## 1. Criar a Service Account (Google Cloud Console)

1. `console.cloud.google.com/iam-admin/serviceaccounts?project=precise-truck-493600-b0`
2. **Create Service Account** → nome tipo `gmail-alerta-logistica` → Create and Continue → Done
   (não precisa dar nenhum papel/role de projeto)
3. Abra a service account criada → aba **Keys** → **Add Key** → **Create new key** → JSON
4. Baixa um arquivo `.json` — salva ele como `service_account_gmail.json` na pasta
   `G:\Meu Drive\offtrade` (não commitar — já está no `.gitignore`)
5. Copia o **Client ID** (número, aba Details) — vai precisar no próximo passo
6. Em **APIs & Services → OAuth consent screen**, se ainda não estiver, mude
   "Publishing status" para **In production** (resolve também o problema de
   expiração do fluxo antigo, redundância de segurança)

## 2. Autorizar domain-wide delegation (Google Workspace Admin Console)

Precisa de login de **administrador** do Workspace `rigarr.com.br`:

1. `admin.google.com` → **Segurança → Controle de acesso e dados → Delegação em todo o domínio**
2. **Adicionar novo**
3. **ID do cliente**: cole o Client ID da service account (passo 1.5)
4. **Escopos OAuth**: `https://www.googleapis.com/auth/gmail.modify`
5. Autorizar

## 3. Configurar localmente

No `.env` (raiz do projeto), adicionar a linha:

```
GMAIL_DELEGATE_USER=email-da-caixa-que-recebe-os-alertas@rigarr.com.br
```

(o mesmo e-mail que hoje faz login manual via `gmail_setup.py`)

## 4. Sincronizar com a VPS

```
python deploy_pipeline_vps.py
```

Isso copia `service_account_gmail.json` e o `.env` atualizado (com
`GMAIL_DELEGATE_USER`) pro pipeline da VPS. A partir daí `alerta_logistica_rj.py`
usa a service account automaticamente, local e na VPS — sem depender mais de
`token_gmail.json`/`gmail_setup.py`.
