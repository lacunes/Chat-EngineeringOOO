/**
 * AI 角色扮演导演台 — 前端脚本
 * 功能：Flash 消息、二次确认、移动端菜单、CSRF 自动注入
 */

(function() {
  'use strict';

  // ── Flash 消息 ──
  // 读取 URL 参数中的 flash 消息并显示
  function showFlash() {
    var container = document.getElementById('flash-container');
    if (!container) return;

    var params = new URLSearchParams(window.location.search);
    var kind = params.get('flash_kind');
    var msg = params.get('flash_msg');

    if (msg) {
      container.className = 'flash-msg flash-' + (kind || 'success') + ' show';
      container.textContent = decodeURIComponent(msg);

      // 清理 URL 中的 flash 参数
      var url = new URL(window.location);
      url.searchParams.delete('flash_kind');
      url.searchParams.delete('flash_msg');
      history.replaceState(null, '', url);

      // 5秒后自动隐藏
      setTimeout(function() {
        container.className = 'flash-msg';
      }, 5000);
    }
  }

  // ── 二次确认 ──
  // 给带有 data-confirm 属性的表单/按钮绑定确认对话框
  function bindConfirms() {
    document.querySelectorAll('[data-confirm]').forEach(function(el) {
      el.addEventListener('click', function(e) {
        if (!confirm(el.getAttribute('data-confirm') || '确认此操作？')) {
          e.preventDefault();
          return false;
        }
      });
    });

    // 表单提交时也检查
    document.querySelectorAll('form[data-confirm]').forEach(function(form) {
      form.addEventListener('submit', function(e) {
        if (!confirm(form.getAttribute('data-confirm') || '确认此操作？')) {
          e.preventDefault();
          return false;
        }
      });
    });
  }

  // ── CSRF Token 自动注入表单 ──
  function injectCSRF() {
    var token = document.querySelector('meta[name="csrf-token"]');
    var tokenValue = token ? token.getAttribute('content') : '';

    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function(form) {
      // 如果已经有 csrf input 则跳过
      if (form.querySelector('input[name="_csrf_token"]')) return;

      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = '_csrf_token';
      input.value = tokenValue;
      form.appendChild(input);
    });
  }

  // ── 移动端菜单 ──
  function bindMobileMenu() {
    var btn = document.getElementById('mobile-menu-btn');
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');

    if (!btn || !sidebar) return;

    btn.addEventListener('click', function() {
      sidebar.classList.toggle('mobile-open');
      if (overlay) overlay.classList.toggle('show');
    });

    if (overlay) {
      overlay.addEventListener('click', function() {
        sidebar.classList.remove('mobile-open');
        overlay.classList.remove('show');
      });
    }
  }

  // ── 自动关闭模态框 ──
  function bindModals() {
    document.querySelectorAll('.modal-overlay').forEach(function(overlay) {
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) {
          overlay.classList.remove('show');
        }
      });
      // 关闭按钮
      var closeBtn = overlay.querySelector('.modal-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function() {
          overlay.classList.remove('show');
        });
      }
    });
  }

  // ── 页面加载完成后运行 ──
  document.addEventListener('DOMContentLoaded', function() {
    showFlash();
    bindConfirms();
    injectCSRF();
    bindMobileMenu();
    bindModals();
  });

})();
