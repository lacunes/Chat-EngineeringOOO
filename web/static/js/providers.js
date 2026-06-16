/**
 * 模型管理页交互。
 * 只读取后端输出的非敏感 provider 配置，不接收或回传 API Key 明文。
 */
(function() {
  'use strict';

  function text(el, value) {
    if (el) el.textContent = value == null ? '' : String(value);
  }

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function parseProviderData() {
    var node = document.getElementById('provider-data');
    if (!node) return {};
    try {
      var rows = JSON.parse(node.textContent || '[]');
      return rows.reduce(function(acc, item) {
        if (item && item.name) acc[item.name] = item;
        return acc;
      }, {});
    } catch (err) {
      return {};
    }
  }

  function postForm(url, formData) {
    var csrfToken = getCsrfToken();
    if (!formData.has('_csrf_token')) formData.append('_csrf_token', csrfToken);

    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRF-Token': csrfToken
      },
      body: formData.toString()
    }).then(function(response) {
      return response.text().then(function(bodyText) {
        var data;
        try {
          data = bodyText ? JSON.parse(bodyText) : {};
        } catch (err) {
          data = { ok: false, error: '服务器返回了非 JSON 响应' };
        }
        if (!response.ok) {
          data.ok = false;
          data.error = data.error || ('HTTP ' + response.status);
        }
        return data;
      });
    });
  }

  function setStatusHtml(el, kind, message) {
    if (!el) return;
    var color = kind === 'success' ? 'var(--success)' : kind === 'warning' ? 'var(--warning)' : 'var(--error)';
    el.innerHTML = '<span style="color:' + color + ';">' + escapeHtml(message) + '</span>';
  }

  function initProvidersPage() {
    var editorDiv = document.getElementById('provider-editor');
    var editorForm = document.getElementById('editor-form');
    if (!editorDiv || !editorForm) return;

    var providerData = parseProviderData();
    var urls = {
      add: editorDiv.getAttribute('data-add-url') || '',
      edit: editorDiv.getAttribute('data-edit-url') || '',
      test: editorDiv.getAttribute('data-test-url') || '',
      fetchModels: editorDiv.getAttribute('data-fetch-models-url') || ''
    };

    var editorTitle = document.getElementById('editor-title');
    var editorIsAdd = document.getElementById('editor-is-add');
    var btnAdd = document.getElementById('btn-add-provider');
    var btnCancel = document.getElementById('btn-cancel-edit');
    var btnFetch = document.getElementById('btn-fetch-models');
    var fetchStatus = document.getElementById('editor-fetch-status');
    var modelSelect = document.getElementById('editor-model-select');

    function input(id) {
      return document.getElementById(id);
    }

    function setInput(id, value) {
      var el = input(id);
      if (el) el.value = value == null ? '' : value;
    }

    function fillForm(data) {
      setInput('editor-model', data.model || '');
      setInput('editor-base-url', data.base_url || '');
      setInput('editor-api-key-env', data.api_key_env || '');
      setInput('editor-priority', data.priority || 99);
      setInput('editor-timeout-chat', data.timeout_chat_seconds || 60);
      setInput('editor-timeout-bg', data.timeout_background_seconds || 30);
      setInput('editor-max-retries', data.max_retries || 1);
      setInput('editor-cooldown', data.cooldown_seconds || 300);
      setInput('editor-max-fail', data.max_consecutive_failures || 3);

      var enabledCb = editorForm.querySelector('input[name="enabled"]');
      if (enabledCb) enabledCb.checked = data.enabled !== false;
      var quotaCb = editorForm.querySelector('input[name="disable_on_quota_exhausted"]');
      if (quotaCb) quotaCb.checked = data.disable_on_quota_exhausted !== false;
      var thinkCb = editorForm.querySelector('input[name="thinking_enabled"]');
      if (thinkCb) thinkCb.checked = data.thinking_enabled === true;

      var types = data.task_types || ['chat'];
      editorForm.querySelectorAll('input[name="task_types"]').forEach(function(cb) {
        cb.checked = types.indexOf(cb.value) >= 0;
      });
    }

    function resetModelSelect() {
      if (!modelSelect) return;
      modelSelect.style.display = 'none';
      modelSelect.innerHTML = '<option value="">- 选择模型 -</option>';
    }

    function clearForm() {
      editorForm.reset();
      setInput('editor-name', '');
      var nameInput = input('editor-name');
      if (nameInput) nameInput.readOnly = false;
      setInput('editor-priority', 99);
      setInput('editor-timeout-chat', 60);
      setInput('editor-timeout-bg', 30);
      setInput('editor-max-retries', 1);
      setInput('editor-cooldown', 300);
      setInput('editor-max-fail', 3);
      editorForm.querySelectorAll('input[name="task_types"]').forEach(function(cb) {
        cb.checked = cb.value === 'chat';
      });
      resetModelSelect();
      text(fetchStatus, '');
    }

    function showEditor(titleValue, actionUrl, isAdd) {
      text(editorTitle, titleValue);
      editorForm.action = actionUrl;
      if (editorIsAdd) editorIsAdd.value = isAdd ? '1' : '0';
      editorDiv.style.display = 'block';
      editorDiv.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function hideEditor() {
      editorDiv.style.display = 'none';
      clearForm();
    }

    function findResultDiv(providerName) {
      return Array.prototype.find.call(document.querySelectorAll('[data-provider-result]'), function(el) {
        return el.getAttribute('data-provider-result') === providerName;
      });
    }

    document.querySelectorAll('.test-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var name = btn.getAttribute('data-provider') || '';
        var resultDiv = findResultDiv(name);
        if (!resultDiv || !urls.test) return;

        resultDiv.style.display = 'block';
        setStatusHtml(resultDiv, 'warning', '测试中...');
        var oldText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中';

        var formData = new URLSearchParams();
        formData.append('name', name);

        postForm(urls.test, formData)
          .then(function(data) {
            if (data.ok) {
              setStatusHtml(
                resultDiv,
                'success',
                '成功！回复: "' + (data.reply || '') + '" | 延迟: ' + (data.latency_ms || 0) + 'ms | 模型: ' + (data.model || '')
              );
            } else {
              setStatusHtml(resultDiv, 'error', '失败: ' + (data.error || '未知错误') + ' (延迟: ' + (data.latency_ms || 0) + 'ms)');
            }
          })
          .catch(function(err) {
            setStatusHtml(resultDiv, 'error', '请求失败: ' + String(err));
          })
          .finally(function() {
            btn.disabled = false;
            btn.textContent = oldText;
          });
      });
    });

    if (btnAdd) {
      btnAdd.addEventListener('click', function() {
        clearForm();
        showEditor('添加 Provider', urls.add, true);
      });
    }

    if (btnCancel) btnCancel.addEventListener('click', hideEditor);

    document.querySelectorAll('.edit-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var name = btn.getAttribute('data-provider') || '';
        var data = providerData[name] || {};
        clearForm();
        setInput('editor-name', name);
        var nameInput = input('editor-name');
        if (nameInput) nameInput.readOnly = true;
        fillForm(data);
        showEditor('编辑 Provider: ' + name, urls.edit, false);
      });
    });

    if (modelSelect) {
      modelSelect.addEventListener('change', function() {
        if (modelSelect.value) setInput('editor-model', modelSelect.value);
      });
    }

    if (btnFetch) {
      btnFetch.addEventListener('click', function() {
        var baseUrl = (input('editor-base-url') || {}).value || '';
        var apiKeyEnv = (input('editor-api-key-env') || {}).value || '';
        var providerName = (input('editor-name') || {}).value || '';
        baseUrl = baseUrl.trim();
        apiKeyEnv = apiKeyEnv.trim();
        providerName = providerName.trim();

        if (!baseUrl) {
          setStatusHtml(fetchStatus, 'error', '请先填写 API 地址 (base_url)');
          return;
        }
        if (!apiKeyEnv) {
          setStatusHtml(fetchStatus, 'error', '请先填写 api_key_env，并在 .env 中配置对应环境变量');
          return;
        }

        btnFetch.disabled = true;
        text(fetchStatus, '获取中...');
        resetModelSelect();

        var formData = new URLSearchParams();
        formData.append('base_url', baseUrl);
        formData.append('api_key_env', apiKeyEnv);
        formData.append('name', providerName);

        postForm(urls.fetchModels, formData)
          .then(function(data) {
            if (data.ok && data.models && data.models.length > 0) {
              resetModelSelect();
              data.models.forEach(function(modelName) {
                var opt = document.createElement('option');
                opt.value = modelName;
                opt.textContent = modelName;
                modelSelect.appendChild(opt);
              });
              modelSelect.style.display = 'block';
              setStatusHtml(fetchStatus, 'success', '获取到 ' + data.models.length + ' 个模型');
            } else {
              setStatusHtml(fetchStatus, 'error', data.error || '未返回模型列表');
            }
          })
          .catch(function(err) {
            setStatusHtml(fetchStatus, 'error', '请求失败: ' + String(err));
          })
          .finally(function() {
            btnFetch.disabled = false;
          });
      });
    }
  }

  document.addEventListener('DOMContentLoaded', initProvidersPage);
})();
