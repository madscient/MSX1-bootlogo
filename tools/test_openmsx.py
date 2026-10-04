"""Run build/msx_logo.rom in openMSX and check what the emulated machine did.
Standard library only. Needs the output of tools/build.py.

  python tools/test_openmsx.py                 run the built-in cases (C-BIOS machines)
  python tools/test_openmsx.py --machine NAME  run one stock openMSX machine instead

openMSX is found through the environment variable OPENMSX_DIR (the directory
holding the openmsx executable and its share/ folder), then on PATH.
It runs without a window and at full speed. Its user data directory is
redirected to build/openmsx/, so the user's own openMSX settings stay untouched.
System ROM images for --machine are searched in the folders listed in the
environment variable MSX_ROM_DIRS (separated like PATH) and in share/systemroms
of the openMSX installation.
The report goes to build/openmsx_test.json. Exit code 1 if any check fails.
"""
from pathlib import Path
from typing import NamedTuple
import json, os, re, shutil, subprocess, sys
from xml.sax.saxutils import escape
ROOT=Path(__file__).resolve().parent.parent
ASSETS=ROOT/'assets'; BUILD=ROOT/'build'; USERDATA=BUILD/'openmsx'
SETTLE=6   # emulated seconds to watch the machine after INIT has returned
LIMIT=40   # emulated seconds allowed per boot before the run is abandoned
# Frames per second of the TMS9918A (262 lines) and TMS9929A (313 lines).
FRAME_RATE={60:3579545/(228*262),50:3579545/(228*313)}

class Case(NamedTuple):
    name: str
    base: str            # stock machine in share/machines
    ram: tuple|None      # (subslot or None, base, size) replaces slot 3; None keeps the stock machine
    romtype: str|None    # None lets openMSX decide, which mirrors a 16 KiB ROM into every page
    resets: int          # boots after the first one; RAM survives a reset
    hz: int|None         # None: taken from what the BIOS reports at 002Bh
    kb: int|None         # None: taken from what openMSX reports for the slot in page 3

CASES=[
    Case('60Hz, ROM mirrored',          'C-BIOS_MSX1',    None,                 None,         0, 60, 64),
    Case('50Hz, ROM mirrored',          'C-BIOS_MSX1_EU', None,                 None,         0, 50, 64),
    Case('ROM not mirrored, reset',     'C-BIOS_MSX1',    None,                 'Normal4000', 1, 60, 64),
    Case('ROM mirrored, reset',         'C-BIOS_MSX1',    None,                 None,         1, 60, 64),
    Case('RAM 64KB in subslot 3-2',     'C-BIOS_MSX1',    (2,0x0000,0x10000),   None,         0, 60, 64),
    Case('RAM 48KB in subslot 3-2',     'C-BIOS_MSX1',    (2,0x4000,0xC000),    None,         0, 60, 48),
    Case('RAM 16KB in subslot 3-1',     'C-BIOS_MSX1',    (1,0xC000,0x4000),    None,         0, 60, 16),
    Case('RAM 32KB in slot 3',          'C-BIOS_MSX1',    (None,0x8000,0x8000), None,         0, 60, 32),
    Case('RAM 16KB in slot 3',          'C-BIOS_MSX1',    (None,0xC000,0x4000), None,         0, 60, 16),
]

def find_openmsx():
    folder=os.environ.get('OPENMSX_DIR')
    found=[Path(folder)/name for name in ('openmsx.exe','openmsx')] if folder else []
    if shutil.which('openmsx'): found.append(Path(shutil.which('openmsx')))
    for exe in found:
        if exe.is_file(): return exe
    sys.exit('openMSX not found: set OPENMSX_DIR to its folder or put openmsx on PATH')

def write_settings(share):
    """openMSX reads its file pools from the settings file of the user data directory."""
    pools=[share/'systemroms']+[Path(folder) for folder in os.environ.get('MSX_ROM_DIRS','').split(os.pathsep) if folder]
    entries=' '.join('{-path {%s} -types system_rom}'%pool.as_posix() for pool in pools)
    USERDATA.mkdir(parents=True,exist_ok=True)
    (USERDATA/'settings.xml').write_text("<!DOCTYPE settings SYSTEM 'settings.dtd'><settings><settings>"
        f'<setting id="__filepool">{escape(entries)}</setting></settings></settings>')

def machine_for(case,share):
    """Name of the machine to start; writes a derived machine when the case moves the RAM."""
    if case.ram is None: return case.base
    stock=share/'machines'/f'{case.base}.xml'
    if not stock.is_file(): sys.exit(f'{case.base}.xml not found in share/machines next to the openMSX executable')
    sub,base,size=case.ram
    ram=f'<RAM id="Main RAM"><mem base="0x{base:04X}" size="0x{size:X}"/></RAM>'
    if sub is not None: ram=f'<secondary slot="{sub}">{ram}</secondary>'
    xml,count=re.subn(r'<primary slot="3">.*?</primary>',f'<primary slot="3">{ram}</primary>',stock.read_text(),flags=re.S)
    assert count==1,stock
    # The derived machine lives outside share/machines, where its ROM images are.
    xml=re.sub(r'<filename>([^<]+)</filename>',lambda m:f'<filename>{(stock.parent/m[1]).as_posix()}</filename>',xml)
    name='logo_test_'+re.sub(r'\W+','_',case.name)
    (USERDATA/'machines').mkdir(parents=True,exist_ok=True)
    (USERDATA/'machines'/f'{name}.xml').write_text(xml)
    return name

def run(case,exe,symbols):
    records_file=BUILD/'openmsx_records.txt'
    records_file.unlink(missing_ok=True)
    write_settings(exe.parent/'share')
    env=dict(os.environ,OPENMSX_USER_DATA=str(USERDATA),LOGO_OUT=str(records_file),
        LOGO_SYMBOLS=' '.join(f'{name} {address}' for name,address in symbols.items()),
        LOGO_RESETS=str(case.resets),LOGO_SETTLE=str(SETTLE),LOGO_LIMIT=str(LIMIT*(case.resets+1)))
    args=[str(exe),'-machine',machine_for(case,exe.parent/'share'),'-carta',str(BUILD/'msx_logo.rom')]
    if case.romtype: args+=['-romtype',case.romtype]
    args+=['-script',str(ROOT/'tools'/'openmsx_test.tcl')]
    try: subprocess.run(args,env=env,capture_output=True,timeout=120)
    except subprocess.TimeoutExpired: pass
    if not records_file.is_file(): return []
    records=[]
    for line in records_file.read_text().splitlines():
        kind,*fields=line.split(' ')
        records.append((kind,dict(field.split('=',1) for field in fields)))
    return records

def slot_id(slot):
    """'3/2' or '1/X' as openMSX prints it -> the slot ID byte the BIOS uses."""
    primary,sub=slot.split('/')
    return int(primary) if sub=='X' else 0x80|int(sub)<<2|int(primary)

def check(case,records):
    """Returns (checks, notes): checks is a list of (boot, what, passed, detail)."""
    checks=[]; notes={}
    def add(boot,what,passed,detail=''): checks.append((boot,what,bool(passed),str(detail)))
    def of(kind,boot=None): return [f for k,f in records if k==kind and (boot is None or int(f['boot'])==boot)]
    setup=of('setup')
    add(None,'openMSX started and wrote records',setup,'no records: the machine or its ROM images may be missing')
    if not setup: return checks,notes
    setup=setup[0]
    notes['openmsx']=bytes.fromhex(setup['version']).decode(); notes['machine']=setup['machine']
    for error in of('error'): add(int(error['boot']),'recorder script ran without error',False,bytes.fromhex(error['msg']).decode())
    add(None,'finished within the time limit',not of('limit'))
    solid=(ASSETS/'solid.dat').read_bytes()
    for boot in range(case.resets+1):
        inits=of('init',boot); returns=of('return',boot); ticks=[float(f['t']) for f in of('tick',boot)]
        finals=of('final',boot); texts=of('text',boot); settled=of('settled',boot)
        notes[f'boot {boot}: INIT calls']=len(inits)
        add(boot,'INIT is called and returns to its caller',inits and len(inits)==len(returns),f'{len(inits)} calls, {len(returns)} returns')
        add(boot,"the caller's stack is intact on return",all(i['guard']==r['guard'] for i,r in zip(inits,returns)))
        add(boot,'RAM from D8A0h up to the stack is not written',all(i['above']==r['above'] for i,r in zip(inits,returns)))
        marks={f['name'] for f in of('mark',boot)}
        played=len(ticks)==154 and marks=={'sweep','fill','blue','ram_stage'} and len(finals)==len(texts)==1
        add(boot,'the animation plays exactly once',played,f'{len(ticks)} ticks (154 expected), stages reached: {sorted(marks)}')
        if settled:
            settled=settled[0]
            add(boot,'the CPU is outside the cartridge after INIT',settled['pc_slot']!=setup['cart'],settled['pc_slot'])
            screen=bytes.fromhex(settled['screen']).decode('latin1')
            notes[f'boot {boot}: {SETTLE}s after INIT']={'screen mode':settled['mode'],'slots in pages 0-3':settled['pages'],
                'text':[line.strip() for line in screen.splitlines() if line.strip()][:4]}
        else: add(boot,'the machine keeps running after INIT',False,'no record after the return')
        if not played: continue
        final=finals[0]; text=texts[0]; before=of('ram_before',boot)[0]
        hz=case.hz or (50 if int(inits[0]['bios2b'])&0x80 else 60)
        rate=FRAME_RATE[hz]
        frames=(ticks[113]-ticks[0])*rate; expected=int(150*hz/20)-int(hz/20)
        add(boot,'the logo completes in 7.5 seconds',abs(frames-expected)<0.5,f'{frames:.2f} frames at {hz}Hz, {expected} expected')
        frames=(float(final['t'])-float(text['t']))*rate
        add(boot,'the RAM size stays on screen for 2 seconds',abs(frames-2*hz)<=1,f'{frames:.2f} frames, {2*hz} expected')
        add(boot,'own slot is identified',int(final['own_slot'])==slot_id(setup['cart']),f"{int(final['own_slot']):02X}h, cartridge is in {setup['cart']}")
        add(boot,'the mirror at 8000h is detected',bool(int(final['mirrored']))==(setup['page2']=='4142'),f"flag {final['mirrored']}, 8000h reads {setup['page2']}")
        add(boot,'the RAM slot is identified',int(final['ram_slot'])==slot_id(before['slot']),f"{int(final['ram_slot']):02X}h, page 3 is {before['slot']}")
        kb=case.kb or int(before['kb'])
        add(boot,'the RAM size is right',int(final['ram_kb'])==kb==int(before['kb']),f"ROM says {final['ram_kb']}KB, openMSX says {before['kb']}KB"+(f', case says {case.kb}KB' if case.kb else ''))
        add(boot,'the probed RAM bytes are restored',before['probes']==text['probes'],f"{before['probes']} -> {text['probes']}")
        glyphs=bytes.fromhex(text['glyphs'])
        picture=bytearray(solid)
        picture[0xF40:0xFB8]=b''.join(glyphs[c*8:c*8+8] for c in f'Main RAM : {kb}KB'.encode())
        add(boot,'the final picture is the logo plus the RAM size in the system font',bytes.fromhex(final['pattern'])==picture and any(picture[0xF40:0xFB8]))
        colors=bytes(0xF1 if 32<=x<224 and 48<=y<112 else 0xF4 for y in range(0,192,8) for x in range(0,256,8) for _ in range(8))
        add(boot,'the colours are a black box on blue',bytes.fromhex(final['colors'])==colors and int(final['r7'])==4,f"R7={final['r7']}")
        add(boot,'the name table is standard and sprites are off',bytes.fromhex(final['names'])==bytes(range(256))*3 and int(final['sprite'])==208)
        work=bytearray.fromhex(returns[-1]['work']); marker=bytes(work[0x1820:0x1822]); work[0x1820:0x1822]=b'\0\0'
        left=[f'{0xC000+i:04X}h={value:02X}h' for i,value in enumerate(work) if value]
        add(boot,'the work RAM is cleared on the last return',not left and marker in (b'\0\0',b'\x5a\xa5'),f'marker {marker.hex()}, left behind: {" ".join(left[:8]) or "nothing"}')
    return checks,notes

if __name__=='__main__':
    args=sys.argv[1:]
    if args and (len(args)!=2 or args[0]!='--machine'): sys.exit(__doc__)
    for needed in (BUILD/'msx_logo.rom',BUILD/'symbols.json'):
        if not needed.is_file(): sys.exit(f'build/{needed.name} not found: run tools/build.py first')
    cases=[Case(args[1],args[1],None,None,1,None,None)] if args else CASES
    exe=find_openmsx()
    symbols=json.loads((BUILD/'symbols.json').read_text())
    report=[]; failed=0
    for case in cases:
        checks,notes=check(case,run(case,exe,symbols))
        bad=[c for c in checks if not c[2]]
        failed+=len(bad)
        print(f'{"FAIL" if bad else "ok  "} {case.name}: {len(checks)-len(bad)}/{len(checks)} checks')
        for boot,what,_,detail in bad: print(f'       {"" if boot is None else f"boot {boot}: "}{what} -- {detail}')
        report.append({'case':case.name,'notes':notes,'checks':[{'boot':b,'check':w,'passed':p,'detail':d} for b,w,p,d in checks]})
    (BUILD/'openmsx_test.json').write_text(json.dumps(report,indent=2))
    print(f'{failed} failed checks' if failed else 'all checks passed')
    sys.exit(1 if failed else 0)
