"""Build the backend-only payload from an immutable Git commit; never deploy."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def included(path):
    if path.startswith(("backend/tests/", "backend/app/integrations/okx_asp/")):
        return False
    if path in {"backend/app/routes/okx_asp.py", "backend/requirements.txt"}:
        return False
    return path.startswith(("backend/app/", "bot/")) or path in {
        "scripts/replay_tracking.py", "scripts/stage1_admin.py", "scripts/stage2_admin.py", "docs/stage1_vps_baseline.json"}


def pack(root, ref, output):
    def git(*args):return subprocess.check_output(["git","-C",str(root),*args])
    sha=git("rev-parse",ref+"^{commit}").decode().strip()
    files=[]
    for raw in git("ls-tree","-r","-z",sha).split(b"\0"):
        if not raw:continue
        info,name=raw.split(b"\t",1)
        mode,kind,oid=info.decode().split()
        name=name.decode()
        if not included(name):continue
        if kind!="blob" or mode not in {"100644","100755"}:
            raise ValueError("Unsupported payload file type: "+name)
        body=git("cat-file","blob",oid)
        files.append((name,body,0o755 if mode=="100755" else 0o644))
    manifest={"release_commit":sha,"files":{name:hashlib.sha256(body).hexdigest() for name,body,_ in files},
              "excluded":["frontend","OKX ASP/x402","tests","dependency installation","live env/data"]}
    with output.open("xb") as target:
        with tarfile.open(fileobj=target,mode="w:gz") as archive:
            for name,body,mode in files+[("release-manifest.json",json.dumps(manifest,indent=2).encode(),0o600)]:
                entry=tarfile.TarInfo(name);entry.size=len(body);entry.mode=mode
                archive.addfile(entry,io.BytesIO(body))
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--ref",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=pack(Path(__file__).resolve().parents[1],args.ref,args.output)
    print(json.dumps({"release_commit":result["release_commit"],"files":len(result["files"]),"output":str(args.output)}))
