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

  /* ---------- Decorative animated figures (rendered into [data-viz]) ---------- */
  var VIZ = {
    // Watch -> Match -> Move -> Journal
    pipeline: function () {
      var icoFolder = '<path d="M1 5h6l2 2h10v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 1 16Z" fill="none" stroke="var(--accent)" stroke-width="1.6"/>';
      var icoRules = '<path d="M2 5h12M2 10h12M2 15h7" stroke="var(--accent)" stroke-width="1.6" stroke-linecap="round"/><path d="M15 12l2 2 4-5" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>';
      var icoMove = '<rect x="2" y="4" width="7" height="13" rx="1.5" fill="none" stroke="var(--accent)" stroke-width="1.5"/><path d="M11 10.5h8m0 0-3-3m3 3-3 3" fill="none" stroke="var(--accent)" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>';
      var icoLog = '<path d="M4 2h9l4 4v14H4Z" fill="none" stroke="var(--accent)" stroke-width="1.5"/><path d="M7 10h9M7 14h9M7 18h5" stroke="var(--accent)" stroke-width="1.4" stroke-linecap="round"/>';
      function stage(x, num, label, icon, delay) {
        var d = 'style="animation-delay:' + delay + 's"';
        return '<g class="viz-stage" ' + d + '>'
          + '<rect class="viz-stage-bg" ' + d + ' x="' + x + '" y="20" width="140" height="104" rx="16" fill="var(--surface)" stroke="var(--border-strong)"/>'
          + '<text x="' + (x + 16) + '" y="42" fill="var(--accent)" font-size="11" letter-spacing="1.5" font-family="var(--font-mono)">' + num + '</text>'
          + '<g transform="translate(' + (x + 60) + ',52)">' + icon + '</g>'
          + '<text x="' + (x + 70) + '" y="106" text-anchor="middle" fill="var(--text-dim)" font-size="12.5" font-family="var(--font-sans)">' + label + '</text>'
          + '</g>';
      }
      function gap(x, delay) {
        return '<line x1="' + (x + 4) + '" y1="72" x2="' + (x + 34) + '" y2="72" stroke="var(--border-strong)" stroke-width="1.5" stroke-dasharray="4 4"/>'
          + '<circle class="viz-dot" style="animation-delay:' + delay + 's" cx="' + x + '" cy="72" r="3" fill="var(--accent)"/>';
      }
      return '<svg viewBox="0 0 700 150" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">'
        + gap(150, 1.5) + gap(330, 3) + gap(510, 4.5)
        + stage(10, '01', 'Watch folder', icoFolder, 0)
        + stage(190, '02', 'Match rules', icoRules, 1.5)
        + stage(370, '03', 'Move file', icoMove, 3)
        + stage(550, '04', 'Journal move', icoLog, 4.5)
        + '</svg>';
    },

    // extension chips matching into destination folders
    rules: function () {
      function row(y, chip, folder, delay) {
        var d = 'style="animation-delay:' + delay + 's"';
        return '<g class="viz-row" ' + d + '>'
          + '<rect x="16" y="' + y + '" width="150" height="46" rx="12" fill="var(--surface-2)" stroke="var(--border-strong)"/>'
          + '<text x="91" y="' + (y + 29) + '" text-anchor="middle" fill="var(--text)" font-size="13" font-family="var(--font-mono)">' + chip + '</text>'
          + '<line x1="182" y1="' + (y + 23) + '" x2="292" y2="' + (y + 23) + '" stroke="var(--border-strong)" stroke-width="1.5" stroke-dasharray="4 4"/>'
          + '<path d="M285 ' + (y + 18) + ' L293 ' + (y + 23) + ' L285 ' + (y + 28) + '" fill="none" stroke="var(--border-strong)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
          + '<circle class="viz-mover" ' + d + ' cx="182" cy="' + (y + 23) + '" r="3" fill="var(--accent)"/>'
          + '<rect x="310" y="' + y + '" width="274" height="46" rx="12" fill="var(--surface)" stroke="var(--border-strong)"/>'
          + '<text x="447" y="' + (y + 29) + '" text-anchor="middle" fill="var(--text-dim)" font-size="13" font-family="var(--font-sans)">' + folder + '</text>'
          + '</g>';
      }
      return '<svg viewBox="0 0 600 210" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">'
        + row(12, '.jpg .png', 'Images', 0)
        + row(80, '.pdf .docx', 'Documents', 1.6)
        + row(148, 'archive.zip', 'Archives', 3.2)
        + '</svg>';
    },

    // configuration directory tree with a sweeping highlight
    tree: function () {
      var lines = [
        { t: '~/.config/gnu-sorter/', d: 0, root: true },
        { t: 'config.json', d: 1 },
        { t: 'folders.json', d: 1 },
        { t: 'presets.json', d: 1 },
        { t: 'rules/', d: 1, root: true },
        { t: 'default.json', d: 2 },
        { t: 'Downloads.json', d: 2 }
      ];
      var body = '';
      for (var i = 0; i < lines.length; i++) {
        var L = lines[i];
        var y = 34 + i * 30;
        var x = 30 + L.d * 24;
        var bullet = L.root
          ? '<rect x="' + (x - 24) + '" y="' + (y - 12) + '" width="16" height="13" rx="2.5" fill="none" stroke="var(--accent)" stroke-width="1.5"/><path d="M' + (x - 24) + ' ' + (y - 8) + 'h16" stroke="var(--accent)" stroke-width="1.5"/>'
          : '<rect x="' + (x - 21) + '" y="' + (y - 12) + '" width="12" height="15" rx="2" fill="none" stroke="var(--border-strong)" stroke-width="1.4"/>';
        body += '<g class="viz-line" style="animation-delay:' + (i * 0.5) + 's">' + bullet
          + '<text x="' + x + '" y="' + y + '" fill="' + (L.root ? 'var(--text)' : 'var(--text-dim)') + '" font-size="13.5" font-family="var(--font-mono)">' + L.t + '</text>'
          + '</g>';
      }
      return '<svg viewBox="0 0 560 260" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">'
        + '<rect x="1" y="1" width="558" height="258" rx="14" fill="var(--bg-soft)" stroke="var(--border)"/>'
        + body + '</svg>';
    },

    // journaled moves being undone
    undo: function () {
      var nodes = [
        { x: 90, t: 'report.pdf' },
        { x: 240, t: 'IMG_0421.jpg' },
        { x: 390, t: 'notes.md' },
        { x: 540, t: 'archive.zip' }
      ];
      var body = '';
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        body += '<g class="viz-tl-node" style="animation-delay:' + ((3 - i) * 0.35) + 's">'
          + '<circle cx="' + n.x + '" cy="120" r="6" fill="var(--accent)"/>'
          + '<text x="' + n.x + '" y="98" text-anchor="middle" fill="var(--text-faint)" font-size="11" font-family="var(--font-sans)">moved</text>'
          + '<text x="' + n.x + '" y="150" text-anchor="middle" fill="var(--text-dim)" font-size="12" font-family="var(--font-mono)">' + n.t + '</text>'
          + '</g>';
      }
      return '<svg viewBox="0 0 640 180" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">'
        + '<line x1="60" y1="120" x2="600" y2="120" stroke="var(--border-strong)" stroke-width="1.5"/>'
        + '<path class="viz-undo-path" d="M560 44 C 420 0, 180 0, 74 44" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="520"/>'
        + '<path d="M90 38 L74 44 L90 50" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        + body + '</svg>';
    }
  };

  if (document.querySelector('[data-viz]')) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-viz]'), function (el) {
      var fn = VIZ[el.getAttribute('data-viz')];
      if (fn) { el.insertAdjacentHTML('afterbegin', fn()); }
    });
  }

  /* ---------- Screenshot galleries (a shot hides itself only if its file is missing) ---------- */
  Array.prototype.forEach.call(document.querySelectorAll('.gallery'), function (gallery) {
    var shots = gallery.querySelectorAll('.shot');
    if (!shots.length) { return; }
    var section = gallery.closest ? gallery.closest('[data-shots-section]') : null;
    function sync() {
      var missing = gallery.querySelectorAll('.shot.is-missing').length;
      var empty = missing === shots.length;
      gallery.classList.toggle('is-empty', empty);
      if (section) { section.classList.toggle('is-empty', empty); }
    }
    function markMissing(img) {
      var shot = img.closest ? img.closest('.shot') : null;
      if (shot) { shot.classList.add('is-missing'); }
      sync();
    }
    Array.prototype.forEach.call(gallery.querySelectorAll('img[data-shot]'), function (img) {
      if (img.complete && img.naturalWidth === 0) { markMissing(img); return; }
      img.addEventListener('error', function () { markMissing(img); });
    });
  });
})();
