# -*- coding: utf-8 -*-
import re
p = r"D:\ctf-agent\src\tools\pull-service-images.sh"
lines = open(p, encoding="utf-8").read().splitlines()
print("total lines:", len(lines))
print("\n".join(lines[:4]))
print("...")
print("\n".join(lines[-7:]))
imgs = [l for l in lines if l.strip().startswith('"')]
n = sum(len(re.findall(r'"([^"]+)"', l)) for l in imgs)
print("image count in array:", n)
