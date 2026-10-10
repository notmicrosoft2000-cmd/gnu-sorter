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

  /* ---------- Reduced-motion preference ---------- */
  var prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Scroll reveal ---------- */
  if (!prefersReduced && 'IntersectionObserver' in window) {
    var revealSelectors = [
      '.section-head', '.highlight', '.card', '.step', '.os-card', '.panel',
      '.download-detail > *', '.cta-inner > *'
    ].join(',');
    var revealEls = document.querySelectorAll(revealSelectors);
    if (revealEls.length) {
      var revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('in');
            revealObserver.unobserve(entry.target);
          }
        });
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.1 });
      Array.prototype.forEach.call(revealEls, function (el) {
        el.classList.add('reveal');
        var parent = el.parentNode;
        var idx = parent ? Array.prototype.indexOf.call(parent.children, el) : 0;
        el.style.transitionDelay = Math.min(idx, 6) * 55 + 'ms';
        revealObserver.observe(el);
      });
    }
  }

  /* ---------- Live sorting demo (home page) ---------- */
  function initSortDemo(demo) {
    var files = Array.prototype.slice.call(demo.querySelectorAll('.demo-file'));
    if (!files.length) { return; }

    var folders = {};
    Array.prototype.forEach.call(demo.querySelectorAll('.demo-folder'), function (folder) {
      folders[folder.getAttribute('data-folder')] = {
        el: folder,
        stack: folder.querySelector('.demo-folder-stack')
      };
    });

    var index = 0;

    function step() {
      if (index >= files.length) {
        window.setTimeout(reset, 2600);
        return;
      }
      var file = files[index++];
      var target = folders[file.getAttribute('data-target')];
      if (!target) { step(); return; }

      var src = file.getBoundingClientRect();
      var dst = target.el.getBoundingClientRect();
      var clone = file.cloneNode(true);
      clone.classList.add('demo-flying');
      clone.style.position = 'fixed';
      clone.style.left = src.left + 'px';
      clone.style.top = src.top + 'px';
      clone.style.width = src.width + 'px';
      clone.style.margin = '0';
      clone.style.pointerEvents = 'none';
      document.body.appendChild(clone);
      file.classList.add('demo-taken');
      target.el.classList.add('is-active');

      var dx = (dst.left + dst.width / 2) - (src.left + src.width / 2);
      var dy = (dst.top + 34) - src.top;

      var anim = clone.animate([
        { transform: 'translate(0, 0) scale(1)', opacity: 1, offset: 0 },
        { transform: 'translate(' + dx + 'px, ' + dy + 'px) scale(0.45)', opacity: 0.85, offset: 0.82 },
        { transform: 'translate(' + dx + 'px, ' + (dy + 8) + 'px) scale(0.2)', opacity: 0, offset: 1 }
      ], { duration: 850, easing: 'cubic-bezier(.5, .05, .25, 1)' });

      anim.onfinish = function () {
        if (clone.parentNode) { clone.parentNode.removeChild(clone); }
        if (target.stack) {
          var chip = document.createElement('span');
          chip.className = 'demo-chip';
          chip.textContent = file.getAttribute('data-ext') || 'file';
          target.stack.appendChild(chip);
        }
        window.setTimeout(function () { target.el.classList.remove('is-active'); }, 550);
      };

      window.setTimeout(step, 640);
    }

    function reset() {
      Array.prototype.forEach.call(demo.querySelectorAll('.demo-folder-stack'), function (stack) {
        stack.textContent = '';
      });
      files.forEach(function (file) { file.classList.remove('demo-taken'); });
      index = 0;
      window.setTimeout(step, 900);
    }

    window.setTimeout(step, 700);
  }

  if (!prefersReduced && window.Element && Element.prototype.animate) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-demo]'), initSortDemo);
  }
})();
