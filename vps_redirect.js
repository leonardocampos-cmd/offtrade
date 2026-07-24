// Abrindo o arquivo direto do disco (file://) OU por outro domínio que não
// seja a VPS (ex: GitHub Pages, leonardocampos-cmd.github.io — mesmo
// conteúdo, mas o nginx da VPS nunca vê esse acesso, então some da auditoria
// em acessos.html) — redireciona sempre pra versão hospedada na VPS.
// Usado nas páginas que não carregam auth.js/auth_vendedor.js (que já têm
// esse mesmo redirecionamento embutido).
(function () {
  if (location.hostname !== 'offtrade.duckdns.org') {
    location.replace('https://offtrade.duckdns.org/' + location.pathname.split(/[\\/]/).pop() + location.search);
  }
})();
