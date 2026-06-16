/**
 * 导演台 — 前端脚本
 * Flash 消息、二次确认、CSRF、记忆编辑、主题与移动端导航
 */
(function() {
  'use strict';

  function safelyGetStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function safelySetStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      // localStorage may be unavailable in private or locked-down browsers.
    }
  }

  function applyTheme(theme) {
    var html = document.documentElement;
    var iconLight = document.getElementById('theme-icon-light');
    var iconDark = document.getElementById('theme-icon-dark');
    var next = theme === 'dark' ? 'dark' : 'light';

    html.setAttribute('data-theme', next);
    if (iconLight) iconLight.classList.toggle('hidden', next === 'dark');
    if (iconDark) iconDark.classList.toggle('hidden', next !== 'dark');
  }

  function bindThemeToggle() {
    var btn = document.getElementById('theme-toggle');
    applyTheme(safelyGetStorage('theme') || 'light');
    if (!btn) return;

    btn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      safelySetStorage('theme', next);
    });
  }

  function bindMobileSidebar() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    var btn = document.getElementById('mobile-menu-btn');
    if (!sidebar || !overlay || !btn) return;

    function openSidebar() {
      sidebar.classList.add('open');
      overlay.classList.add('open');
      document.body.classList.add('sidebar-open');
      btn.setAttribute('aria-expanded', 'true');
    }

    function closeSidebar() {
      sidebar.classList.remove('open');
      overlay.classList.remove('open');
      document.body.classList.remove('sidebar-open');
      btn.setAttribute('aria-expanded', 'false');
    }

    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      if (sidebar.classList.contains('open')) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });

    overlay.addEventListener('click', closeSidebar);
    sidebar.querySelectorAll('.sidebar-nav a').forEach(function(link) {
      link.addEventListener('click', function() {
        if (window.innerWidth <= 768) closeSidebar();
      });
    });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closeSidebar();
    });

    window.addEventListener('resize', function() {
      if (window.innerWidth > 768) closeSidebar();
    });
  }

  function bindTracebackToggle() {
    document.querySelectorAll('[data-toggle-traceback]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var targetId = btn.getAttribute('data-toggle-traceback');
        var el = targetId ? document.getElementById(targetId) : document.getElementById('full-traceback');
        if (el) el.classList.toggle('hidden');
      });
    });
  }

  // ── Flash 消息 ──
  function showFlash() {
    var container = document.getElementById('flash-container');
    if (!container) return;

    var params = new URLSearchParams(window.location.search);
    var kind = params.get('flash_kind');
    var msg = params.get('flash_msg');

    if (msg) {
      container.className = 'flash-msg flash-' + (kind || 'success');
      container.textContent = decodeURIComponent(msg);

      var url = new URL(window.location);
      url.searchParams.delete('flash_kind');
      url.searchParams.delete('flash_msg');
      history.replaceState(null, '', url);

      setTimeout(function() {
        container.className = 'flash-msg';
      }, 5000);
    }
  }

  // ── 二次确认 ──
  function bindConfirms() {
    document.querySelectorAll('[data-confirm]').forEach(function(el) {
      el.addEventListener('click', function(e) {
        if (!confirm(el.getAttribute('data-confirm') || '确认此操作？')) {
          e.preventDefault();
          return false;
        }
      });
    });

    document.querySelectorAll('form[data-confirm]').forEach(function(form) {
      form.addEventListener('submit', function(e) {
        if (!confirm(form.getAttribute('data-confirm') || '确认此操作？')) {
          e.preventDefault();
          return false;
        }
      });
    });
  }

  // ── CSRF Token 自动注入 ──
  function injectCSRF() {
    var token = document.querySelector('meta[name="csrf-token"]');
    var tokenValue = token ? token.getAttribute('content') : '';

    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function(form) {
      if (form.querySelector('input[name="_csrf_token"]')) return;
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = '_csrf_token';
      input.value = tokenValue;
      form.appendChild(input);
    });
  }

  // ── 记忆卡片编辑 ──
  function bindMemoryEdit() {
    document.querySelectorAll('.mem-edit-btn').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.preventDefault();
        var card = btn.closest('.memory-card');
        if (!card) return;
        var display = card.querySelector('.mem-display');
        var editor = card.querySelector('.mem-editor');
        var textarea = card.querySelector('.mem-textarea');
        if (!display || !editor || !textarea) return;

        textarea.value = (display.textContent || '').trim();
        display.classList.add('hidden');
        editor.classList.remove('hidden');
      });
    });

    document.querySelectorAll('.mem-cancel-btn').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.preventDefault();
        var card = btn.closest('.memory-card');
        if (!card) return;
        var display = card.querySelector('.mem-display');
        var editor = card.querySelector('.mem-editor');
        if (!display || !editor) return;

        display.classList.remove('hidden');
        editor.classList.add('hidden');
      });
    });
  }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', function() {
    bindThemeToggle();
    bindMobileSidebar();
    showFlash();
    bindConfirms();
    injectCSRF();
    bindMemoryEdit();
    bindTracebackToggle();
  });

})();
