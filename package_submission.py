"""只打包本作业代码和派生成果，不包含原始数据或工作区其他项目。"""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root=Path(__file__).resolve().parent
files=[p for p in root.iterdir() if p.is_file() and p.suffix in (".py",".html",".md",".txt")]
files += [root/".gitignore"]
for name in ("examples","output"):
    files += [p for p in (root/name).rglob("*") if p.is_file() and p.suffix in (".json",".csv",".html",".txt",".png",".svg")]
files=sorted(set(files))
manifest={str(p.relative_to(root)).replace("\\","/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
target=root.parent/"lesson03_sse50_作业包.zip"
with ZipFile(target,"w",compression=ZIP_DEFLATED,compresslevel=9) as z:
    for p in files:
        z.write(p,str(Path(root.name)/p.relative_to(root)))
    z.writestr(root.name+"/MANIFEST.json",json.dumps(manifest,ensure_ascii=False,indent=2))
with ZipFile(target) as z:
    assert z.testzip() is None
    assert not any(name.endswith(".parquet") for name in z.namelist())
print(f"Packaged {len(files)} files; {target.stat().st_size:,} bytes; {target}")
