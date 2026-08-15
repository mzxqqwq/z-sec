# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\ctf-agent\src\ctf_orchestrator")
from workers import kali_exec

# 1. shell 类型
print("shell:", repr(kali_exec("echo $0; bash --version | head -1", timeout=30)))

# 2. /dev/tcp 语法是否可用
print("devtcp:", repr(kali_exec(
    "timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/9 2>/dev/null && echo OPEN || echo CLOSED'",
    timeout=30)))

# 3. allocator 原样输出
cmd = ("p=21000; for ((i=0;i<10;i++)); do "
       "if ! (exec 3<>/dev/tcp/127.0.0.1/$((p+i))) 2>/dev/null; then "
       "echo $((p+i)); exit 0; fi; done; echo 0")
print("alloc:", repr(kali_exec(cmd, timeout=30)))

# 4. podman 是否可用
print("podman:", repr(kali_exec("podman --version 2>&1 | head -1", timeout=30)))

# 5. 镜像在不在
print("img:", repr(kali_exec(
    "podman image exists docker.io/llmctf/2023f-cry-describeme:latest && echo yes || echo no",
    timeout=30)))
