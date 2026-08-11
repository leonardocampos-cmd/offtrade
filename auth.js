// Abrindo o arquivo direto do disco (file://) OU por outro domínio que não
// seja a VPS (ex: GitHub Pages, leonardocampos-cmd.github.io — mesmo
// conteúdo, mas o nginx da VPS nunca vê esse acesso, então some da auditoria
// em acessos.html) — redireciona sempre pra versão hospedada na VPS.
(function _redirectLocalToVps() {
  if (location.hostname !== 'offtrade.duckdns.org') {
    location.replace('https://offtrade.duckdns.org/' + location.pathname.split(/[\\/]/).pop() + location.search);
  }
})();

const _AUTH_HASH = 'b4ba917b95850dc43cce91dba3be9fd1a4f029e18b81d6846a7183839c81d8dd';
const _AUTH_KEY  = 'rg_auth';

// Mesma lista de login.html (EMAILS_OK) — as 30 páginas que incluem
// auth.js são só-gestor de propósito (pedidos.html, agendamento.html etc,
// ver memória "vendedores_auth_vazio_incidente"). Sem checar contra essa
// lista, o atalho de cookie abaixo promovia QUALQUER sessão autenticada
// (inclusive vendedor comum, cujo login também grava o cookie
// offtrade_token) a "rg_auth=HASH" — e várias páginas de vendedor
// (sp.html/es.html/mg.html/metas.html/login.html) tratam rg_auth===HASH
// como "é gestor, acesso total", ignorando a restrição por RCA. Confirmado
// em 2026-08-07: RCA 588 (W.S) acessou pedidos.html (só-gestor) 3 vezes.
const _GESTORES_PERMITIDOS = [
  'danielle.soares@rigarr.com.br',
  'allan.correa@rigarr.com.br',
  'leonardo.campos@rigarr.com.br',
  'alexsandro.nunes@rigarr.com.br',
  'giovani.cabral@rigarr.com.br',
  'kaliel.caro@rigarr.com.br',
  'artur.furlan@rigarr.com.br',
  'daniel.diniz@rigarr.com.br',
  'marcus.tanamachi@rigarr.com.br',
  'geovanna.lescano@rigarr.com.br',
  'fernando.risson@rigarr.com.br',
  'erocles.oliveira@rigarr.com.br',
  'andre.massensini@rigarr.com.br',
  'priscilla.zambrano@rigarr.com.br',
  'anderson.canaveis@rigarr.com.br',
];

async function _sha256(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

function _getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : null;
}

// Mesmo formato que utils.py::_decode_email decodifica do lado do Streamlit
// (id_token real do Google OU o token sintético "header.payload.sso" que
// login.html fabrica pro fluxo de RCA+senha) — payload é JSON base64url com
// campo "email", sem checar assinatura (o Streamlit também não checa).
function _decodeEmailFromCookie() {
  try {
    const raw = _getCookie('offtrade_token');
    if (!raw) return '';
    const idToken = JSON.parse(raw).id_token || '';
    let payload = idToken.split('.')[1] || '';
    payload = payload.replace(/-/g, '+').replace(/_/g, '/');
    payload += '='.repeat((4 - payload.length % 4) % 4);
    return (JSON.parse(atob(payload)).email || '').toLowerCase();
  } catch (e) {
    return '';
  }
}

(function checkAuth() {
  // Não basta checar só rg_auth: metas.html tem um atalho de senha
  // ("voltar para todos", sem Google SSO) que marca rg_auth sem nunca gravar
  // rg_email nessa aba — daí quem navegasse dali direto pra uma página que
  // reexige rg_email (base_ataque_vinhos.html, raiox_clientes_risco.html
  // etc.) ficava bloqueado mesmo sendo gestor de verdade (confirmado em
  // 2026-08-10, e-mail na lista de gestores mas rg_email vazio nessa aba).
  // Por isso, mesmo com rg_auth já ok, ainda tenta preencher rg_email a
  // partir do cookie antes de decidir se segue autenticado.
  if (sessionStorage.getItem(_AUTH_KEY) === _AUTH_HASH && sessionStorage.getItem('rg_email')) return;

  // Sem isso, quem chega direto de uma página do Streamlit (Google OAuth,
  // cookie offtrade_token já presente — ex: botão "Voltar" em
  // Credito_e_Cadastro) caía de novo em login.html mesmo já autenticado: o
  // gate daqui é sessionStorage, que é por aba e nunca foi setado numa aba
  // que só passou pelo Streamlit (confirmado em 2026-08-04). Cookie presente
  // já prova login válido — mas só marca o gate de gestor se o e-mail do
  // token for mesmo de gestor (ver _GESTORES_PERMITIDOS acima); vendedor com
  // cookie válido (login normal dele) cai no else e vai pro login.html, que
  // já sabe redirecionar pra metas.html dele (sessionStorage.rg_vendedor).
  const _email = _decodeEmailFromCookie();
  if (_email && _GESTORES_PERMITIDOS.includes(_email)) {
    sessionStorage.setItem(_AUTH_KEY, _AUTH_HASH);
    // Sem isso, rg_email fica vazio nessa aba (só login.html setava) — as
    // páginas "só pra gestor" (base_ataque_vinhos.html, raiox_clientes_risco.html
    // etc.) revalidam contra sessionStorage.rg_email e bloqueavam gestor de
    // verdade que chegou por esse atalho (confirmado em 2026-08-07, e-mail
    // na lista de gestores mas bloqueado por chegar direto na página, sem
    // passar pelo Google SSO nessa aba).
    sessionStorage.setItem('rg_email', _email);
    return;
  }

  // rg_auth já setado (ex: senha universal do metas.html) mas o cookie não
  // deu um e-mail de gestor (sessão de vendedor, ou nenhum cookie) — segue
  // autenticado no gate genérico (rg_auth), mas rg_email continua vazio de
  // propósito: páginas que exigem e-mail nominal continuam bloqueando, isso
  // é esperado (não é a mesma prova de identidade que o Google SSO dá).
  if (sessionStorage.getItem(_AUTH_KEY) === _AUTH_HASH) return;

  sessionStorage.setItem('rg_redirect', location.pathname + location.search);
  location.replace('login.html');
})();
