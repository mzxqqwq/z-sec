# -*- coding: utf-8 -*-
import json
from pathlib import Path
m = json.loads(Path(r"D:\ctf-agent\src\tools\service-manifest.json").read_text(encoding="utf-8"))
for k in m:
    if "escribeme" in k.lower():
        print(repr(k))
