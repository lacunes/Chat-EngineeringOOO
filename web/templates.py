"""Web 面板 HTML 渲染函数。

所有页面使用内联样式，零外部 CSS/JS 依赖。
结构：base() 提供外层骨架，各页面函数返回 body 内容。
"""

from config import settings


def base(title: str, body: str, refresh_sec: int = 0) -> str:
    """所有页面的外层 HTML 骨架。

    自动从 URL query string 读取 flash 参数并显示提示条（格式: flash=success:消息 或 flash=error:消息）。
    """
    refresh = f'<meta http-equiv="refresh" content="{refresh_sec}">' if refresh_sec else ""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Roleplay Bot</title>
{refresh}
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #333; }}
  .nav {{ background: #2c3e50; padding: 0 20px; display: flex; gap: 0; }}
  .nav a {{ color: #ecf0f1; text-decoration: none; padding: 14px 16px; font-size: 14px; }}
  .nav a:hover, .nav a.active {{ background: #34495e; }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
  h1 {{ font-size: 22px; margin-bottom: 16px; color: #2c3e50; }}
  h2 {{ font-size: 17px; margin: 20px 0 10px; color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 6px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .stat {{ display: inline-block; min-width: 200px; margin: 6px 12px 6px 0; }}
  .stat label {{ font-size: 12px; color: #888; display: block; }}
  .stat value {{ font-size: 18px; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 14px; }}
  th {{ background: #fafafa; font-weight: 600; color: #555; }}
  tr:hover {{ background: #f9f9f9; }}
  .msg-user {{ background: #e3f2fd; }}
  .msg-assistant {{ background: #fff; }}
  .msg-system {{ background: #fff3e0; font-size: 12px; color: #888; }}
  .role {{ font-size: 11px; color: #888; text-transform: uppercase; }}
  .btn {{ display: inline-block; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; text-decoration: none; }}
  .btn-primary {{ background: #3498db; color: #fff; }}
  .btn-danger {{ background: #e74c3c; color: #fff; }}
  .btn-sm {{ padding: 4px 10px; font-size: 12px; }}
  .btn:hover {{ opacity: .85; }}
  input, textarea {{ width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; margin: 6px 0 12px; }}
  textarea {{ resize: vertical; min-height: 80px; }}
  .log-line {{ font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 12px; padding: 2px 0; white-space: pre-wrap; word-break: break-all; }}
  .warn {{ color: #e67e22; }}
  .error {{ color: #e74c3c; }}
  .info {{ color: #2980b9; }}
  .flash {{ padding: 10px 16px; border-radius: 4px; margin-bottom: 12px; }}
  .flash-success {{ background: #d4edda; color: #155724; }}
  .flash-error {{ background: #f8d7da; color: #721c24; }}
  .empty {{ text-align: center; color: #aaa; padding: 40px 0; }}
  #flash-msg {{ display: none; }}
</style>
</head>
<body>
<div class="nav">
  <a href="/"{' class="active"' if title == "仪表盘" else ""}>📊 仪表盘</a>
  <a href="/memory"{' class="active"' if title.startswith("短期") else ""}>💬 短期记忆</a>
  <a href="/memory/long"{' class="active"' if title.startswith("长期") else ""}>🧠 长期记忆</a>
  <a href="/worlds"{' class="active"' if title.startswith("世界") else ""}>🌍 世界</a>
  <a href="/logs"{' class="active"' if title.startswith("日志") else ""}>📜 日志</a>
</div>
<div class="container">
<div id="flash-msg"></div>
{body}
</div>
<script>
(function() {{
  var m = location.search.match(/flash=([^&]+)/);
  if (m) {{
    var parts = decodeURIComponent(m[1]).split(':');
    var kind = parts[0], text = parts.slice(1).join(':');
    var el = document.getElementById('flash-msg');
    el.className = 'flash flash-' + (kind === 'error' ? 'error' : 'success');
    el.textContent = text;
    el.style.display = 'block';
    var url = new URL(location);
    url.searchParams.delete('flash');
    history.replaceState(null, '', url);
  }}
}})();
</script>
</body>
</html>"""


def flash(message: str, kind: str = "success") -> str:
    return f'<div class="flash flash-{kind}">{message}</div>'


# ── 仪表盘 ──────────────────────────────────────────

def dashboard(world_name: str, model: str, memory_count: int, long_count: int,
              npc_status: str, uptime: str, log_size: str) -> str:
    body = f"""
<h1>📊 仪表盘</h1>

<div class="card">
  <h2>运行状态</h2>
  <div class="stat"><label>当前世界</label><value>{world_name}</value></div>
  <div class="stat"><label>模型</label><value>{model}</value></div>
  <div class="stat"><label>运行时长</label><value>{uptime}</value></div>
  <div class="stat"><label>日志大小</label><value>{log_size}</value></div>
</div>

<div class="card">
  <h2>记忆概览</h2>
  <div class="stat"><label>短期记忆</label><value>{memory_count} 条</value></div>
  <div class="stat"><label>长期记忆</label><value>{long_count} 条</value></div>
</div>

<div class="card">
  <h2>NPC 系统</h2>
  <pre style="font-size:13px;line-height:1.6;white-space:pre-wrap;">{npc_status}</pre>
</div>

<div class="card">
  <h2>操作</h2>
  <form method="post" action="/reset" onsubmit="return confirm('确认清空当前世界所有记忆？此操作不可撤销！')" style="display:inline">
    <button class="btn btn-danger">🔄 重置当前世界记忆</button>
  </form>
</div>
"""
    return base("仪表盘", body)


# ── 短期记忆 ────────────────────────────────────────

def short_memory(messages: list, memory_count: int) -> str:
    if not messages:
        rows = '<tr><td colspan="3" class="empty">暂无短期记忆</td></tr>'
    else:
        rows = ""
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            # 截断过长内容
            if len(content) > 300:
                content = content[:300] + "…"
            role_class = f"msg-{role}" if role in ("user", "assistant", "system") else ""
            rows += f"""<tr class="{role_class}">
  <td style="width:50px;text-align:right;color:#aaa;">{i + 1}</td>
  <td style="width:70px;"><span class="role">{role}</span></td>
  <td>{content}</td>
</tr>"""

    body = f"""
<h1>💬 短期记忆</h1>
<p style="margin-bottom:12px;color:#888;">共 {memory_count} 条，最近 {settings.CONTEXT_LENGTH} 条会注入模型上下文。页面每 30 秒自动刷新。</p>

<div class="card">
  <table>{rows}</table>
</div>
"""
    return base("短期记忆", body, refresh_sec=30)


# ── 长期记忆 ────────────────────────────────────────

def long_memory(items: list, max_items: int) -> str:
    if not items:
        rows = '<tr><td colspan="3" class="empty">暂无长期记忆</td></tr>'
    else:
        rows = ""
        for i, item in enumerate(items):
            # 截断过长条目
            text = item if len(item) <= 200 else item[:200] + "…"
            rows += f"""<tr>
  <td style="width:40px;text-align:right;color:#aaa;">{i + 1}</td>
  <td>{text}</td>
  <td style="width:60px;text-align:center;">
    <form method="post" action="/memory/long/{i}/delete" style="display:inline">
      <button class="btn btn-danger btn-sm">删除</button>
    </form>
  </td>
</tr>"""

    body = f"""
<h1>🧠 长期记忆</h1>
<p style="margin-bottom:12px;color:#888;">共 {len(items)} 条（上限 {max_items} 条），最近 {settings.LONG_MEMORY_CONTEXT_LIMIT} 条会注入模型上下文。</p>

<div class="card">
  <h2>新增条目</h2>
  <form method="post" action="/memory/long">
    <textarea name="content" placeholder="输入新记忆内容…" required></textarea>
    <button class="btn btn-primary">💾 写入</button>
  </form>
</div>

<div class="card">
  <h2>现有条目</h2>
  <table>{rows}</table>
</div>

<div class="card">
  <form method="post" action="/memory/long/refine" style="display:inline">
    <button class="btn btn-primary">🔧 精炼长期记忆</button>
  </form>
  <span style="margin-left:8px;font-size:13px;color:#888;">去重合并，保留核心信息</span>
</div>
"""
    return base("长期记忆", body)


# ── 日志 ────────────────────────────────────────────

def logs_view(lines: list, file_path: str) -> str:
    if not lines:
        html_lines = '<div class="empty">暂无日志</div>'
    else:
        html_lines = ""
        for line in lines:
            # 简单高亮
            css = ""
            if "WARNING" in line:
                css = " warn"
            elif "ERROR" in line:
                css = " error"
            elif "INFO" in line:
                css = " info"
            html_lines += f'<div class="log-line{css}">{line}</div>'

    body = f"""
<h1>📜 日志</h1>
<p style="margin-bottom:12px;color:#888;">文件: {file_path}（最新 100 行，页面每 15 秒自动刷新）</p>

<div class="card">
  {html_lines}
</div>
"""
    return base("日志", body, refresh_sec=15)


# ── 世界管理 ────────────────────────────────────────

def world_list(worlds: list[dict], active: str) -> str:
    """世界文件列表页。

    worlds: [{"name": "one", "path": "worlds/one.py", "size_kb": 3.2, "is_active": True}, ...]
    """
    if not worlds:
        rows = '<tr><td colspan="4" class="empty">未找到世界文件</td></tr>'
    else:
        rows = ""
        for w in worlds:
            badge = ' <span style="background:#27ae60;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">当前</span>' if w["is_active"] else ""
            rows += f"""<tr>
  <td>{w['name']}{badge}</td>
  <td style="font-size:12px;color:#888;">{w['path']}</td>
  <td style="font-size:12px;color:#888;">{w['size_kb']:.1f} KB</td>
  <td style="text-align:center;">
    <a href="/worlds/{w['name']}" class="btn btn-primary btn-sm">编辑</a>
    {'<span class="btn btn-sm" style="background:#95a5a6;color:#fff;cursor:default;">已激活</span>' if w['is_active'] else f'<form method="post" action="/worlds/switch" style="display:inline"><input type="hidden" name="world" value="{w["name"]}"><button class="btn btn-sm" style="background:#f39c12;color:#fff;">切换</button></form>'}
  </td>
</tr>"""

    body = f"""
<h1>🌍 世界管理</h1>
<p style="margin-bottom:12px;color:#888;">切换世界会更新 .env 文件，需重启 Bot 生效。</p>

<div class="card">
  <table>{rows}</table>
</div>

<div class="card">
  <form method="post" action="/restart" onsubmit="return confirm('确认重启 Bot？Web 面板将暂时不可用。')" style="display:inline">
    <button class="btn btn-danger">🔄 重启 Bot</button>
  </form>
  <span style="margin-left:8px;font-size:13px;color:#888;">使世界切换生效（需 tmux/systemd 自动拉起）</span>
</div>
"""
    return base("世界管理", body)


def world_editor(name: str, content: str, file_path: str, error: str = "") -> str:
    """世界文件编辑页。"""
    lines = content.count("\n") + 1
    error_html = f'<div class="flash flash-error">{error}</div>' if error else ""
    body = f"""
<h1>🌍 编辑世界: {name}</h1>
<p style="margin-bottom:12px;color:#888;">文件: {file_path}（{lines} 行）</p>
{error_html}

<div class="card">
  <form method="post" action="/worlds/{name}">
    <textarea name="content" style="min-height:500px;font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;">{content}</textarea>
    <div style="margin-top:8px;display:flex;gap:8px;">
      <button class="btn btn-primary" onclick="return confirm('确认保存？语法错误可能导致世界加载失败。')">💾 保存</button>
      <a href="/worlds" class="btn" style="background:#95a5a6;color:#fff;">取消</a>
    </div>
  </form>
</div>
"""
    return base(f"世界编辑 - {name}", body)

