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
  if (sessionStorage.getItem(_AUTH_KEY) === _AUTH_HASH) return;

  // Sem isso, quem chega direto de uma página do Streamlit (Google OAuth,
  // cookie offtrade_token já presente — ex: botão "Voltar" em
  // Credito_e_Cadastro) caía de novo em login.html mesmo já autenticado: o
  // gate daqui é sessionStorage, que é por aba e nunca foi setado numa aba
  // que só passou pelo Streamlit (confirmado em 2026-08-04). Cookie presente
  // já prova login válido — marca o gate local também, senão repetiria essa
  // checagem a cada navegação nessa aba.
  if (_getCookie('offtrade_token')) {
    sessionStorage.setItem(_AUTH_KEY, _AUTH_HASH);
    // Sem isso, rg_email fica vazio nessa aba (só login.html setava) — as
    // páginas "só pra gestor" (base_ataque_vinhos.html, raiox_clientes_risco.html
    // etc.) revalidam contra sessionStorage.rg_email e bloqueavam gestor de
    // verdade que chegou por esse atalho (confirmado em 2026-08-07, e-mail
    // na lista de gestores mas bloqueado por chegar direto na página, sem
    // passar pelo Google SSO nessa aba).
    const email = _decodeEmailFromCookie();
    if (email) sessionStorage.setItem('rg_email', email);
    return;
  }

  sessionStorage.setItem('rg_redirect', location.pathname + location.search);
  location.replace('login.html');
})();
