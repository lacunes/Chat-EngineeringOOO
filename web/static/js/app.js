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

    function openMenu() {
      sidebar.classList.add('mobile-open');
      if (overlay) overlay.classList.add('show');
      document.body.style.overflow = 'hidden'; // 防止背景滚动
    }

    function closeMenu() {
      sidebar.classList.remove('mobile-open');
      if (overlay) overlay.classList.remove('show');
      document.body.style.overflow = '';
    }

    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (sidebar.classList.contains('mobile-open')) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    if (overlay) {
      overlay.addEventListener('click', function() {
        closeMenu();
      });
    }

    // 点击侧栏内的导航链接后自动关闭
    sidebar.querySelectorAll('a').forEach(function(link) {
      link.addEventListener('click', function() {
        // 延迟一点关闭，让浏览器有时间处理导航
        setTimeout(closeMenu, 150);
      });
    });
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

  // ── 记忆卡片编辑切换 ──
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

  // ── 页面加载完成后运行 ──
  document.addEventListener('DOMContentLoaded', function() {
    showFlash();
    bindConfirms();
    injectCSRF();
    bindMobileMenu();
    bindModals();
    bindMemoryEdit();
  });

})();
