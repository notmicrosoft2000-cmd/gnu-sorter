(function () {
  'use strict';

  var root = document.documentElement;

  /* ---------- Theme ---------- */
  var toggles = document.querySelectorAll('.theme-toggle');
  function currentTheme() {
    return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }
  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    try { localStorage.setItem('gs-theme', theme); } catch (e) { /* storage may be blocked */ }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) { meta.setAttribute('content', theme === 'light' ? '#fbfcfc' : '#0a0b0d'); }
    toggles.forEach(function (btn) {
      btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
    });
  }
  toggles.forEach(function (btn) {
    btn.addEventListener('click', function () {
      applyTheme(currentTheme() === 'light' ? 'dark' : 'light');
    });
  });
  applyTheme(currentTheme());

  /* ---------- Mobile navigation ---------- */
  var menuToggle = document.querySelector('.menu-toggle');
  if (menuToggle) {
    var closeNav = function () {
      document.body.classList.remove('nav-open');
      menuToggle.setAttribute('aria-expanded', 'false');
      menuToggle.setAttribute('aria-label', 'Open navigation');
    };
    menuToggle.addEventListener('click', function () {
      var open = document.body.classList.toggle('nav-open');
      menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    });
    document.querySelectorAll('.primary-nav a').forEach(function (link) {
      link.addEventListener('click', closeNav);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { closeNav(); }
    });
  }

  /* ---------- Copy buttons ---------- */
  function legacyCopy(text, done) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'absolute';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* ignore */ }
    document.body.removeChild(area);
  }

  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.getAttribute('data-copy') || '';
      var done = function () {
        var original = btn.textContent;
        btn.textContent = 'Copied';
        btn.classList.add('copied');
        window.setTimeout(function () {
          btn.textContent = original;
          btn.classList.remove('copied');
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { legacyCopy(text, done); });
      } else {
        legacyCopy(text, done);
      }
    });
  });

  /* ---------- Footer year ---------- */
  var year = document.getElementById('year');
  if (year) { year.textContent = String(new Date().getFullYear()); }

  /* ---------- Documentation scrollspy ---------- */
  var sidebarLinks = document.querySelectorAll('.docs-sidebar a');
  if (sidebarLinks.length) {
    var sections = [];
    sidebarLinks.forEach(function (link) {
      var href = link.getAttribute('href') || '';
      if (href.charAt(0) === '#') {
        var el = document.getElementById(href.slice(1));
        if (el) { sections.push({ el: el, link: link }); }
      }
    });
    if (sections.length) {
      var setActive = function () {
        var pos = window.scrollY + 130;
        var current = sections[0];
        sections.forEach(function (item) {
          if (item.el.offsetTop <= pos) { current = item; }
        });
        sidebarLinks.forEach(function (link) { link.classList.remove('active'); });
        if (current) { current.link.classList.add('active'); }
      };
      setActive();
      window.addEventListener('scroll', setActive, { passive: true });
      window.addEventListener('resize', setActive);
    }
  }
})();
