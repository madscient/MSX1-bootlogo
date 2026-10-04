"""Assemble src/logo.asm into build/msx_logo.rom with a general-purpose Z80 assembler.
Standard library only. Run from any directory.

  python tools/build.py              use the first assembler that is found
  python tools/build.py --asm zmac   use this one: zmac, sjasmplus or pasmo

An assembler is looked up through its environment variable (ZMAC_EXE,
SJASMPLUS_EXE, PASMO_EXE: the path of the executable), then on PATH.
Writes to build/: msx_logo.rom (16 KiB), symbols.json (label addresses for the
test scripts) and the assembler's own output (msx_logo.cim plus listing/symbols).
"""
from pathlib import Path
import hashlib, json, os, re, shutil, subprocess, sys
ROOT=Path(__file__).resolve().parent.parent
SRC='src/logo.asm'; CIM='build/msx_logo.cim'; LST='build/msx_logo.lst'; SYM='build/msx_logo.sym'
ROM_SIZE=0x4000; ORG=0x4000

# args: run from ROOT, must write the raw image to CIM (zmac only accepts that suffix).
# symbols: the output file that lists the labels, and one regex per line of it
# with the name in group 1 and the hexadecimal address in group 2.
ASSEMBLERS={
    'zmac':     dict(env='ZMAC_EXE',      args=['-o',CIM,'-o',LST,SRC],
                     symbols=LST, line=r'(\w+)\s+([0-9A-F]{4})\s+\d+\s*$'),
    'sjasmplus':dict(env='SJASMPLUS_EXE', args=['--nologo','--msg=war','--raw='+CIM,'--lst='+LST,'--sym='+SYM,SRC],
                     symbols=SYM, line=r'(\w+): EQU 0x([0-9A-F]+)\s*$'),
    'pasmo':    dict(env='PASMO_EXE',     args=[SRC,CIM,SYM],
                     symbols=SYM, line=r'(\w+)\s+EQU 0([0-9A-F]+)H\s*$'),
}

def find(name):
    exe=os.environ.get(ASSEMBLERS[name]['env']) or shutil.which(name)
    return exe if exe and Path(exe).is_file() else None

def build(name,exe):
    asm=ASSEMBLERS[name]
    (ROOT/'build').mkdir(exist_ok=True)
    # Stale outputs must not survive a failed or partial run.
    for stale in (CIM,LST,SYM,'build/msx_logo.rom','build/symbols.json'): (ROOT/stale).unlink(missing_ok=True)
    run=subprocess.run([exe]+asm['args'],cwd=ROOT,capture_output=True,text=True)
    if run.returncode or not (ROOT/CIM).is_file():
        sys.exit(f'{name} failed (exit code {run.returncode})\n{run.stdout}{run.stderr}')
    image=(ROOT/CIM).read_bytes()
    labels={}
    for line in (ROOT/asm['symbols']).read_text().splitlines():
        m=re.match(asm['line'],line)
        if m: labels[m[1]]=int(m[2],16)
    # The image must be exactly org..rom_end: an assembler that drops or adds
    # bytes would otherwise shift everything without any error.
    if labels.get('rom_end',0)-ORG!=len(image):
        sys.exit(f'{name}: image is {len(image)} bytes but rom_end is {labels.get("rom_end",0):04X}h')
    if len(image)>ROM_SIZE: sys.exit(f'{name}: {len(image)} bytes do not fit in {ROM_SIZE}')
    rom=image+b'\xff'*(ROM_SIZE-len(image))
    (ROOT/'build/msx_logo.rom').write_bytes(rom)
    (ROOT/'build/symbols.json').write_text(json.dumps(labels,indent=2))
    print(f'{name}: build/msx_logo.rom {len(rom)} bytes, used {len(image)}, SHA-256 {hashlib.sha256(rom).hexdigest()}')

if __name__=='__main__':
    args=sys.argv[1:]
    if args and (len(args)!=2 or args[0]!='--asm' or args[1] not in ASSEMBLERS): sys.exit(__doc__)
    names=[args[1]] if args else list(ASSEMBLERS)
    for name in names:
        exe=find(name)
        if exe:
            build(name,exe)
            break
    else:
        sys.exit('No assembler found. Looked for: '+', '.join(f'{n} ({ASSEMBLERS[n]["env"]} or PATH)' for n in names))
