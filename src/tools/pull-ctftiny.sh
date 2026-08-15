#!/bin/bash
# pull-ctftiny.sh —— 只拉 CTFTiny 50 题需要的镜像（磁盘紧张版）
# 用法：sudo bash pull-ctftiny.sh [重试次数]

IMAGES=(
    "llmctf/2017f-cry-lupin:latest" "llmctf/2017q-pwn-pilot:latest" "llmctf/2018f-msc-showdown:latest" "llmctf/2018q-misc-algebra:latest"
    "llmctf/2018q-pwn-bigboi:latest" "llmctf/2019q-cry-super_curve:latest" "llmctf/2019q-pwn-baby_boi:latest" "llmctf/2019q-pwn-got_milk:latest"
    "llmctf/2019q-rev-gibberish_check:latest" "llmctf/2020q-pwn-roppity:latest" "llmctf/2020q-pwn-slithery:latest" "llmctf/2021f-rev-maze:latest"
    "llmctf/2021q-pwn-password_checker:latest" "llmctf/2021q-web-poem_collection:latest" "llmctf/2022f-cry-open_ellipti_ph:latest" "llmctf/2022f-cry-polly_crack_this:latest"
    "llmctf/2022f-cry-the_lengths_we_extend_ourselves:latest" "llmctf/2022q-cry-beyond_quantum:latest" "llmctf/2022q-rev-the_big_bang:latest" "llmctf/2023f-cry-describeme:latest"
    "llmctf/2023f-web-shreeramquest-nginx:latest" "llmctf/2023f-web-shreeramquest-nodeserver:latest" "llmctf/2023q-msc-android_dropper:latest" "llmctf/2023q-pwn-puffin:latest"
    "llmctf/2023q-pwn-target_practice:latest" "llmctf/2023q-pwn-unlimited_subway:latest" "llmctf/2023q-web-smug_dino:latest" "mongo:latest"
)

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
    printf '%s\n' "${FAILED_LIST[@]}"
fi

