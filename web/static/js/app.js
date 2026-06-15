/**
 * 导演台 — 前端脚本
 * Flash 消息、二次确认、CSRF、记忆编辑
 */
(function() {
  'use strict';

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
    showFlash();
    bindConfirms();
    injectCSRF();
    bindMemoryEdit();
  });

})();
