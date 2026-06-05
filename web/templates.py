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
  .mode-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
  .mode-bar a {{ font-size: 13px; color: #888; text-decoration: none; }}
  .mode-bar a:hover {{ color: #3498db; }}
  .rel-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .rel-table th {{ font-size: 11px; padding: 6px 4px; text-align: center; }}
  .rel-table td {{ padding: 4px; vertical-align: middle; }}
  .rel-table input[type=number] {{ width: 56px; padding: 4px; margin: 0; text-align: center; font-size: 13px; }}
  .rel-table input[type=text] {{ width: 80px; padding: 4px; margin: 0; font-size: 13px; }}
  .rel-table textarea {{ width: 140px; padding: 4px; margin: 0; font-size: 12px; min-height: 40px; }}
  .rel-table .btn {{ padding: 3px 8px; font-size: 11px; }}
  .memory-card {{ background: #fff; border-left: 3px solid #3498db; border-radius: 6px; padding: 12px 16px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
  .memory-card .mem-index {{ font-size: 11px; color: #aaa; }}
  .memory-card .mem-text {{ font-size: 14px; line-height: 1.7; margin: 6px 0; }}
  .memory-card .mem-actions {{ text-align: right; }}
  .world-field {{ margin-bottom: 16px; }}
  .world-field label {{ display: block; font-weight: 600; font-size: 14px; color: #2c3e50; margin-bottom: 4px; }}
  .world-field .hint {{ font-size: 11px; color: #aaa; margin-bottom: 4px; }}
  .world-field textarea {{ font-size: 13px; line-height: 1.6; }}
  .world-field input[type=text] {{ font-size: 14px; }}
</style>
</head>
<body>
<div class="nav">
  <a href="/"{' class="active"' if title == "仪表盘" else ""}>📊 仪表盘</a>
  <a href="/memory"{' class="active"' if title.startswith("短期") else ""}>💬 短期记忆</a>
  <a href="/memory/long"{' class="active"' if title.startswith("长期") else ""}>🧠 长期记忆</a>
  <a href="/worlds"{' class="active"' if title.startswith("世界") else ""}>🌍 世界</a>
  <a href="/relations"{' class="active"' if title.startswith("关系") else ""}>💞 关系</a>
  <a href="/time"{' class="active"' if title.startswith("时间") else ""}>⏰ 时间</a>
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

def long_memory_cards(items: list, max_items: int) -> str:
    """长期记忆：卡片展示模式。"""
    if not items:
        cards = '<div class="empty">暂无长期记忆</div>'
    else:
        cards = ""
        for i, item in enumerate(items):
            cards += f"""<div class="memory-card">
  <div class="mem-index">#{i + 1}</div>
  <div class="mem-text">{item}</div>
  <div class="mem-actions">
    <form method="post" action="/memory/long/{i}/delete" style="display:inline">
      <button class="btn btn-danger btn-sm">删除</button>
    </form>
  </div>
</div>"""

    body = f"""
<h1>🧠 长期记忆</h1>
<div class="mode-bar">
  <span style="color:#888;font-size:13px;">共 {len(items)} 条（上限 {max_items} 条），最近 {settings.LONG_MEMORY_CONTEXT_LIMIT} 条注入上下文</span>
</div>

<div class="card">
  <h2>新增条目</h2>
  <form method="post" action="/memory/long">
    <textarea name="content" placeholder="输入新记忆内容…" required></textarea>
    <button class="btn btn-primary">💾 写入</button>
  </form>
</div>

<div class="card">
  <h2>现有条目</h2>
  {cards}
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


def world_form(name: str, fields: dict, file_path: str, error: str = "") -> str:
    """世界编辑：表单模式。

    fields: {"WORLD_NAME": "one", "START_SCENE": "...", "SYSTEM_PROMPT": "...",
             "CHARACTERS": "角色A: 描述A\\n角色B: 描述B", ...}
    """
    error_html = f'<div class="flash flash-error">{error}</div>' if error else ""

    def _field(key: str, label: str, value: str, hint: str = "", rows: int = 4) -> str:
        v = value or ""
        return f"""<div class="world-field">
  <label>{label}</label>
  <div class="hint">{hint}</div>
  <textarea name="field_{key}" rows="{rows}">{v}</textarea>
</div>"""

    npc_json = fields.get("NPCS", "")
    body = f"""
<h1>🌍 编辑世界: {name}</h1>
<div class="mode-bar">
  <span style="color:#888;font-size:13px;">表单模式 — 文件: {file_path}</span>
  <a href="/worlds/{name}?mode=raw">📝 高级模式（源码编辑）</a>
</div>
{error_html}

<form method="post" action="/worlds/{name}">
<input type="hidden" name="mode" value="form">

<div class="card">
  <div class="world-field">
    <label>世界名</label>
    <input type="text" name="field_WORLD_NAME" value="{fields.get('WORLD_NAME',name)}" style="width:200px;">
  </div>

  {_field("START_SCENE", "开场场景", fields.get("START_SCENE",""), "用户 /start 时显示的文本", 6)}
  {_field("SYSTEM_PROMPT", "系统提示词", fields.get("SYSTEM_PROMPT",""), "世界观、人物关系、叙事规则的核心入口", 12)}
  {_field("CHARACTERS", "角色设定", fields.get("CHARACTERS",""), "每行: 角色名: 描述", 4)}
  {_field("RULES", "剧情规则", fields.get("RULES",""), "每行一条规则", 4)}
  {_field("LOCATIONS", "地点设定", fields.get("LOCATIONS",""), "每行: 地点名: 描述", 4)}
  {_field("EVENT_POOL", "事件池", fields.get("EVENT_POOL",""), "每行一个随机事件", 4)}

  <div class="world-field">
    <label>NPC 配置</label>
    <div class="hint">JSON 格式，参见 worlds/one.py 中的 NPCS 注释示例</div>
    <textarea name="field_NPCS" rows="10" style="font-family:'Cascadia Code','Fira Code',monospace;font-size:12px;">{npc_json}</textarea>
  </div>

  <div style="margin-top:12px;display:flex;gap:8px;">
    <button class="btn btn-primary" onclick="return confirm('确认保存？')">💾 保存</button>
    <a href="/worlds" class="btn" style="background:#95a5a6;color:#fff;">取消</a>
  </div>
</div>
</form>
"""
    return base(f"世界编辑 - {name}", body)


def world_editor_raw(name: str, content: str, file_path: str, error: str = "") -> str:
    """世界文件编辑：源码模式（高级）。"""
    lines = content.count("\n") + 1
    error_html = f'<div class="flash flash-error">{error}</div>' if error else ""
    body = f"""
<h1>🌍 编辑世界: {name}</h1>
<div class="mode-bar">
  <span style="color:#888;font-size:13px;">源码编辑模式 — {lines} 行</span>
  <a href="/worlds/{name}">📋 普通模式（表单编辑）</a>
</div>
{error_html}

<div class="card">
  <form method="post" action="/worlds/{name}">
    <input type="hidden" name="mode" value="raw">
    <textarea name="content" style="min-height:500px;font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;">{content}</textarea>
    <div style="margin-top:8px;display:flex;gap:8px;">
      <button class="btn btn-primary" onclick="return confirm('确认保存？语法错误可能导致世界加载失败。')">💾 保存</button>
      <a href="/worlds" class="btn" style="background:#95a5a6;color:#fff;">取消</a>
    </div>
  </form>
</div>
"""
    return base(f"世界编辑 - {name}", body)


# ── 关系网络 ────────────────────────────────────────

DIM_LABELS = [("affection","好感"),("trust","信任"),("fear","畏惧"),
              ("dependence","依赖"),("suspicion","怀疑"),("hostility","敌意")]


def relations_page_structured(world_name: str, relations: dict, error: str = "") -> str:
    """关系网络：表格 + 数字输入。"""
    error_html = f'<div class="flash flash-error">{error}</div>' if error else ""

    rows = ""
    idx = 0
    for key, rel in relations.items():
        parts = key.split("->", 1)
        frm = parts[0].strip() if len(parts) > 0 else ""
        to = parts[1].strip() if len(parts) > 1 else ""
        notes = "\n".join(rel.get("notes", []))
        cells = "".join(
            f'<td><input type="number" name="rel_{idx}_{dim}" value="{rel.get(dim,0)}" min="0" max="110"></td>'
            for dim, _ in DIM_LABELS
        )
        rows += f"""<tr>
  <td><input type="text" name="rel_{idx}_from" value="{frm}" style="width:70px;"></td>
  <td><input type="text" name="rel_{idx}_to" value="{to}" style="width:70px;"></td>
  {cells}
  <td><textarea name="rel_{idx}_notes" placeholder="备注，一行一条">{notes}</textarea></td>
  <td><label style="font-size:11px;white-space:nowrap;"><input type="checkbox" name="rel_{idx}_delete" value="1"> 删</label></td>
</tr>"""
        idx += 1

    header_cells = "".join(f'<th>{label}</th>' for _, label in DIM_LABELS)

    body = f"""
<h1>💞 关系网络: {world_name}</h1>
<div class="mode-bar">
  <span style="color:#888;font-size:13px;">表格编辑模式（0-100 正常，设为 110 可锁死该维度不再自动变化）</span>
  <a href="/relations?mode=raw">📝 高级模式（JSON 编辑）</a>
</div>
{error_html}

<div class="card">
  <form method="post" action="/relations">
  <input type="hidden" name="mode" value="structured">
  <table class="rel-table">
  <tr><th>从</th><th>到</th>{header_cells}<th>备注</th><th></th></tr>
  {rows if rows else '<tr><td colspan="10" class="empty">暂无关系数据</td></tr>'}
  </table>

  <h2 style="margin-top:20px;">新增关系</h2>
  <table class="rel-table">
  <tr>
    <td><input type="text" name="new_from" placeholder="角色A" style="width:70px;"></td>
    <td><input type="text" name="new_to" placeholder="角色B" style="width:70px;"></td>
    {"".join(f'<td><input type="number" name="new_{dim}" value="0" min="0" max="110"></td>' for dim, _ in DIM_LABELS)}
    <td><textarea name="new_notes" placeholder="备注" style="width:140px;"></textarea></td>
    <td></td>
  </tr>
  </table>

  <div style="margin-top:12px;">
    <button class="btn btn-primary">💾 保存全部</button>
    <span style="margin-left:8px;font-size:12px;color:#888;">（勾选"删"的行将被移除）</span>
  </div>
  </form>
</div>
"""
    return base("关系网络", body)


def relations_page_raw(world_name: str, json_text: str, error: str = "") -> str:
    """关系网络：原始 JSON 编辑器（高级模式）。"""
    lines = json_text.count("\n") + 1
    error_html = f'<div class="flash flash-error">{error}</div>' if error else ""
    body = f"""
<h1>💞 关系网络: {world_name}</h1>
<div class="mode-bar">
  <span style="color:#888;font-size:13px;">JSON 编辑模式</span>
  <a href="/relations">📋 普通模式（表格编辑）</a>
</div>
{error_html}
<div class="card">
  <p style="font-size:13px;color:#555;margin-bottom:8px;">
  affection=好感 | trust=信任 | fear=畏惧 | dependence=依赖 | suspicion=怀疑 | hostility=敌意
  </p>
  <form method="post" action="/relations">
    <input type="hidden" name="mode" value="raw">
    <textarea name="content" style="min-height:400px;font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;">{json_text}</textarea>
    <div style="margin-top:8px;">
      <button class="btn btn-primary" onclick="return confirm('确认保存？')">💾 保存</button>
    </div>
  </form>
</div>
"""
    return base("关系网络", body)


# ── 时间状态 ────────────────────────────────────────

TIME_PERIOD_OPTIONS = ["清晨", "上午", "中午", "下午", "傍晚", "夜晚", "深夜"]
SEASON_OPTIONS = ["春", "夏", "秋", "冬"]


def time_page(world_name: str, day: int, time_period: str, season: str, recent_days: list[str]) -> str:
    period_select = "".join(
        f'<option value="{p}"{" selected" if p == time_period else ""}>{p}</option>'
        for p in TIME_PERIOD_OPTIONS
    )
    season_select = "".join(
        f'<option value="{s}"{" selected" if s == season else ""}>{s}</option>'
        for s in SEASON_OPTIONS
    )
    notes_text = "\n".join(recent_days)
    notes_rows = ""
    for i, note in enumerate(recent_days):
        notes_rows += f"""<tr>
  <td style="width:40px;text-align:right;color:#aaa;">{i + 1}</td>
  <td>{note}</td>
  <td style="width:60px;text-align:center;">
    <form method="post" action="/time/{i}/delete" style="display:inline">
      <button class="btn btn-danger btn-sm">删除</button>
    </form>
  </td>
</tr>"""

    body = f"""
<h1>⏰ 时间状态: {world_name}</h1>

<div class="card">
  <form method="post" action="/time">
  <input type="hidden" name="action" value="save">
  <div style="display:flex;gap:20px;align-items:end;flex-wrap:wrap;">
    <div class="world-field" style="flex:0 0 auto;">
      <label>天数</label>
      <input type="number" name="day" value="{day}" min="1" style="width:80px;">
    </div>
    <div class="world-field" style="flex:0 0 auto;">
      <label>时段</label>
      <select name="time_period" style="padding:8px 12px;border:1px solid #ddd;border-radius:4px;font-size:14px;">{period_select}</select>
    </div>
    <div class="world-field" style="flex:0 0 auto;">
      <label>季节</label>
      <select name="season" style="padding:8px 12px;border:1px solid #ddd;border-radius:4px;font-size:14px;">{season_select}</select>
    </div>
    <div style="flex:0 0 auto;padding-bottom:12px;">
      <button class="btn btn-primary">💾 保存</button>
    </div>
  </div>
  </form>
</div>

<div class="card">
  <div style="display:flex;gap:8px;margin-bottom:12px;">
    <form method="post" action="/time" style="display:inline">
      <input type="hidden" name="action" value="advance_period">
      <button class="btn btn-primary">⏭ 推进时段</button>
    </form>
    <form method="post" action="/time" style="display:inline">
      <input type="hidden" name="action" value="advance_day">
      <button class="btn" style="background:#f39c12;color:#fff;">📅 推进一天</button>
    </form>
  </div>
</div>

<div class="card">
  <h2>近日摘要</h2>
  <form method="post" action="/time">
  <input type="hidden" name="action" value="save">
  <textarea name="recent_days" rows="6" style="font-size:13px;">{notes_text}</textarea>
  <input type="hidden" name="day" value="{day}">
  <input type="hidden" name="time_period" value="{time_period}">
  <input type="hidden" name="season" value="{season}">
  <button class="btn btn-primary" style="margin-top:8px;">💾 保存摘要</button>
  </form>
  {f'<table style="margin-top:12px;">{notes_rows}</table>' if notes_rows else '<p style="color:#aaa;margin-top:12px;">暂无摘要</p>'}
</div>
"""
    return base("时间状态", body)

