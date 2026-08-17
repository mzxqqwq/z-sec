FROM kalilinux/kali-rolling
ENV DEBIAN_FRONTEND=noninteractive
# CTF 工具链 + sshd（worker 容器专用镜像，benchmark 隔离改造）。
# 基础工具走 apt；python 工具链 pip 装并钉死版本（= 宿主机 Kali 实测可用组合，
# py3.14 下只有 unicorn==2.1.2 能源码编译，最新版编译失败）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev python3-venv socat netcat-openbsd openssh-server \
    gdb strace ltrace nmap sqlmap binwalk john hashcat radare2 \
    gcc make cmake git curl ca-certificates file xxd \
    libsodium23 libgmp10 \
    && rm -rf /var/lib/apt/lists/*
# 工具链 = 宿主机 Kali 实测可用的 dist-packages 快照（同 kali-rolling + py3.14.6，
# angr 全家桶在 py3.14 下只有宿主机这套源码编译产物可用——容器内重编 unicorn 必失败）。
# 由 _sandbox_tar_dist.py 生成；sys-dist-py3.tgz = /usr/lib/python3/dist-packages
# （PySocks/paramiko/mako 等系统 apt 包），dist-py314.tgz = /usr/local 的 pip 包。
COPY dist-py314.tgz sys-dist-py3.tgz /root/
RUN tar xzf /root/dist-py314.tgz -C /usr/local/lib/python3.14 \
 && tar xzf /root/sys-dist-py3.tgz -C /usr/lib/python3 \
 && rm -f /root/dist-py314.tgz /root/sys-dist-py3.tgz \
 && python3 -c "import pwn, angr, z3, sympy, Crypto, socks, paramiko; print('tools-ok')"
RUN sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?UsePAM.*/UsePAM no/' /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
