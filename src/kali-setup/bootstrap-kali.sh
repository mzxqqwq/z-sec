#!/usr/bin/env bash
# Kali 执行环境一键初始化 —— CTF agent 工具链（Jeopardy: web/pwn/re/crypto/misc）
# 用法：bash bootstrap-kali.sh [--with-sagemath] [--with-docker] [--with-cairn]
# 幂等可重跑；国内网络默认走清华/阿里镜像。
set -euo pipefail

log() { echo -e "\n\033[1;36m==> $1\033[0m"; }

# ---------- 镜像源（国内加速） ----------
log "配置 apt 镜像源（Kali 官方源较慢，切换国内源）"
if ! grep -q "mirrors.tuna" /etc/apt/sources.list 2>/dev/null; then
    sudo sed -i 's|http.kali.org|mirrors.tuna.tsinghua.edu.cn/kali|g' /etc/apt/sources.list 2>/dev/null || true
fi

# ---------- apt 工具 ----------
log "apt update"
sudo apt-get update -y

log "安装核心安全工具"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git curl wget unzip p7zip-full \
    nmap netcat-openbsd socat dnsutils whois \
    gdb gcc g++ make binutils file strings ltrace strace \
    binwalk foremost steghide zsteg exiftool \
    john hashcat hydra \
    sqlmap ffuf gobuster \
    radare2 \
    tcpdump wireshark-common tshark \
    openssl || true

# ---------- pip 工具（清华源） ----------
log "pip 工具链（清华源）"
pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
pip3 install --break-system-packages --upgrade pip 2>/dev/null || pip3 install --upgrade pip || true
pip3 install --break-system-packages \
    pwntools z3-solver sympy angr scapy pycryptodome gmpy2 \
    requests beautifulsoup4 lxml flask \
    volatility3 capstone unicorn ropper ROPgadget \
    ciphey pyzipper msoffcrypto-tool pdfminer.six \
    factordb-pycli sagecell 2>/dev/null \
  || pip3 install \
    pwntools z3-solver sympy angr scapy pycryptodome gmpy2 \
    requests beautifulsoup4 lxml flask \
    volatility3 capstone unicorn ropper ROPgadget || true

# ---------- 可选：SageMath（crypto 题数学库，体积大） ----------
if [[ "${1:-}" == "--with-sagemath" ]]; then
    log "安装 SageMath（较大，耐心等待）"
    sudo apt-get install -y sagemath || true
fi

# ---------- 可选：Docker（Cairn 容器模式用） ----------
if [[ "${1:-}" == "--with-docker" ]]; then
    log "安装 Docker"
    curl -fsSL https://get.docker.com | sudo sh || true
    sudo usermod -aG docker "$USER" || true
fi

# ---------- 可选：uv + Cairn 部署 ----------
if [[ "${1:-}" == "--with-cairn" ]]; then
    log "安装 uv 并准备 Cairn"
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    export PATH="$HOME/.local/bin:$PATH"
    git clone --depth 1 https://github.com/oritera/Cairn.git "$HOME/Cairn" 2>/dev/null || true
    cd "$HOME/Cairn" && uv sync || true
fi

log "版本自检"
python3 - <<'EOF'
import importlib
for m in ("pwn", "z3", "sympy", "angr", "scapy", "Crypto", "gmpy2", "requests"):
    try:
        importlib.import_module(m)
        print(f"[OK] {m}")
    except Exception as e:
        print(f"[--] {m}: {e}")
EOF

log "完成。检查清单：nmap/sqlmap/gdb/pwntools/z3/angr/sympy/binwalk/zsteg"
