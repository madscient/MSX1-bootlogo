"""Focused Z80/BIOS/VDP harness, NOT a full MSX emulator.
Executes the ROM images in build/, checks animation milestones and cycle budgets.
Needs the output of tools/build.py.

  python tools/verify.py            check every image
  python tools/verify.py --page 2   check one image: 1 or 2

The report goes to build/. Optional Pillow also writes a GIF there, made from
actual emulated VRAM writes.
"""
from pathlib import Path
import argparse, json, hashlib, sys
import build, screen
ROOT=Path(__file__).resolve().parent.parent
BUILD=ROOT/'build'
PROBES=(0x100,0x4100,0x8100,0xC100,0xE100)
PHASES=('sweep','fill','blue','ram_stage','ram_text_visible','return_to_basic')
HOOKS=PHASES+('tick_done','beams_off','count_ram')
# Common approximation of the fixed TMS9918A palette, colour codes 0-15.
PALETTE=[(0,0,0),(0,0,0),(33,200,66),(94,220,120),(84,85,237),(125,118,252),(212,82,77),(66,235,245),
    (252,85,84),(255,121,120),(212,193,84),(230,206,128),(33,176,59),(201,91,186),(204,204,204),(255,255,255)]

def load(page):
    """Make one image the ROM under test."""
    global ROM,SYM,BASE
    for needed in (build.rom_file(page),build.symbols_file(page)):
        if not needed.is_file(): sys.exit(f'build/{needed.name} not found: run tools/build.py first')
    SYM=json.loads(build.symbols_file(page).read_text())
    ROM=build.rom_file(page).read_bytes(); BASE=build.PAGES[page]
    assert len(ROM)==16384 and ROM[:2]==b'AB'
    assert int.from_bytes(ROM[2:4],'little')==SYM['init']==BASE+0x10
    assert SYM['rom_end']<=BASE+0x4000
    # The work RAM must exist on an 8 KiB machine and stay clear of the stack.
    assert 0xE000<=SYM['work'] and SYM['work']+SYM['work_size']<=0xF000

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
    return b''.join(bytes(FONT[c*8:c*8+8]) for c in f'Main RAM : {kb:2}KB'.encode())
class Machine:
    def __init__(self,hz,kb=64,ram='3',mirrored=False,sprite_mode=0,record=False):
        self.mem=bytearray(65536); self.mem[BASE:BASE+0x4000]=ROM
        self.mem[0x2b]=128 if hz==50 else 0
        self.mem[FONT_ADDR:FONT_ADDR+2048]=FONT
        self.mem[4:6]=FONT_ADDR.to_bytes(2,'little') # CGTABL
        self.kb=kb; self.mirrored=mirrored; self.where=ram
        # Page 3 has no RAM below this address (8 KiB machines).
        self.bottom=max(0xc000,65536-kb*1024)
        # Recognisable contents in the free RAM: "left alone" and "cleared"
        # both differ from "written".
        for a in range(0xc000,0xf380): self.mem[a]=(a*29+7)&255 if a>=self.bottom else 0xff
        self.mem[0xf91f]=0; self.mem[0xf920:0xf922]=FONT_ADDR.to_bytes(2,'little') # CGPNT
        self.r=[0]*8; self.sp=0xf370; self.pc=SYM['init']
        self.entry_sp=self.sp; self.mem[self.sp:self.sp+2]=bytes([0x34,0x12])
        self.return_guard=bytes(self.mem[self.sp:self.sp+16])
        self.ram=bytearray((i*17+9)&255 for i in range(65536))
        # The RAM is in primary slot 3 or 0, or in a subslot of it ('3-2', '0-2').
        # The BIOS is in slot 0, in subslot 0 when slot 0 is expanded. The
        # cartridge is in slot 1.
        self.primary=int(ram[0]); self.sub=int(ram[2]) if '-' in ram else None
        self.slot=self.primary if self.sub is None else 0x80|self.sub<<2|self.primary
        self.mem[0xfcc1+self.primary]=0 if self.sub is None else 0x80 # EXPTBL
        self.mem[0xfcc5+self.primary]=0 # SLTTBL deliberately stale: the ROM must not rely on it
        # The secondary slot register of the RAM's primary slot: the RAM in
        # pages 3 and 2, subslot 0 in pages 1 and 0. FFFFh reads it back inverted.
        self.sub_reg=self.sub_reg_at_entry=(self.sub or 0)*0x50
        self.sharing=self.primary==0 and not self.sub # the RAM shares its slot with the BIOS
        self.slots=self.primary<<6|1<<(BASE>>14)*2
        self.sprite_mode=sprite_mode; self.mem[0xf3e0]=0xe0|sprite_mode # RG1SAV
        self.probe_before=None; self.text_frame=None; self.last_image=None; self.text_screen=False
        self.z=False; self.s=False; self.c=False; self.iff=True
        self.vram=bytearray(16384); self.reg=[0,0xe0|sprite_mode,0,0,0,0,0,0]
        self.latch=None; self.addr=0; self.cycles=0; self.hz=hz
        self.vdp_time=0; self.vdp_gap=None
        self.frame_cycles=3579545/hz; self.frames=0
        self.samples=[]; self.deadlines=[]; self.issues=[]; self.phase={}
        self.at={SYM[name]:name for name in HOOKS}
        self.step_index=None; self.swept=False; self.mark=None; self.work=[]
        self.beam_frames=0; self.phases_seen=set()
        self.record=record; self.shots=[]
        self.initial=bytes(self.mem)
    def pair(self,n): return (self.r[n]<<8)|self.r[n+1]
    def setpair(self,n,v): self.r[n]=(v>>8)&255; self.r[n+1]=v&255
    def fetch(self):
        a=self.mem[self.pc]; self.pc=(self.pc+1)&65535; return a
    def word(self): return self.fetch() | self.fetch()<<8
    def push(self,v):
        self.sp-=2; self.mem[self.sp]=v&255; self.mem[self.sp+1]=(v>>8)&255
    def pop(self):
        v=self.mem[self.sp]|self.mem[self.sp+1]<<8; self.sp+=2; return v
    def page_0_is_ram(self):
        """Page 0 shows the RAM slot instead of the BIOS: the RAM is in another
        subslot of slot 0 and the secondary slot register selects it there."""
        return self.primary==0 and bool(self.sub) and self.sub_reg&3==self.sub
    def rd(self,addr):
        if addr==0xffff and self.sub is not None: return ~self.sub_reg&255
        if addr<0x4000 and self.page_0_is_ram(): return self.ram_read(addr)
        if 0xc000<=addr<self.bottom:
            if not self.mirrored: return 0xff
            addr=self.bottom+addr%(self.kb*1024)
        return self.mem[addr]
    def wr(self,addr,value):
        if addr==0xffff and self.sub is not None:
            assert not self.iff,'secondary slot register written with interrupts enabled'
            assert value&0xfc==self.sub_reg_at_entry&0xfc,f'secondary slot register {value:02x}h moves pages 1-3'
            self.sub_reg=value; return
        if addr<0x4000 and self.page_0_is_ram(): self.ram_write(addr,value); return
        # Apart from that the ROM may write to page 3 only, and where the
        # machine has no RAM only to the address it probes.
        assert addr>=0xc000,f'write to {addr:04x}h, outside page 3'
        if addr<self.bottom:
            if not self.mirrored:
                assert addr==0xc100,f'write to {addr:04x}h, where this machine has no RAM'
                return
            addr=self.bottom+addr%(self.kb*1024)
        self.mem[addr]=value&255
    def get(self,r): return self.rd(self.pair(4)) if r==6 else self.r[r]
    def put(self,r,v):
        if r==6: self.wr(self.pair(4),v)
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
    def set_reg(self,n,value):
        self.reg[n]=value; self.mem[0xf3df+n]=value # RG0SAV..RG7SAV
    def bios(self,addr):
        assert not self.page_0_is_ram(),f'BIOS {addr:04x}h called while page 0 does not show the BIOS'
        if addr==0x138:
            self.r[7]=self.slots
        elif addr in (0x0c,0x14):
            assert self.r[7]==self.slot,(self.r[7],self.slot)
            address=self.pair(4); result=self.ram_read(address)
            assert address<0xc000,'page 3 must not be accessed through RDSLT/WRSLT'
            # On page 0 of slot 0 the BIOS would switch itself away, and in its
            # own slot pages 0 and 1 are the system ROM, which is not to be written.
            assert address>=0x4000 or self.primary!=0,'RDSLT/WRSLT on page 0 of the primary slot of the BIOS'
            assert address>=0x8000 or not self.sharing,'RDSLT/WRSLT on the pages of the BIOS'
            if addr==0x14: self.ram_write(address,self.r[3])
            # Model BIOS-documented clobbers, not friendly register stubs.
            self.r[0:4]=[0xa5,0x69,0x96,0x5a]
            self.r[7]=result if addr==0x0c else 0xcc
        elif addr==0x5c:
            src=self.pair(4); dst=self.pair(2); count=self.pair(0)
            self.vram[dst:dst+count]=self.mem[src:src+count]
            self.advance(count*40)
        elif addr==0x5f:
            # A mode change keeps the sprite size and magnification bits of R1.
            keep=self.mem[0xf3e0]&3
            if self.r[7]==1:
                self.last_image=bytes(self.vram)
                self.text_screen=True
                self.set_reg(1,0xe0|keep)
                return
            for n,value in enumerate([2,0xe0|keep,6,255,3,0x36,7,0]): self.set_reg(n,value)
            self.vram[0x1800:0x1b00]=bytes(range(256))*3
        elif addr==0x41: self.set_reg(1,self.reg[1]&0xbf)
        elif addr==0x44: self.set_reg(1,self.reg[1]|0x40)
        elif addr==0x56:
            start=self.pair(4); count=self.pair(0)
            self.vram[start:start+count]=bytes([self.r[7]])*count
            self.advance(count*40)
        elif addr==0x4d: self.vram[self.pair(4)]=self.r[7]
        elif addr==0x47: self.set_reg(self.r[1],self.r[0])
        else: raise AssertionError(f'Unknown BIOS {addr:04x}')
        self.advance(100)
    def hook(self,name):
        if name=='tick_done':
            self.deadlines.append((self.frames,self.mem[SYM['w_due']]))
            self.samples.append((bytes(self.vram),self.reg[7],self.reg[1]))
            self.mark=self.cycles
        if name in PHASES and name not in self.phase: self.phase[name]=self.frames
        if name=='sweep':
            if self.step_index is None: self.sweep_frame=self.frames
            self.step_index=0 if self.step_index is None else self.step_index+1
        if name=='beams_off': self.step_index=None; self.swept=True
        if name=='count_ram': self.probe_before=[self.ram_read(a) for a in PROBES]
        if name=='ram_text_visible': self.text_frame=self.frames
        if name=='return_to_basic': self.kb_seen=self.mem[SYM['w_ram_kb']]
    def on_halt(self):
        """A HALT ends the work of a frame: what is in VRAM now is what that frame shows."""
        assert self.iff,'HALT with interrupts disabled'
        if self.step_index is not None:
            if self.mark is not None: self.work.append(self.cycles-self.mark); self.mark=None
            phase=screen.phase_at(self.frames-self.sweep_frame)
            pattern,colours,sprites=screen.expected(self.step_index,phase)
            where=f'sweep step {self.step_index}, frame {self.frames-self.sweep_frame}'
            assert self.vram[:6144]==pattern,f'{where}: pattern table'
            assert self.vram[0x2000:0x3800]==colours,f'{where}: colour table'
            assert screen.sprites_shown(self.vram,self.reg[1])==sprites,f'{where}: sprites'
            self.beam_frames+=1; self.phases_seen.add(phase)
        elif self.swept and 'fill' not in self.phase:
            assert self.vram[:6144]==screen.OUTLINE and self.vram[0x2000:0x3800]==bytes([0xf1])*6144,'beams left on the outline'
            assert self.vram[0x1b00]==208,'sprites left on the outline'
        if self.record:
            shot=(bytes(self.vram),self.reg[7],self.reg[1])
            if not self.shots or self.shots[-1][1]!=shot: self.shots.append((self.frames,shot))
    def step(self):
        name=self.at.get(self.pc)
        if name: self.hook(name)
        op=self.fetch(); cycles=4
        if op==0xf3: self.iff=False
        elif op==0xfb: self.iff=True
        elif op==0xed:
            ext=self.fetch()
            if ext==0x56: cycles=8
            elif ext==0xb0:
                n=self.pair(0); src=self.pair(4); dst=self.pair(2)
                for i in range(n): self.wr(dst+i,self.rd(src+i))
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
            self.on_halt()
            cycles=int((self.frames+1)*self.frame_cycles-self.cycles)+1
        elif 0x40<=op<=0x7f:
            dst=(op>>3)&7; src=op&7; self.put(dst,self.get(src)); cycles=7 if 6 in (dst,src) else 4
        elif op in (0x22,0x2a):
            addr=self.word()
            if op==0x22:
                self.wr(addr,self.r[5]); self.wr(addr+1,self.r[4])
            else: self.r[5]=self.rd(addr); self.r[4]=self.rd(addr+1)
            cycles=16
        elif op in (0x32,0x3a):
            addr=self.word()
            if op==0x32: self.wr(addr,self.r[7])
            else: self.r[7]=self.rd(addr)
            cycles=13
        elif op in (0x12,0x1a):
            if op==0x12: self.wr(self.pair(2),self.r[7])
            else: self.r[7]=self.rd(self.pair(2))
            cycles=7
        elif 0x80<=op<=0xbf or op&0xc7==0xc6:
            # ADD, SUB, AND, XOR, OR, CP with a register, (HL) or an immediate.
            if op<0xc0: operand=self.get(op&7); cycles=7 if op&7==6 else 4
            else: operand=self.fetch(); cycles=7
            kind=(op>>3)&7; a=self.r[7]
            if kind==0: v=a+operand
            elif kind in (2,7): v=a-operand
            elif kind==4: v=a&operand
            elif kind==5: v=a^operand
            elif kind==6: v=a|operand
            else: raise AssertionError(f'Unsupported {op:02x} at {self.pc-1:04x}')
            self.flags(v)
            if kind!=7: self.r[7]=v&255
        elif op&0xc7 in (4,5):
            # INC and DEC of a register or (HL) leave the carry alone.
            carry=self.c; r=(op>>3)&7; v=self.get(r)+(-1 if op&1 else 1)
            self.flags(v); self.c=carry; self.put(r,v); cycles=11 if r==6 else 4
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
            assert not self.iff,'VDP port written with interrupts enabled'
            if port==0x99:
                if self.latch is None: self.latch=a
                else:
                    assert a&0x40 and not a&0x80
                    self.addr=((a&63)<<8)|self.latch; self.latch=None
            elif port==0x98:
                assert self.addr<6144 or 0x1b00<=self.addr<0x1b80 or 0x2000<=self.addr<0x3800, f'VRAM write outside the tables {self.addr:04x}'
                # T states since the previous access; the VDP needs 8 us = 29.
                gap=self.cycles-self.vdp_time
                self.vdp_gap=gap if self.vdp_gap is None else min(gap,self.vdp_gap)
                self.vram[self.addr]=a; self.addr=(self.addr+1)&16383
            else: raise AssertionError(port)
            self.vdp_time=self.cycles
        else: raise AssertionError(f'Unsupported {op:02x} at {self.pc-1:04x}')
        self.advance(cycles)
    def until_return(self,steps,what):
        for _ in range(steps):
            if self.pc==0x1234: return
            self.step()
        raise AssertionError(what)
    def check_memory(self):
        """What INIT leaves in RAM: its work area zeroed, everything else as it was."""
        work=SYM['work']; end=work+SYM['work_size']
        assert not any(self.mem[work:end]),'work RAM is not cleared'
        assert self.sub_reg==self.sub_reg_at_entry,'the secondary slot register is not restored'
        assert self.mem[0xc000:work]==self.initial[0xc000:work],'RAM below the work area was modified'
        assert self.mem[end:0xf300]==self.initial[end:0xf300],'RAM between the work area and the BIOS stack was modified'
        assert self.sp==self.entry_sp+2
        assert bytes(self.mem[self.entry_sp:self.entry_sp+16])==self.return_guard
        assert self.probe_before==[self.ram_read(a) for a in PROBES]
        assert self.vdp_gap is None or self.vdp_gap>=29,f'VRAM written {self.vdp_gap} T states after the previous VDP access'
        assert self.reg[1]&3==self.mem[0xf3e0]&3==self.sprite_mode,"the caller's sprite size is not restored"
    def run(self):
        self.until_return(12000000,'ROM did not finish')
        assert len(self.samples)==154,len(self.samples)
        assert self.samples[0][0][:6144]==bytes(6144)
        assert self.samples[46][0][:6144]==screen.OUTLINE
        expected=bytearray(screen.SOLID)
        expected[0xf40:0xfb8]=label(self.kb)
        assert self.vram[:6144]==expected
        assert self.kb_seen==self.kb
        self.check_memory()
        assert self.reg[7]==4
        assert self.vram[0x2000:0x3800]==screen.box_colours()
        # Every blue-wipe sample has exactly the expected top-to-bottom extent.
        for band in range(24):
            assert self.samples[74+band][0][0x2000:0x3800]==screen.box_colours(band+1),f'blue wipe, band {band}'
        assert self.vram[0x1800:0x1b00]==bytes(range(256))*3
        assert self.vram[0x1b00]==208
        assert self.text_screen
        assert self.phase['return_to_basic']-self.text_frame==2*self.hz
        assert all((frame-deadline)&255==0 for frame,deadline in self.deadlines), self.deadlines
        assert self.deadlines[113][0]-self.deadlines[0][0]==int(150*self.hz/20)-int(self.hz/20)
        # The sweep was checked frame by frame in on_halt: every frame of its
        # 3.6 seconds, at every position of the colour rotation.
        assert len(self.work)==36 and self.beam_frames==int(3.6*self.hz) and self.phases_seen=={0,1,2}
        # Each step is on screen within the frame in which its tick began.
        assert max(self.work)<self.frame_cycles,f'a sweep step took {max(self.work)} T states'
        return {'Hz':self.hz,'RAM_KB':self.kb,'RAM_slot':self.where,'mirrored':self.mirrored,'ticks':154,'logo_seconds':7.5,'RAM_display_seconds':2.0,'returns_to_BIOS':True,'deadlines_met':True,'final_pattern_match':True,'stack_balanced':True,'black_rectangle_preserved':True,'top_to_bottom_wipe':True,
            'beam_frames_checked':self.beam_frames,'sweep_step_T_states_max':max(self.work),'sweep_step_frames_max':round(max(self.work)/self.frame_cycles,2),'VDP_access_gap_T_states_min':self.vdp_gap}

def verify(page):
    """All checks for one image. Returns (report, the 60 Hz machine)."""
    load(page)
    results=[]
    # The whole animation at both rates. The 50 Hz machine has 8 KiB of RAM in
    # the slot of the BIOS, and a sprite setting that must come back.
    for hz,kb,ram,sprite_mode in ((50,8,'0',1),(60,64,'3',0)):
        machine=Machine(hz,kb,ram,sprite_mode=sprite_mode,record=hz==60); results.append(machine.run())
    # Re-use completed animation state; run the RAM/return stage for other
    # slot/capacity layouts so the hardware-independent animation isn't repeated.
    # In slot 0 the RAM shares the slot with the BIOS ('0', '0-0': pages 0 and 1
    # are the BIOS) or sits in another subslot ('0-2').
    for kb,ram,mirrored in ((8,'3',False),(16,'3',False),(32,'3',False),(8,'3-2',False),(48,'3-2',False),(64,'3-2',False),
            (8,'3-2',True),(16,'3-2',True),(32,'3-2',True),
            (8,'0',False),(16,'0',False),(32,'0',False),(16,'0-0',False),(32,'0-0',False),
            (8,'0-2',False),(16,'0-2',False),(32,'0-2',False),(48,'0-2',False),(64,'0-2',False),(16,'0-2',True)):
        test=Machine(60,kb,ram,mirrored)
        work=SYM['work']; test.mem[work:work+SYM['work_size']]=bytes(SYM['work_size'])
        test.pc=SYM['ram_stage']; test.mem[SYM['w_rate']]=6
        test.until_return(100000,'RAM stage did not return')
        assert test.kb_seen==kb,f'{kb} KiB in slot {ram}: the ROM says {test.kb_seen}'
        test.check_memory()
        assert test.phase['return_to_basic']-test.text_frame==120
        assert test.vram[0xf40:0xfb8]==label(kb)
        results.append({'RAM_KB':kb,'RAM_slot':ram,'mirrored':mirrored,'probe_restored':True,'returns_to_BIOS':True,'RAM_display_seconds':2.0})
    report={'page':page,'file':build.rom_file(page).name,'size':len(ROM),'header':ROM[:16].hex(' '),'init':f'0x{SYM["init"]:04x}','sha256':hashlib.sha256(ROM).hexdigest(),'results':results}
    return report,machine

def preview(machine):
    from PIL import Image
    flat=[value for colour in PALETTE for value in colour]
    def image(shot):
        im=Image.new('P',(256,192)); im.putpalette(flat); im.putdata(screen.picture(*shot))
        return im.resize((768,576),Image.Resampling.NEAREST)
    # One GIF frame per distinct screen, held for the video frames it was shown.
    # GIF delays are multiples of 10 ms, so each frame ends on the rounded time.
    end=machine.phase['return_to_basic']
    starts=[frame for frame,_ in machine.shots]+[end]
    edges=[round((frame-starts[0])*100/machine.hz)*10 for frame in starts]
    durations=[b-a for a,b in zip(edges,edges[1:])]
    frames=[image(shot) for _,shot in machine.shots]
    frames[0].save(BUILD/'preview.gif',save_all=True,append_images=frames[1:],duration=durations,loop=0,optimize=False)
    ticks=[image(sample) for sample in machine.samples]
    ticks[-1].save(BUILD/'final.png')
    for index in (17,29,46,62,85): ticks[index].save(BUILD/f'frame_{index:03}.png')
    sheet=Image.new('RGB',(768*3,576*2))
    for i,index in enumerate((17,29,46,62,85,113)): sheet.paste(ticks[index].convert('RGB'),((i%3)*768,(i//3)*576))
    sheet.resize((1152,576)).save(BUILD/'storyboard.png')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Run the ROM images in the harness and check them.')
    parser.add_argument('--page',type=int,choices=list(build.PAGES),help='image to check (default: all)')
    args=parser.parse_args()
    pages=[args.page] if args.page else list(build.PAGES)
    reports=[]; machines=[]
    for page in pages:
        report,machine=verify(page); reports.append(report); machines.append(machine)
    # The images differ in where they run, not in what they show.
    assert all(m.samples==machines[0].samples and m.shots==machines[0].shots for m in machines),'the images show different animations'
    report={'method':'Focused instruction-execution harness with BIOS stubs; not full MSX emulation','images':reports}
    (BUILD/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    try: preview(machines[0])
    except ImportError: print('Pillow unavailable: preview skipped')
