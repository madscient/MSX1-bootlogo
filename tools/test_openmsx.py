"""Run the ROM images of build/ in openMSX and check what the emulated machine did.
Standard library only. Needs the output of tools/build.py.

  python tools/test_openmsx.py                 run the built-in cases (C-BIOS machines) with every image
  python tools/test_openmsx.py --page 2        only one image: 1 or 2
  python tools/test_openmsx.py --machine NAME  run one stock openMSX machine instead of the built-in cases
  python tools/test_openmsx.py --machine NAME --ram 0-2:64
                                               the same with its RAM replaced: 64 KiB in subslot 0-2
                                               (SLOT:KB, where SLOT is a primary slot or PRIMARY-SUB)
  python tools/test_openmsx.py --machine NAME --rom neighbour
                                               what the other page of the cartridge slot holds in that run:
                                               alone (default), neighbour or mirrored

openMSX is found through the environment variable OPENMSX_DIR (the directory
holding the openmsx executable and its share/ folder), then on PATH.
It runs without a window and at full speed. Its user directories are
redirected to build/openmsx/, so the user's own openMSX settings and saved
machine data stay untouched.
System ROM images for --machine are searched in the folders listed in the
environment variable MSX_ROM_DIRS (separated like PATH) and in share/systemroms
of the openMSX installation.
The report goes to build/openmsx_test.json. Exit code 1 if any check fails.
"""
from pathlib import Path
from typing import NamedTuple
import argparse, json, os, re, shutil, subprocess, sys
from xml.sax.saxutils import escape
import build, screen
ROOT=Path(__file__).resolve().parent.parent
BUILD=ROOT/'build'; USERDATA=BUILD/'openmsx'
SETTLE=6   # emulated seconds to watch the machine after INIT has returned
LIMIT=40   # emulated seconds allowed per boot before the run is abandoned
# Frames per second of the TMS9918A (262 lines) and TMS9929A (313 lines).
FRAME_RATE={60:3579545/(228*262),50:3579545/(228*313)}
STAGES={'sweep','beams_off','fill','blue','ram_stage'}

class Case(NamedTuple):
    name: str
    base: str            # stock machine in share/machines
    ram: tuple|None      # (slot, KiB) replaces the RAM of the machine, e.g. ('3-2',48); None keeps it
    rom: str             # what the other page of the cartridge slot holds: one of LAYOUTS
    resets: int          # boots after the first one; RAM survives a reset
    hz: int|None         # None: taken from what the BIOS reports at 002Bh
    kb: int|None         # None: taken from what openMSX reports for the slot in page 3

# alone: nothing in the other page. neighbour: another ROM in the other page,
# whose INIT returns at once. mirrored: openMSX picks the ROM type, which shows
# a 16 KiB ROM in every page; the ROM does not support that and plays once for
# every INIT call the BIOS then makes.
LAYOUTS=('alone','neighbour','mirrored')

def placed(where,kb): return f'RAM {kb}KB in {"subslot" if "-" in where else "slot"} {where}'

# In slot 0 the RAM shares the slot with the BIOS, or sits in another subslot of it.
CASES=[Case('60Hz','C-BIOS_MSX1',None,'alone',0,60,64),
       Case('50Hz','C-BIOS_MSX1_EU',None,'alone',0,50,64),
       Case('reset','C-BIOS_MSX1',None,'alone',1,60,64)]
CASES+=[Case(placed(where,kb),'C-BIOS_MSX1',(where,kb),'alone',0,60,kb) for where,kb in
    (('3-2',64),('3-2',48),('3-1',16),('3-1',8),('3',32),('3',16),('3',8),('0',16),('0',8),('0-2',64),('0-2',48),('0-2',16),('0-2',8))]

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
    where,kb=case.ram
    primary,_,sub=where.partition('-')
    ram=f'<RAM id="Main RAM"><mem base="0x{0x10000-kb*1024:04X}" size="0x{kb*1024:X}"/></RAM>'
    # Take out the RAM the machine has, then put the new one in its slot. A
    # primary slot that gets a subslot keeps what it had in subslot 0.
    xml,count=re.subn(r'<(RAM|MemoryMapper)\b.*?</\1>','',stock.read_text(),flags=re.S)
    assert count,stock
    if f'slot="{primary}"' not in xml: xml=xml.replace('</devices>',f'<primary slot="{primary}"></primary></devices>')
    slot=re.search(rf'<primary slot="{primary}">(.*?)</primary>',xml,flags=re.S)
    if not slot or '<secondary' in slot[1]: sys.exit(f'{case.base}: slot {primary} is a cartridge slot or already has subslots, so the RAM cannot go there')
    if not sub: inside=slot[1]+ram
    elif slot[1].strip(): inside=f'<secondary slot="0">{slot[1]}</secondary><secondary slot="{sub}">{ram}</secondary>'
    else: inside=f'<secondary slot="{sub}">{ram}</secondary>'
    xml=xml[:slot.start(1)]+inside+xml[slot.end(1):]
    # The derived machine lives outside share/machines, where its ROM images are.
    xml=re.sub(r'<filename>([^<]+)</filename>',lambda m:f'<filename>{(stock.parent/m[1]).as_posix()}</filename>',xml)
    name=f'logo_test_{case.base}_{where}_{kb}'
    (USERDATA/'machines').mkdir(parents=True,exist_ok=True)
    (USERDATA/'machines'/f'{name}.xml').write_text(xml)
    return name

def cartridge(page,layout):
    """(ROM file, openMSX ROM type or None) that puts an image in the cartridge slot in a layout."""
    if layout=='mirrored': return build.rom_file(page),None
    if layout=='alone': return build.rom_file(page),f'Normal{build.PAGES[page]:04X}'
    # A 32 KiB ROM for 4000h-BFFFh: the image in its page and a stub in the other.
    other=0xC000-build.PAGES[page]
    stub=b'AB'+(other+0x10).to_bytes(2,'little')+bytes(12)+b'\xC9'
    stub+=b'\xFF'*(build.ROM_SIZE-len(stub))
    image=build.rom_file(page).read_bytes()
    pair=BUILD/f'neighbour_page{page}.rom'
    pair.write_bytes(image+stub if page==1 else stub+image)
    return pair,'Normal4000'

def run(case,page,exe,symbols):
    records_file=BUILD/'openmsx_records.txt'
    records_file.unlink(missing_ok=True)
    write_settings(exe.parent/'share')
    # OPENMSX_USER_DATA moves the settings and machines, OPENMSX_HOME the rest
    # (the persistent data of machines with a clock chip or battery RAM).
    env=dict(os.environ,OPENMSX_HOME=str(USERDATA),OPENMSX_USER_DATA=str(USERDATA),LOGO_OUT=str(records_file),
        LOGO_SYMBOLS=' '.join(f'{name} {address}' for name,address in symbols.items()),
        LOGO_RESETS=str(case.resets),LOGO_SETTLE=str(SETTLE),LOGO_LIMIT=str(LIMIT*(case.resets+1)))
    rom,romtype=cartridge(page,case.rom)
    args=[str(exe),'-machine',machine_for(case,exe.parent/'share'),'-carta',str(rom)]
    if romtype: args+=['-romtype',romtype]
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

def beam_problem(record,start,patterns,late):
    """What is wrong with the beams of a sweep record, or None. start is the
    frame the sweep began on; a record taken when a tick ends still shows the
    colour of the frame before (late=1)."""
    step=int(record['step']); phase=screen.phase_at((int(record['jiffy'])-start-late)&255)
    pattern,colours,sprites=screen.expected(step,phase)
    vram=bytearray(16384); vram[0x1B00:0x1B80]=bytes.fromhex(record['spr']); vram[0x3800:0x3800+len(patterns)]=patterns
    try: shown=screen.sprites_shown(vram,int(record['r1']))
    except AssertionError as error: return f'step {step}: {error}'
    if shown!=sprites: return f'step {step}: the sprites are not the vertical and diagonal beams in their colours'
    if 'cells' in record:
        if bytes.fromhex(record['cells'])!=bytes(c for c in colours if c!=0xF1): return f'step {step}: the horizontal beams are not in their colours'
    elif bytes.fromhex(record['pattern'])!=pattern: return f'step {step}: pattern table'
    elif bytes.fromhex(record['colors'])!=colours: return f'step {step}: colour table'
    return None

def check(case,page,records,symbols):
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
    own=setup[f'page{page}']
    add(None,'the ROM is visible in its own page',own=='4142'+(build.PAGES[page]+0x10).to_bytes(2,'little').hex(),f'header reads {own}')
    notes['header in the other page of the cartridge slot']=setup[f'page{3-page}']
    for boot in range(case.resets+1):
        inits=of('init',boot); returns=of('return',boot); ticks=of('tick',boot)
        finals=of('final',boot); texts=of('text',boot); settled=of('settled',boot)
        notes[f'boot {boot}: INIT calls']=len(inits)
        if inits: notes[f'boot {boot}: at INIT']={name:f"{int(inits[0][name]):04X}h" for name in ('sp','bottom','himem')}
        add(boot,'INIT is called and returns to its caller',inits and len(inits)==len(returns),f'{len(inits)} calls, {len(returns)} returns')
        add(boot,"the caller's stack is intact on return",all(i['guard']==r['guard'] for i,r in zip(inits,returns)))
        add(boot,'RAM outside the work area and the stack is not written',all(i['below']==r['below'] and i['above']==r['above'] for i,r in zip(inits,returns)))
        add(boot,"the caller's sprite size is restored",all(int(i[reg])&3==int(r[reg])&3 for i,r in zip(inits,returns) for reg in ('rg1sav','r1')),
            ' '.join(f"{reg} {int(i[reg]):02X}h->{int(r[reg]):02X}h" for i,r in zip(inits,returns) for reg in ('rg1sav','r1')))
        marks={f['name']:f for f in of('mark',boot)}
        played=len(ticks)==154 and set(marks)==STAGES and len(finals)==len(texts)==1
        add(boot,'the animation plays exactly once',played,f'{len(ticks)} ticks (154 expected), stages reached: {sorted(marks)}')
        if settled:
            settled=settled[0]
            add(boot,'the CPU is outside the cartridge after INIT',settled['pc_slot']!=setup['cart'],settled['pc_slot'])
            text=bytes.fromhex(settled['screen']).decode('latin1')
            notes[f'boot {boot}: {SETTLE}s after INIT']={'screen mode':settled['mode'],'slots in pages 0-3':settled['pages'],
                'text':[line.strip() for line in text.splitlines() if line.strip()][:4]}
        else: add(boot,'the machine keeps running after INIT',False,'no record after the return')
        if not played: continue
        final=finals[0]; text=texts[0]; before=of('ram_before',boot)[0]
        hz=case.hz or (50 if int(inits[0]['bios2b'])&0x80 else 60)
        rate=FRAME_RATE[hz]; times=[float(f['t']) for f in ticks]
        frames=(times[113]-times[0])*rate; expected=int(150*hz/20)-int(hz/20)
        add(boot,'the logo completes in 7.5 seconds',abs(frames-expected)<0.5,f'{frames:.2f} frames at {hz}Hz, {expected} expected')
        frames=(float(final['t'])-float(text['t']))*rate
        add(boot,'the RAM size stays on screen for 2 seconds',abs(frames-2*hz)<=1,f'{frames:.2f} frames, {2*hz} expected')
        # The sweep: the whole screen when each step ends, and the sprites and
        # beam colours in every frame.
        start=int(marks['sweep']['due']); patterns=bytes.fromhex(marks['sweep']['sprpat'])
        steps=[f for f in ticks if 'pattern' in f]
        problems=[p for p in (beam_problem(f,start,patterns,1) for f in steps) if p]
        add(boot,'every step of the sweep shows its beams and its part of the outline',
            [int(f['step']) for f in steps]==list(range(len(screen.SCRIPT))) and not problems,'; '.join(problems[:3]) or f'{len(steps)} steps recorded')
        shown=of('frame',boot)
        problems=[p for p in (beam_problem(f,start,patterns,0) for f in shown) if p]
        seen={screen.phase_at((int(f['jiffy'])-start)&255) for f in shown}
        add(boot,f'the beams have three different colours that move on every {screen.COLOUR_FRAMES} frames, in every frame of the sweep',
            shown and not problems and seen=={0,1,2},'; '.join(problems[:3]) or f'{len(shown)} frames')
        offsets=[(int(f['jiffy'])-start)&255 for f in shown]
        add(boot,'each sweep step is on screen within the frame its tick begins in',offsets==list(range(len(screen.SCRIPT)*hz//10)),
            f'{len(offsets)} frames reached their HALT, {len(screen.SCRIPT)*hz//10} expected')
        add(boot,'the RAM slot is identified',int(final['ram_slot'])==slot_id(before['slot']),f"{int(final['ram_slot']):02X}h, page 3 is {before['slot']}")
        kb=case.kb or int(before['kb'])
        add(boot,'the RAM size is right',int(final['ram_kb'])==kb==int(before['kb']),f"ROM says {final['ram_kb']}KB, openMSX says {before['kb']}KB"+(f', case says {case.kb}KB' if case.kb else ''))
        add(boot,'the probed RAM bytes are restored',before['probes']==text['probes'],f"{before['probes']} -> {text['probes']}")
        glyphs=bytes.fromhex(text['glyphs'])
        picture=bytearray(screen.SOLID)
        picture[0xF40:0xFB8]=b''.join(glyphs[c*8:c*8+8] for c in f'Main RAM : {kb:2}KB'.encode())
        add(boot,'the final picture is the logo plus the RAM size in the system font',bytes.fromhex(final['pattern'])==picture and any(picture[0xF40:0xFB8]))
        add(boot,'the colours are a black box on blue',bytes.fromhex(final['colors'])==screen.box_colours() and int(final['r7'])==4,f"R7={final['r7']}")
        add(boot,'the name table is standard and sprites are off',bytes.fromhex(final['names'])==bytes(range(256))*3 and int(final['sprite'])==208)
        left=[f'{symbols["work"]+i:04X}h={value:02X}h' for i,value in enumerate(bytes.fromhex(returns[-1]['work'])) if value]
        add(boot,'the work RAM is cleared on return',not left,f'left behind: {" ".join(left[:8]) or "nothing"}')
    return checks,notes

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Run the ROM images in openMSX and check them.')
    parser.add_argument('--page',type=int,choices=list(build.PAGES),help='image to test (default: all)')
    parser.add_argument('--machine',help='stock openMSX machine to test instead of the built-in cases')
    parser.add_argument('--rom',choices=LAYOUTS,default='alone',help='with --machine: what the other page of the cartridge slot holds')
    parser.add_argument('--ram',metavar='SLOT:KB',help='with --machine: replace its RAM, e.g. 0-2:64 or 3:16')
    args=parser.parse_args()
    if args.ram and not re.fullmatch(r'[0-3](-[0-3])?:(8|16|32|48|64)',args.ram): parser.error('--ram takes SLOT:KB, e.g. 0-2:64')
    ram=(args.ram.split(':')[0],int(args.ram.split(':')[1])) if args.ram else None
    pages=[args.page] if args.page else list(build.PAGES)
    for page in pages:
        for needed in (build.rom_file(page),build.symbols_file(page)):
            if not needed.is_file(): sys.exit(f'build/{needed.name} not found: run tools/build.py first')
    name=f'{args.machine}, ROM {args.rom}'+(f', {placed(*ram)}' if ram else '')
    cases=[Case(name,args.machine,ram,args.rom,1,None,ram and ram[1])] if args.machine else CASES
    exe=find_openmsx()
    report=[]; failed=0
    for page in pages:
        symbols=json.loads(build.symbols_file(page).read_text())
        for case in cases:
            checks,notes=check(case,page,run(case,page,exe,symbols),symbols)
            bad=[c for c in checks if not c[2]]
            failed+=len(bad)
            print(f'{"FAIL" if bad else "ok  "} page {page}, {case.name}: {len(checks)-len(bad)}/{len(checks)} checks')
            for boot,what,_,detail in bad: print(f'       {"" if boot is None else f"boot {boot}: "}{what} -- {detail}')
            report.append({'page':page,'case':case.name,'notes':notes,'checks':[{'boot':b,'check':w,'passed':p,'detail':d} for b,w,p,d in checks]})
    (BUILD/'openmsx_test.json').write_text(json.dumps(report,indent=2))
    print(f'{failed} failed checks' if failed else 'all checks passed')
    sys.exit(1 if failed else 0)
