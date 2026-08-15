# -*- coding: utf-8 -*-
"""由 service-images.txt 重新生成 pull-service-images.sh。"""
from pathlib import Path

TXT = Path(r"D:\ctf-agent\src\tools\service-images.txt")
OUT = Path(r"D:\ctf-agent\src\tools\pull-service-images.sh")

images = [l.strip() for l in TXT.read_text(encoding="utf-8").splitlines() if l.strip()]

# 分块为可读的多行数组
lines = []
lines.append("#!/bin/bash")
lines.append("# pull-service-images.sh —— 批量预拉全部服务题镜像（由 regen-pull-script.py 自动生成）")
lines.append("# 用法：bash pull-service-images.sh [重试次数]")
lines.append("# 拉过的镜像留在 podman 本地存储，之后跑 benchmark 不需要 VPN。")
lines.append("")
lines.append("IMAGES=(")
for i in range(0, len(images), 4):
    lines.append("    " + " ".join(f'"{x}"' for x in images[i:i+4]))
lines.append(")")
lines.append("")
lines.append('''RETRIES="${1:-1}"
OK=0; FAIL=0; FAILED_LIST=()

for img in "${IMAGES[@]}"; do
    for ((i=1; i<=RETRIES; i++)); do
        if timeout 300 podman pull "docker.io/$img" >/dev/null 2>&1; then
            echo "OK   $img"
            OK=$((OK+1))
            break
        fi
        if [ "$i" -eq "$RETRIES" ]; then
            echo "FAIL $img"
            FAIL=$((FAIL+1))
            FAILED_LIST+=("$img")
        fi
    done
done

echo "========================================"
echo "完成：成功 $OK / 失败 $FAIL / 总计 ${#IMAGES[@]}"
if [ "$FAIL" -gt 0 ]; then
    echo "失败清单（可重跑）:"
    printf '%s\\n' "${FAILED_LIST[@]}"
fi''')

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"已生成 {OUT}：{len(images)} 个镜像")
