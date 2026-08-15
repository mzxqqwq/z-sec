# -*- coding: utf-8 -*-
"""从 ctftiny + nyu-ctf-bench 全量扫描 docker-compose.yml，提取服务镜像清单。

产出：
  src/tools/service-images.txt      —— 归一化镜像名（去引号/去@sha256/补:latest），按题去重
  src/tools/service-manifest.json   —— 题slug -> {compose, services: [{name, image, normalized, ports}]}
然后可用 tools/regen-pull-script.py 重新生成 pull-service-images.sh。
"""
import json
import re
import sys
from pathlib import Path

ROOTS = [
    Path(r"D:\ctf-agent\benchmarks\ctftiny"),
    Path(r"D:\ctf-agent\benchmarks\nyu-ctf-bench"),
]
OUT_TXT = Path(r"D:\ctf-agent\src\tools\service-images.txt")
OUT_JSON = Path(r"D:\ctf-agent\src\tools\service-manifest.json")

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml")

TAG_RE = re.compile(r"^([^@:]+)(?::([^@]+))?(?:@sha256:[0-9a-f]+)?$")


def normalize(image: str) -> str:
    image = image.strip().strip("'\"")
    m = TAG_RE.match(image)
    if not m:
        return image
    repo, tag = m.group(1), m.group(2)
    if not tag:
        tag = "latest"
    return f"{repo}:{tag}"


def main() -> int:
    manifest: dict = {}
    all_images: set = set()

    for root in ROOTS:
        if not root.exists():
            print(f"!! 根目录不存在: {root}", file=sys.stderr)
            continue
        for compose in sorted(root.rglob("*")):
            if compose.name not in COMPOSE_NAMES:
                continue
            # 跳过上游已移除的题目（removed/ 下的不参与评测）
            if "removed" in compose.relative_to(root).parts:
                continue
            # 跳过 removed/development 等非 test 子集？不——全拉，反正本地存储便宜。
            # 但 manifest 里记录相对路径便于后续本地复活。
            try:
                data = yaml_safe_load(compose)
            except Exception as e:
                print(f"!! 解析失败 {compose}: {e}", file=sys.stderr)
                continue
            if not isinstance(data, dict) or "services" not in data:
                continue
            services = data.get("services") or {}
            if not isinstance(services, dict):
                continue
            entry = {"compose": str(compose), "root": str(root), "services": []}
            for svc_name, svc in services.items():
                if not isinstance(svc, dict):
                    continue
                image = svc.get("image")
                if not image or not isinstance(image, str):
                    continue
                norm = normalize(image)
                ports = svc.get("ports") or []
                if isinstance(ports, str):
                    ports = [ports]
                ports = [str(p) for p in ports]
                volumes = svc.get("volumes") or []
                if isinstance(volumes, str):
                    volumes = [volumes]
                volumes = [str(v) for v in volumes]
                env = svc.get("environment") or []
                if isinstance(env, dict):
                    env = [f"{k}={v}" for k, v in env.items()]
                env = [str(e) for e in env]
                entry["services"].append({
                    "name": svc_name,
                    "image": image.strip(),
                    "normalized": norm,
                    "ports": ports,
                    "volumes": volumes,
                    "environment": env,
                })
                all_images.add(norm)
            if entry["services"]:
                # key = 相对题库根目录的路径（与 ctftiny.json/test_dataset.json 的 path 字段对齐，
                # 不加 root.name 前缀——eval_platform 的 meta path 正是相对题库根的）
                key = compose.parent.relative_to(root).as_posix()
                manifest[key] = entry

    # 分类统计
    base = sorted(i for i in all_images if not i.startswith("llmctf/"))
    llm = sorted(i for i in all_images if i.startswith("llmctf/"))

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        for i in llm + base:
            f.write(i + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"compose 文件数: {len(manifest)}")
    print(f"唯一镜像: {len(all_images)}  (llmctf/* {len(llm)}, 基础镜像 {len(base)})")
    print(f"基础镜像: {base}")
    print(f"已写: {OUT_TXT}")
    print(f"已写: {OUT_JSON}")
    return 0


def yaml_safe_load(path: Path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    sys.exit(main())
