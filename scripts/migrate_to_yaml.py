"""一次性：JSON → YAML 迁移脚本"""
import json
import yaml
from pathlib import Path

data_dir = Path("data/worlds")


def yaml_quote(s: str) -> str:
    """判断是否需要引号包裹，返回安全字符串。"""
    if not s:
        return "''"
    # 需要引号的特殊字符
    special = set(":#{}[]&*?!|-><=!%%@`")

    if s.startswith(" ") or s.endswith(" "):
        return json.dumps(s, ensure_ascii=False)
    if any(c in s for c in special) or s[0] in "'\"":
        return json.dumps(s, ensure_ascii=False)
    if s.lower() in ("true", "false", "null", "yes", "no", "on", "off") or s.isdigit():
        return json.dumps(s, ensure_ascii=False)
    return s


for json_file in sorted(data_dir.glob("*.json")):
    name = json_file.stem
    data = json.loads(json_file.read_text(encoding="utf-8"))

    yaml_path = data_dir / f"{name}.yaml"
    lines = []

    field_order = ["WORLD_NAME", "START_SCENE", "SYSTEM_PROMPT",
                   "CHARACTERS", "RULES", "LOCATIONS", "EVENT_POOL", "NPCS"]

    for key in field_order:
        value = data.get(key, "")

        if key == "WORLD_NAME":
            lines.append(f"{key}: {yaml_quote(str(value))}")
            lines.append("")

        elif key in ("START_SCENE", "SYSTEM_PROMPT"):
            text = str(value) if value else ""
            if "\n" in text:
                lines.append(f"{key}: |")
                for line in text.split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"{key}: {yaml_quote(text)}")
            lines.append("")

        elif key in ("CHARACTERS", "LOCATIONS"):
            d = value if isinstance(value, dict) else {}
            lines.append(f"{key}:")
            if d:
                for k, v in d.items():
                    v_str = str(v)
                    lines.append(f"  {yaml_quote(k)}: {yaml_quote(v_str)}")
            else:
                lines.append("  {}")
            lines.append("")

        elif key in ("RULES", "EVENT_POOL"):
            lst = value if isinstance(value, list) else []
            lines.append(f"{key}:")
            if lst:
                for item in lst:
                    lines.append(f"  - {yaml_quote(str(item))}")
            else:
                lines.append("  []")
            lines.append("")

        elif key == "NPCS":
            d = value if isinstance(value, dict) else {}
            if d:
                lines.append(f"{key}:")
                npcs_yaml = yaml.dump(d, allow_unicode=True, default_flow_style=False,
                                      sort_keys=False, indent=2)
                for line in npcs_yaml.strip().split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"{key}: {{}}")
            lines.append("")

    yaml_text = "\n".join(lines) + "\n"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    print(f"  {name}.yaml  ({yaml_path.stat().st_size} bytes)")

print("\nMigration complete!")
