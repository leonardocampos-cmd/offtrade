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
    return;
  }

  sessionStorage.setItem('rg_redirect', location.pathname + location.search);
  location.replace('login.html');
})();
