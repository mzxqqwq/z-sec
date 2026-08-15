# -*- coding: utf-8 -*-
"""生成 CTFTiny 50 题专用的拉取脚本（只拉这批题需要的镜像）。"""
import json
from pathlib import Path

MANIFEST = Path(r"D:\ctf-agent\src\tools\service-manifest.json")
OUT = Path(r"D:\ctf-agent\src\tools\pull-ctftiny.sh")

m = json.loads(MANIFEST.read_text(encoding="utf-8"))
imgs = set()
compose_count = 0
for key, entry in m.items():
    if "benchmarks\\ctftiny" not in entry.get("root", "").replace("/", "\\"):
        continue
    compose_count += 1
    for svc in entry.get("services", []):
        imgs.add(svc["normalized"])
imgs = sorted(imgs)

lines = ["#!/bin/bash",
         "# pull-ctftiny.sh —— 只拉 CTFTiny 50 题需要的镜像（磁盘紧张版）",
         "# 用法：sudo bash pull-ctftiny.sh [重试次数]",
         ""]
lines.append("IMAGES=(")
for i in range(0, len(imgs), 4):
    lines.append("    " + " ".join(f'"{x}"' for x in imgs[i:i+4]))
lines.append(")")
lines.append("""
RETRIES="${1:-1}"
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
fi
""")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"CTFTiny: {compose_count} 个 compose → {len(imgs)} 个唯一镜像")
for i in imgs:
    print("  ", i)
