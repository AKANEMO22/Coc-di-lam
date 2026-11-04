(() => {
  const sidebar = document.querySelector('.sidebar');
  const toggle = document.querySelector('.menu-toggle');
  const backdrop = document.querySelector('.nav-backdrop');
  const mobile = window.matchMedia('(max-width:850px)');
  const closeButton = document.createElement('button');
  closeButton.type = 'button'; closeButton.className = 'nav-close';
  closeButton.setAttribute('aria-label', 'Đóng điều hướng'); closeButton.textContent = '×';
  sidebar?.prepend(closeButton);
  function updateInert() { if (sidebar) sidebar.inert = mobile.matches && !sidebar.classList.contains('open'); }
  function closeMenu() {
    sidebar?.classList.remove('open'); backdrop?.classList.remove('open');
    toggle?.setAttribute('aria-expanded', 'false');
    updateInert();
    document.body.classList.remove('nav-open');
  }
  toggle?.addEventListener('click', () => {
    const open = !sidebar.classList.contains('open');
    sidebar.classList.toggle('open', open); backdrop.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    updateInert();
    document.body.classList.toggle('nav-open', open);
    if (open) closeButton.focus();
  });
  closeButton.addEventListener('click', () => { closeMenu(); toggle?.focus(); });
  mobile.addEventListener('change', () => { closeMenu(); updateInert(); });
  updateInert();
  backdrop?.addEventListener('click', closeMenu);
  document.addEventListener('keydown', event => {
    if (!sidebar?.classList.contains('open')) return;
    if (event.key === 'Escape') { closeMenu(); toggle?.focus(); }
    if (event.key === 'Tab' && mobile.matches) {
      const targets = Array.from(sidebar.querySelectorAll('a,button')).filter(el => el.getClientRects().length);
      if (event.shiftKey && document.activeElement === targets[0]) { event.preventDefault(); targets[targets.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === targets[targets.length - 1]) { event.preventDefault(); targets[0].focus(); }
    }
  });
  document.querySelectorAll('.sidebar nav a').forEach(link => link.addEventListener('click', closeMenu));
  if (document.body.dataset.page === 'knowledge') {
    function navigate() {
      const section = ['knowledge', 'search', 'indexes'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'knowledge';
      document.querySelectorAll('[data-admin-section]').forEach(node => { node.hidden = node.id !== section; });
      document.querySelectorAll('[data-nav-section]').forEach(link => {
        const selected = link.dataset.navSection === section;
        link.classList.toggle('active', selected);
        if (selected) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
      });
      const labels = {knowledge: 'Kho tri thức', search: 'Tra cứu thử nghiệm', indexes: 'Phiên bản chỉ mục'};
      document.getElementById('page-title').textContent = labels[section];
      document.title = `${labels[section]} · CocDiLam`;
    }
    window.addEventListener('hashchange', navigate); navigate();
  }
})();
