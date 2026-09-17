"""Public configuration and code identity only; never read .env or credentials."""
import hashlib
import subprocess
from .common import ROOT,AREA


def snapshot():
    files=sorted((ROOT/'backend/src').rglob('*.py'))
    hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    fixture=AREA/'fixture.json'
    return {'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),
            'source_sha256':hashes,'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest() if fixture.exists() else None}
