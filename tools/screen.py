"""What the laser sweep must put on the screen, and how a TMS9918A shows it.
Shared by tools/verify.py and tools/test_openmsx.py. Standard library only.

The expectation is built from the beam geometry of tools/gen_assets.py and
not from the bytes of assets/beams.dat, so a comparison also covers the
encoding of that file and the ROM code that decodes it.
"""
from functools import lru_cache
from pathlib import Path
import gen_assets
ASSETS=Path(__file__).resolve().parent.parent/'assets'
COLOURS=(15,11,7)   # white, light yellow, cyan
COLOUR_FRAMES=2     # frames a beam keeps one colour
BEAMS=3             # beams in a step; each is one colour ahead of the one before
BOX=(32,44,224,112) # the black box around the logo: left, top, right+1, bottom+1
SCRIPT=gen_assets.script()
OUTLINE=(ASSETS/'outline.dat').read_bytes()
SOLID=(ASSETS/'solid.dat').read_bytes()

def phase_at(frames):
    """Position in the colour rotation that many frames after the sweep began."""
    return frames//COLOUR_FRAMES%len(COLOURS)

def beam_colour(phase,beam):
    """Colour of a beam of a step: no two of the three are the same."""
    return COLOURS[(phase+beam)%len(COLOURS)]

@lru_cache(maxsize=None)
def expected(step,phase):
    """The screen while a step of the sweep is shown at a position of the
    colour rotation: (pattern table, colour table, sprite pixels as
    {(x, y): colour})."""
    revealed={SCRIPT[t][0] for t in range(step+1)}
    pattern=bytearray(OUTLINE[i] if i>>3&31 in revealed else 0 for i in range(6144))
    colours=bytearray([0xF1])*6144
    sprites={}
    for number,beam in enumerate(SCRIPT[step][1]):
        colour=beam_colour(phase,number)
        for x,y in gen_assets.pixels(beam):
            # Where two sprite beams cross, the one with the lower number is in front.
            if beam[3]: sprites.setdefault((x,y),colour)
            else:
                # A horizontal beam is in the pattern table. Its colour is that
                # of the whole byte, outline pixels of that byte included.
                i=(y//8)*256+(x//8)*8+y%8
                pattern[i]|=128>>(x%8); colours[i]=colour<<4|1
    return bytes(pattern),bytes(colours),sprites

def box_colours(bands=24):
    """Colour table once that many 8-line bands, from the top, have turned blue."""
    left,top,right,bottom=BOX
    return bytes(0xF4 if y<bands*8 and not (left<=x<right and top<=y<bottom) else 0xF1
        for row in range(24) for x in range(0,256,8) for y in range(row*8,row*8+8))

def sprites_shown(vram,r1):
    """Sprite pixels {(x, y): colour} of the attribute table at 1B00h and the
    patterns at 3800h. Fails where the VDP would not show them as listed."""
    assert r1&3==2,f'sprites are not 16x16 unmagnified: R1={r1:02X}h'
    pixels={}; on_line=[0]*192
    for n in range(32):
        y,x,name,colour=vram[0x1B00+n*4:0x1B04+n*4]
        if y==208: break
        top=y+1 if y<208 else y-255
        if colour&0x80: x-=32
        for line in range(16):
            at=0x3800+(name&0xFC)*8+line
            bits=vram[at]<<8|vram[at+16]
            if not 0<=top+line<192: continue
            on_line[top+line]+=1
            for column in range(16):
                if bits&0x8000>>column and 0<=x+column<256 and colour&15:
                    # A lower sprite number is in front.
                    pixels.setdefault((x+column,top+line),colour&15)
    assert max(on_line)<=4,'more than four sprites on a scanline'
    return pixels

def picture(vram,r7,r1):
    """The 256x192 screen as a flat list of colour codes 1..15."""
    backdrop=r7&15 or 1
    sprites=sprites_shown(vram,r1) if vram[0x1B00]!=208 else {}
    codes=[]
    for y in range(192):
        for x in range(256):
            i=(y//8)*256+(x//8)*8+y%8
            colour=vram[0x2000+i]
            colour=colour>>4 if vram[i]&128>>(x%8) else colour&15
            codes.append(sprites.get((x,y)) or colour or backdrop)
    return codes
