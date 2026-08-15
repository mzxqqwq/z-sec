# Kali 机器资产清单（10.174.153.128）

> 本文档记录本次备赛在 Kali 上安装/产生的所有内容与地址。
> 用户自己的内容（8080 端口容器 ctf2024-challenge08、/root/work 等）不在此清单，请勿与以下目录混淆。

## 一、安装的工具链

### apt 安装（/usr/bin 等系统路径）
| 工具 | 用途 |
|---|---|
| binwalk / foremost / zsteg / exiftool | 隐写与文件分析 |
| john / hashcat | 密码爆破 |
| radare2 | 逆向 |
| gdb | 调试（pwn） |
| socat | 网络 |
| jadx / apktool | Android 逆向 |
| podman（原有，docker 命令仿真） | 容器（服务类题） |

### Python 包（MCP API 的 venv）
路径：`/home/kali/MCP-Kali-Server/.venv/`
已装：pwntools、z3-solver、sympy、angr、scapy、pycryptodome、gmpy2、flask
（API 的 python3 即此 venv，全部命令天然可用）

### 未装成功（已确认路线）
- SageMath：apt 源无包 + pip 无 py3.13 wheel → 需修 Kali apt 源
- steghide/stegseek：apt 依赖冲突（php 相关）→ 需修源
- docker.io：半装失败（交互卡住），实际用 podman 即可

## 二、下载的仓库与数据集

| 路径 | 内容 |
|---|---|
| `/root/ctftiny/` | CTFTiny benchmark（CSAW 真题 50 道，含 challenge.json 真值） |
| `/root/cybench/` | 空目录（克隆失败残留，可删） |
| `/root/dasctf-solve/` | DASCTF 真题解题工作目录（子代理产出） |

## 三、比赛系统运行时数据

| 路径 | 内容 |
|---|---|
| `/root/ctf/` | 编排器的每题远程工作区（每 cid 一个目录：attachments/） |
| `/root/ctf/src/` | pwn_bof.c、web_ssti.py（演练题源码） |
| `/root/ctf/8/pwn_bof` | 演练用溢出二进制 |
| `/root/ctf/pwn_flag.txt`、`web_flag.txt` | 演练题 flag 文件 |
| `/root/ctf/web_ssti.py`（进程，端口 8300） | 演练用 SSTI 服务（nohup 常驻） |

## 四、临时文件（/tmp，可清理）
- pip/apt 安装日志：/tmp/pip-*.log、/tmp/apt*.log、/tmp/sage-pip.log、/tmp/podman-pull*.log
- 解压临时：/tmp/sd、/tmp/ctf-venv 等

## 五、历史遗留（本次早期创建，已被取代）
- `/home/kali/ctf-venv/`（第一次 pip 安装的 venv，功能已被 MCP venv 取代，可删）
- `/root/ctf-venv/`（第二次尝试，未完成，可删）
