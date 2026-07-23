// Abrindo o arquivo direto do disco (file://) em vez do site publicado —
// redireciona pra versão hospedada na VPS, que sempre está atualizada.
// Usado nas páginas que não carregam auth.js/auth_vendedor.js (que já têm
// esse mesmo redirecionamento embutido).
(function () {
  if (location.protocol === 'file:') {
    location.replace('https://offtrade.duckdns.org/' + location.pathname.split(/[\\/]/).pop() + location.search);
  }
})();
