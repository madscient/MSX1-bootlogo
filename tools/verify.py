"""Focused Z80/BIOS/VDP harness, NOT a full MSX emulator.
Executes build/msx_logo.rom, checks animation milestones and cycle budgets.
Needs the output of tools/build.py. The report goes to build/. Optional Pillow
also writes a GIF there, made from actual emulated VRAM writes.
"""
from pathlib import Path
import json, hashlib, sys
ROOT=Path(__file__).resolve().parent.parent
ASSETS=ROOT/'assets'; BUILD=ROOT/'build'
for needed in (BUILD/'msx_logo.rom',BUILD/'symbols.json'):
    if not needed.is_file(): sys.exit(f'build/{needed.name} not found: run tools/build.py first')
SYM=json.loads((BUILD/'symbols.json').read_text())
ROM=(BUILD/'msx_logo.rom').read_bytes()
assert len(ROM)==16384 and ROM[:2]==b'AB'
assert int.from_bytes(ROM[2:4],'little')==SYM['init']==0x4010
assert SYM['rom_end']<=0x8000
# Stand-in for the BIOS system font (the BIOS ROM is not bundled): simple 5x7
# glyphs placed where CGTABL/CGPNT point. The real machine shows its own font.
FONT_ADDR=0x1bbf
def stand_in_font():
    font={
      'M':[17,27,21,21,17,17,17], 'a':[0,0,14,1,15,17,15],
      'i':[4,0,12,4,4,4,14], 'n':[0,0,30,17,17,17,17],
      'R':[30,17,17,30,20,18,17], 'A':[14,17,17,31,17,17,17],
      'M':[17,27,21,21,17,17,17], ':':[0,4,4,0,4,4,0],
      '1':[4,12,4,4,4,4,14], '2':[14,17,1,2,4,8,31],
      '3':[30,1,1,14,1,1,30], '4':[2,6,10,18,31,2,2],
      '6':[14,16,16,30,17,17,14], '8':[14,17,17,14,17,17,14],
      'K':[17,18,20,24,20,18,17], 'B':[30,17,17,30,17,17,30],
      ' ':[0]*7}
    data=bytearray(2048)
    for ch,rows in font.items():
        for y,row in enumerate(rows): data[ord(ch)*8+y]=(row<<3)&255
    return data
FONT=stand_in_font()
def label(kb):
    return b''.join(bytes(FONT[c*8:c*8+8]) for c in f'Main RAM : {kb}KB'.encode())
class Machine:
    def __init__(self,hz,kb=64,expanded=False,mirrored=False,rom_mirror=False):
        self.mem=bytearray(65536); self.mem[0x4000:0x8000]=ROM
        self.mem[0x2b]=128 if hz==50 else 0
        self.mem[FONT_ADDR:FONT_ADDR+2048]=FONT
        self.mem[4:6]=FONT_ADDR.to_bytes(2,'little') # CGTABL
        self.mem[0xf91f]=0; self.mem[0xf920:0xf922]=FONT_ADDR.to_bytes(2,'little') # CGPNT
        self.r=[0]*8; self.sp=0xf370; self.pc=0x4010
        self.entry_sp=self.sp; self.mem[self.sp:self.sp+2]=bytes([0x34,0x12])
        self.return_guard=bytes(self.mem[self.sp:self.sp+16])
        self.kb=kb; self.mirrored=mirrored; self.rom_mirror=rom_mirror
        self.ram=bytearray((i*17+9)&255 for i in range(65536))
        self.slot=0x8b if expanded else 3
        self.mem[0xfcc4]=0x80 if expanded else 0
        self.mem[0xfcc8]=0 # SLTTBL deliberately stale: the ROM must not rely on it
        self.mem[0xffff]=(~0xa0)&255 if expanded else 0xff # secondary register reads back inverted
        self.probe_before=None; self.text_frame=None; self.last_image=None
        self.z=False; self.s=False; self.c=False
        self.vram=bytearray(16384); self.reg=[0]*8
        self.latch=None; self.addr=0; self.cycles=0; self.hz=hz
        self.frame_cycles=3579545/hz; self.frames=0
        self.samples=[]; self.deadlines=[]; self.issues=[]; self.phase={}
    def pair(self,n): return (self.r[n]<<8)|self.r[n+1]
    def setpair(self,n,v): self.r[n]=(v>>8)&255; self.r[n+1]=v&255
    def fetch(self):
        a=self.mem[self.pc]; self.pc=(self.pc+1)&65535; return a
    def word(self): return self.fetch() | self.fetch()<<8
    def push(self,v):
        self.sp-=2; self.mem[self.sp]=v&255; self.mem[self.sp+1]=(v>>8)&255
    def pop(self):
        v=self.mem[self.sp]|self.mem[self.sp+1]<<8; self.sp+=2; return v
    def get(self,r): return self.mem[self.pair(4)] if r==6 else self.r[r]
    def put(self,r,v):
        if r==6: self.mem[self.pair(4)]=v&255
        else: self.r[r]=v&255
    def flags(self,v): self.z=(v&255)==0; self.s=bool(v&128); self.c=v<0 or v>255
    def advance(self,n):
        self.cycles+=n
        frame=int(self.cycles/self.frame_cycles)
        if frame>self.frames:
            self.mem[0xfc9e]=frame&255; self.mem[0xfc9f]=(frame>>8)&255
            self.frames=frame
    def ram_index(self,addr):
        bottom=65536-self.kb*1024
        if self.mirrored: return bottom+addr%(self.kb*1024)
        return addr if addr>=bottom else None
    def ram_read(self,addr):
        index=self.ram_index(addr)
        if index is None: return 255
        return self.mem[index] if index>=0xc000 else self.ram[index]
    def ram_write(self,addr,value):
        index=self.ram_index(addr)
        if index is not None:
            if index>=0xc000: self.mem[index]=value
            else: self.ram[index]=value
    def bios(self,addr):
        if addr==0x138:
            self.r[7]=0xd4 # page 3 primary slot 3; page1 cartridge slot1
        elif addr==0x0c and self.r[7]==1:
            # Cartridge slot 1: ROM at 4000h, optionally mirrored in every page.
            address=self.pair(4)
            result=ROM[address&0x3fff] if self.rom_mirror or 0x4000<=address<0x8000 else 255
            self.r[0:4]=[0xa5,0x69,0x96,0x5a]; self.r[7]=result
        elif addr in (0x0c,0x14):
            assert self.r[7]==self.slot,(self.r[7],self.slot)
            address=self.pair(4); result=self.ram_read(address)
            assert address<0xc000,'page 3 must not be accessed through RDSLT/WRSLT'
            if addr==0x14: self.ram_write(address,self.r[3])
            # Model BIOS-documented clobbers, not friendly register stubs.
            self.r[0:4]=[0xa5,0x69,0x96,0x5a]
            self.r[7]=result if addr==0x0c else 0xcc
        elif addr==0x5c:
            src=self.pair(4); dst=self.pair(2); count=self.pair(0)
            self.vram[dst:dst+count]=self.mem[src:src+count]
            self.advance(count*40)
        elif addr==0x5f:
            if self.r[7]==1:
                self.last_image=bytes(self.vram)
                self.text_screen=True
                return
            self.reg=[2,0xe0,6,255,3,0x36,7,0]
            self.vram[0x1800:0x1b00]=bytes(range(256))*3
        elif addr in (0x41,0x44): pass
        elif addr==0x56:
            start=self.pair(4); count=self.pair(0)
            self.vram[start:start+count]=bytes([self.r[7]])*count
            self.advance(count*40)
        elif addr==0x4d: self.vram[self.pair(4)]=self.r[7]
        elif addr==0x47: self.reg[self.r[1]]=self.r[0]
        else: raise AssertionError(f'Unknown BIOS {addr:04x}')
        self.advance(100)
    def step(self):
        if self.pc==SYM['tick_done']:
            self.deadlines.append((self.frames,self.mem[0xd805]))
            self.samples.append((bytes(self.vram),self.reg[7]))
        for name in ('sweep','fill','blue','ram_stage','ram_text_visible','return_to_basic'):
            if self.pc==SYM[name] and name not in self.phase: self.phase[name]=self.frames
        if self.pc==SYM['count_ram']:
            self.probe_before=[self.ram_read(a) for a in (0x100,0x4100,0x8100,0xc100)]
        if self.pc==SYM['ram_text_visible']: self.text_frame=self.frames
        if self.pc==SYM['return_to_basic']: self.kb_seen=self.mem[0xd824]; self.img_seen=bytes(self.mem[0xc000:0xd800])
        op=self.fetch(); cycles=4
        if op in (0xf3,0xfb): pass
        elif op==0xed:
            ext=self.fetch()
            if ext==0x56: cycles=8
            elif ext==0xb0:
                n=self.pair(0); src=self.pair(4); dst=self.pair(2)
                for i in range(n): self.mem[dst+i]=self.mem[src+i]
                self.setpair(0,0); self.setpair(4,src+n); self.setpair(2,dst+n); cycles=21*n-5
            else: raise AssertionError(hex(ext))
        elif op in (1,0x11,0x21,0x31):
            v=self.word()
            if op==0x31: self.sp=v
            else: self.setpair((op>>4)*2,v)
            cycles=10
        elif op&0xc7==6:
            r=(op>>3)&7; self.put(r,self.fetch()); cycles=10 if r==6 else 7
        elif op==0x76:
            cycles=int((self.frames+1)*self.frame_cycles-self.cycles)+1
        elif 0x40<=op<=0x7f:
            dst=(op>>3)&7; src=op&7; self.put(dst,self.get(src)); cycles=7 if 6 in (dst,src) else 4
        elif op in (0x22,0x2a):
            addr=self.word()
            if op==0x22:
                self.mem[addr]=self.r[5]; self.mem[addr+1]=self.r[4]
            else: self.r[5]=self.mem[addr]; self.r[4]=self.mem[addr+1]
            cycles=16
        elif op in (0x32,0x3a):
            addr=self.word()
            if op==0x32: self.mem[addr]=self.r[7]
            else: self.r[7]=self.mem[addr]
            cycles=13
        elif op in (0x12,0x1a):
            if op==0x12: self.mem[self.pair(2)]=self.r[7]
            else: self.r[7]=self.mem[self.pair(2)]
            cycles=7
        elif op in (0xb1,0xb3,0xb7):
            v=self.r[7]|self.r[op&7]; self.flags(v); self.r[7]=v
        elif op in (0xaf,0x80,0x81,0x87,0x90,0xb6,0xc6,0xd6,0xe6,0xf6,0xfe):
            a=self.r[7]
            operand=self.fetch() if op in (0xc6,0xd6,0xe6,0xf6,0xfe) else (self.r[0] if op in (0x80,0x90) else self.r[1] if op==0x81 else a)
            if op==0xaf: v=0
            elif op in (0x80,0x81,0x87,0xc6): v=a+operand
            elif op in (0x90,0xd6,0xfe): v=a-operand
            elif op==0xe6: v=a&operand
            elif op==0xf6: v=a|operand
            else: v=a|self.mem[self.pair(4)]
            self.flags(v)
            if op!=0xfe: self.r[7]=v&255
            cycles=7 if op in (0xb6,0xc6,0xd6,0xe6,0xf6,0xfe) else 4
        elif op in (0x3c,0x0d):
            carry=self.c; r=7 if op==0x3c else 1; v=self.r[r]+(1 if r==7 else -1)
            self.flags(v); self.c=carry; self.r[r]=v&255
        elif op in (0x23,0x13):
            r=4 if op==0x23 else 2; self.setpair(r,self.pair(r)+1); cycles=6
        elif op in (9,0x19,0x29):
            n={9:0,0x19:2,0x29:4}[op]; v=self.pair(4)+self.pair(n)
            self.setpair(4,v); self.c=v>65535; cycles=11
        elif op==0xeb:
            a=self.pair(4); self.setpair(4,self.pair(2)); self.setpair(2,a)
        elif op==0xcb:
            assert self.fetch()==0x3f
            a=self.r[7]; self.r[7]>>=1; self.flags(self.r[7]); self.c=bool(a&1); cycles=8
        elif op in (0xc5,0xd5,0xe5,0xf5,0xc1,0xd1,0xe1,0xf1):
            r=((op>>4)&3)*2
            if op&4:
                v=(self.r[7]<<8)|(self.s<<7)|(self.z<<6)|self.c if r==6 else self.pair(r)
                self.push(v); cycles=11
            else:
                v=self.pop()
                if r==6: self.r[7]=v>>8; self.s=bool(v&128); self.z=bool(v&64); self.c=bool(v&1)
                else: self.setpair(r,v)
                cycles=10
        elif op==0xcd:
            addr=self.word()
            if addr<0x4000: self.bios(addr)
            else: self.push(self.pc); self.pc=addr
            cycles=17
        elif op==0xc8:
            cycles=5
            if self.z: self.pc=self.pop(); cycles=11
        elif op==0xc9: self.pc=self.pop(); cycles=10
        elif op in (0xc3,0xc2,0xca,0xda,0xd2,0xf2):
            addr=self.word()
            if {0xc3:True,0xc2:not self.z,0xca:self.z,0xda:self.c,0xd2:not self.c,0xf2:not self.s}[op]: self.pc=addr
            cycles=10
        elif op==0x10:
            rel=self.fetch(); rel=rel-256 if rel>=128 else rel
            self.r[0]=(self.r[0]-1)&255
            if self.r[0]: self.pc+=rel; cycles=13
            else: cycles=8
        elif op==0xd3:
            port=self.fetch(); a=self.r[7]; cycles=11
            if port==0x99:
                if self.latch is None: self.latch=a
                else:
                    assert a&0x40 and not a&0x80
                    self.addr=((a&63)<<8)|self.latch; self.latch=None
            elif port==0x98:
                assert self.addr<6144 or 0x2000<=self.addr<0x3800, f'Pattern write outside table {self.addr:04x}'
                self.vram[self.addr]=a; self.addr=(self.addr+1)&16383
            else: raise AssertionError(port)
        else: raise AssertionError(f'Unsupported {op:02x} at {self.pc-1:04x}')
        self.advance(cycles)
    def run(self):
        for _ in range(12000000):
            if self.pc==0x1234: break
            self.step()
        else: raise AssertionError('ROM did not finish')
        assert len(self.samples)==154,len(self.samples)
        assert self.samples[0][0][:6144]==bytes(6144)
        assert self.samples[46][0][:6144]==(ASSETS/'outline.dat').read_bytes()
        expected=bytearray((ASSETS/'solid.dat').read_bytes())
        expected[0xf40:0xfb8]=label(self.kb)
        assert self.vram[:6144]==expected
        assert self.kb_seen==self.kb
        assert self.probe_before==[self.ram_read(a) for a in (0x100,0x4100,0x8100,0xc100)]
        assert self.img_seen==(ASSETS/'solid.dat').read_bytes()
        # On return the work RAM is zero again (only the mirror marker may remain).
        assert not any(self.mem[0xc000:0xd820]) and not any(self.mem[0xd822:0xd8a0])
        # Work RAM ends at D89Fh: nothing between it and the caller's stack is written.
        assert not any(self.mem[0xd8a0:0xf300]),'RAM near the BIOS stack was modified'
        assert self.reg[7]==4
        for y in range(192):
            for x in range(0,256,8):
                index=(y//8)*256+x+y%8
                expected=0xf1 if 32<=x<224 and 48<=y<112 else 0xf4
                assert self.vram[0x2000+index]==expected
        # Every blue-wipe sample has exactly the expected top-to-bottom extent.
        for band in range(24):
            colors=self.samples[74+band][0][0x2000:0x3800]
            for y in range(192):
                expected=0xf4 if y<(band+1)*8 else 0xf1
                assert colors[(y//8)*256+y%8]==expected
        assert self.vram[0x1800:0x1b00]==bytes(range(256))*3
        assert self.vram[0x1b00]==208
        assert self.sp==self.entry_sp+2
        assert bytes(self.mem[self.entry_sp:self.entry_sp+16])==self.return_guard
        assert self.text_screen
        assert self.phase['return_to_basic']-self.text_frame==2*self.hz
        assert all((frame-deadline)&255==0 for frame,deadline in self.deadlines), self.deadlines
        assert self.deadlines[113][0]-self.deadlines[0][0]==int(150*self.hz/20)-int(self.hz/20)
        return {'Hz':self.hz,'RAM_KB':self.kb,'expanded_slot':bool(self.slot&128),'mirrored':self.mirrored,'ticks':154,'logo_seconds':7.5,'RAM_display_seconds':2.0,'returns_to_BIOS':True,'deadlines_met':True,'final_pattern_match':True,'stack_balanced':True,'black_rectangle_preserved':True,'top_to_bottom_wipe':True}

def preview(samples):
    from PIL import Image
    frames=[]
    for vram,bg in samples:
        im=Image.new('RGB',(256,192))
        palette={0:(0,0,0),1:(0,0,0),4:(84,85,237),15:(255,255,255)}
        pix=im.load()
        for y in range(192):
            for x in range(256):
                index=(y//8)*256+(x//8)*8+y%8
                ink=bool(vram[index]&(128>>(x%8)))
                color=vram[0x2000+index]
                color=(color>>4) if ink else (color&15)
                pix[x,y]=palette[color if color else bg]
        frames.append(im.resize((768,576),Image.Resampling.NEAREST))
    durations=[100 if 10<=i<46 else 50 for i in range(len(frames))]
    frames[0].save(BUILD/'preview.gif',save_all=True,append_images=frames[1:],duration=durations,loop=0,optimize=False)
    frames[-1].save(BUILD/'final.png')
    for index in (17,29,46,62,85): frames[index].save(BUILD/f'frame_{index:03}.png')
    sheet=Image.new('RGB',(768*3,576*2))
    for i,index in enumerate((17,29,46,62,85,113)): sheet.paste(frames[index],((i%3)*768,(i//3)*576))
    sheet.resize((1152,576)).save(BUILD/'storyboard.png')

if __name__=='__main__':
    results=[]
    for hz in (50,60):
        machine=Machine(hz); results.append(machine.run())
    # Re-use completed animation state; run the RAM/return stage for other
    # slot/capacity layouts so the hardware-independent animation isn't repeated.
    for kb,expanded,mirrored in ((16,False,False),(32,False,False),(48,True,False),(64,True,False),(16,True,True),(32,True,True)):
        test=Machine(60,kb,expanded,mirrored)
        test.pc=SYM['ram_stage']; test.mem[0xd804]=6
        for _ in range(100000):
            if test.pc==0x1234: break
            test.step()
        else: raise AssertionError('RAM stage did not return')
        assert test.kb_seen==kb
        assert test.probe_before==[test.ram_read(a) for a in (0x100,0x4100,0x8100,0xc100)]
        assert test.sp==test.entry_sp+2
        assert not any(test.mem[0xd8a0:0xf300])
        assert test.phase['return_to_basic']-test.text_frame==120
        assert test.vram[0xf40:0xfb8]==label(kb)
        results.append({'RAM_KB':kb,'expanded_slot':expanded,'mirrored':mirrored,'probe_restored':True,'returns_to_BIOS':True,'RAM_display_seconds':2.0})
    # Page-2 mirror: the BIOS calls INIT twice. First call plays and leaves the
    # marker; the second call must return immediately and consume the marker.
    first=Machine(60,rom_mirror=True); first.run()
    assert bytes(first.mem[0xd820:0xd822])==b'\x5a\xa5'
    second=Machine(60,rom_mirror=True); second.mem[0xd820:0xd822]=b'\x5a\xa5'
    for _ in range(1000):
        if second.pc==0x1234: break
        second.step()
    else: raise AssertionError('mirror call did not return at once')
    assert second.sp==second.entry_sp+2 and not second.samples and not any(second.vram)
    assert bytes(second.mem[0xd820:0xd822])==b'\0\0'
    # No mirror: no marker is left, so every reset plays the animation.
    assert bytes(machine.mem[0xd820:0xd822])==b'\0\0'
    results.append({'ROM_mirrored_at_8000h':True,'first_INIT_plays':True,'second_INIT_skipped':True,'unmirrored_leaves_no_marker':True})
    report={'method':'Focused instruction-execution harness with BIOS stubs; not full MSX emulation',
      'size':len(ROM),'header':ROM[:16].hex(' '),'init':'0x4010','sha256':hashlib.sha256(ROM).hexdigest(),'results':results}
    (BUILD/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    try: preview(machine.samples)
    except ImportError: print('Pillow unavailable: preview skipped')
