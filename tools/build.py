"""Assemble the ROM images with a general-purpose Z80 assembler.
Standard library only. Run from any directory.

  python tools/build.py              build both images with the first assembler that is found
  python tools/build.py --asm zmac   use this one: zmac, sjasmplus or pasmo
  python tools/build.py --page 2     build one image: 1 (runs at 4000h) or 2 (runs at 8000h)

An assembler is looked up through its environment variable (ZMAC_EXE,
SJASMPLUS_EXE, PASMO_EXE: the path of the executable), then on PATH.
Writes to build/, for page N: msx_logo_pageN.rom (16 KiB), symbols_pageN.json
(label addresses for the test scripts) and the assembler's own output
(msx_logo_pageN.cim plus listing/symbols).
"""
from pathlib import Path
import argparse, hashlib, json, os, re, shutil, subprocess, sys
ROOT=Path(__file__).resolve().parent.parent
ROM_SIZE=0x4000
# Page number -> address the image runs at. src/pageN.asm sets it and includes
# src/logo.asm. The test scripts take the pages and the file names from here.
PAGES={1:0x4000,2:0x8000}

def source(page): return f'src/page{page}.asm'
def output(page,suffix): return f'build/msx_logo_page{page}.{suffix}'
def rom_file(page): return ROOT/output(page,'rom')
def symbols_file(page): return ROOT/f'build/symbols_page{page}.json'

# args: run from ROOT, must write the raw image to the .cim file (zmac only
# accepts that suffix). symbols: the suffix of the output file that lists the
# labels, and one regex per line of it with the name in group 1 and the
# hexadecimal value in group 2.
ASSEMBLERS={
    'zmac':     dict(env='ZMAC_EXE',      args=lambda p:['-o',output(p,'cim'),'-o',output(p,'lst'),source(p)],
                     symbols='lst', line=r'(\w+)\s+=?([0-9A-F]+)\s+\d+\s*$'),
    'sjasmplus':dict(env='SJASMPLUS_EXE', args=lambda p:['--nologo','--msg=war','--raw='+output(p,'cim'),'--lst='+output(p,'lst'),'--sym='+output(p,'sym'),source(p)],
                     symbols='sym', line=r'(\w+): EQU 0x([0-9A-F]+)\s*$'),
    'pasmo':    dict(env='PASMO_EXE',     args=lambda p:[source(p),output(p,'cim'),output(p,'sym')],
                     symbols='sym', line=r'(\w+)\s+EQU 0([0-9A-F]+)H\s*$'),
}

def find(name):
    exe=os.environ.get(ASSEMBLERS[name]['env']) or shutil.which(name)
    return exe if exe and Path(exe).is_file() else None

def build(name,exe,page):
    asm=ASSEMBLERS[name]; base=PAGES[page]
    (ROOT/'build').mkdir(exist_ok=True)
    # Stale outputs must not survive a failed or partial run.
    for stale in [ROOT/output(page,suffix) for suffix in ('cim','lst','sym','rom')]+[symbols_file(page)]: stale.unlink(missing_ok=True)
    run=subprocess.run([exe]+asm['args'](page),cwd=ROOT,capture_output=True,text=True)
    if run.returncode or not (ROOT/output(page,'cim')).is_file():
        sys.exit(f'{name} failed (exit code {run.returncode})\n{run.stdout}{run.stderr}')
    image=(ROOT/output(page,'cim')).read_bytes()
    labels={}
    for line in (ROOT/output(page,asm['symbols'])).read_text().splitlines():
        m=re.match(asm['line'],line)
        if m: labels[m[1]]=int(m[2],16)
    if labels.get('rom_base')!=base: sys.exit(f'{name}: {source(page)} does not set rom_base to {base:04X}h')
    # The image must be exactly rom_base..rom_end: an assembler that drops or
    # adds bytes would otherwise shift everything without any error.
    if labels.get('rom_end',0)-base!=len(image):
        sys.exit(f'{name}: image is {len(image)} bytes but rom_end is {labels.get("rom_end",0):04X}h')
    if len(image)>ROM_SIZE: sys.exit(f'{name}: {len(image)} bytes do not fit in {ROM_SIZE}')
    rom=image+b'\xff'*(ROM_SIZE-len(image))
    rom_file(page).write_bytes(rom)
    symbols_file(page).write_text(json.dumps(labels,indent=2))
    print(f'{name}: {output(page,"rom")} {len(rom)} bytes, used {len(image)}, SHA-256 {hashlib.sha256(rom).hexdigest()}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Assemble the ROM images into build/.')
    parser.add_argument('--asm',choices=list(ASSEMBLERS),help='assembler to use (default: the first one found)')
    parser.add_argument('--page',type=int,choices=list(PAGES),help='image to build (default: all)')
    args=parser.parse_args()
    names=[args.asm] if args.asm else list(ASSEMBLERS)
    for name in names:
        exe=find(name)
        if exe:
            for page in [args.page] if args.page else list(PAGES): build(name,exe,page)
            break
    else:
        sys.exit('No assembler found. Looked for: '+', '.join(f'{n} ({ASSEMBLERS[n]["env"]} or PATH)' for n in names))
