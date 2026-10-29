#!/usr/bin/env bash
set -euo pipefail
project_dir=$(dirname -- "$(readlink -f -- "$0")")
bin_dir="$HOME/.local/bin"
app_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p -- "$bin_dir" "$app_dir"
if [ -e "$bin_dir/sc830" ] && [ "$(readlink -f -- "$bin_dir/sc830")" != "$project_dir/run.sh" ]; then
  printf '%s\n' '已有其他 sc830 命令，请先选择安装位置。' >&2
  exit 1
fi
ln -sfn -- "$project_dir/run.sh" "$bin_dir/sc830"
python3 - "$app_dir/sc830.desktop" "$project_dir/run.sh" <<'PY'
from pathlib import Path
import sys
launcher = sys.argv[2].replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
Path(sys.argv[1]).write_text('[Desktop Entry]\nType=Application\nName=SC830 鼠标控制中心\n'
    'Comment=DPI、回报率、灯效、按键和宏配置\nExec="' + launcher + '" gui\n'
    'Icon=input-mouse\nTerminal=false\nCategories=Settings;HardwareSettings;\n'
    'Keywords=mouse;AULA;SC830;DPI;鼠标;\n', encoding='utf-8')
PY
printf '%s\n' '已安装 sc830 命令和 SC830 鼠标控制中心桌面入口。'
