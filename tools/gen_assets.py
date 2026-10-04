"""Regenerate the binary assets that src/logo.asm pulls in with incbin.
Standard library only. Run from any directory.

  python tools/gen_assets.py          write assets/solid.dat, outline.dat, beams.dat, sprites.dat
  python tools/gen_assets.py --check  write nothing; exit 1 if a tracked file differs
"""
from pathlib import Path
import random, sys
ASSETS = Path(__file__).resolve().parent.parent / 'assets'

# Original geometric interpretation of the MSX wordmark, not a BIOS ROM extract.
# The vertices are in the 480x424 coordinates of the reference screenshot and
# are scaled to the MSX1 256x192 canvas.
REFERENCE = [(87,226),(114,117),(139,117),(153,165),(165,117),
 (190,117),(210,198),(259,198),(265,193),(263,188),(239,185),
 (221,177),(211,166),(206,151),(208,140),(217,126),(231,117),
 (321,117),(342,144),(361,117),(396,117),(359,170),(400,226),
 (367,226),(342,192),(318,226),(281,226),(324,172),(305,145),
 (240,145),(233,150),(234,154),(239,158),(256,158),(272,163),
 (285,174),(293,188),(294,201),(288,215),(277,224),(268,226),
 (189,226),(178,175),(166,226),(143,226),(129,175),(113,226)]
POLYGONS = [[(x*256/480,y*192/424) for x,y in REFERENCE]]

STEPS = 36  # steps of the laser sweep
BEAM = 67   # pixels in a laser beam
# 16x16 sprite patterns as (x step per line downwards, lines). A beam is not a
# multiple of 16 long, so each direction also has a pattern for its last lines.
SPRITES = [(1,16),(-1,16),(0,16),(1,BEAM%16),(-1,BEAM%16),(0,BEAM%16)]

def inside(x,y,poly):
    hit=False
    for (x1,y1),(x2,y2) in zip(poly,poly[1:]+poly[:1]):
        if (y1>y)!=(y2>y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            hit=not hit
    return hit

def images():
    solid=[[any(inside(x+.5,y+.5,p) for p in POLYGONS) for x in range(256)] for y in range(192)]
    outline=[[solid[y][x] and any(not solid[y+dy][x+dx] for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]) if 0<x<255 and 0<y<191 else False for x in range(256)] for y in range(192)]
    files={}
    for name,pixels in [('solid',solid),('outline',outline)]:
        # SCREEN 2 pattern table order, so the ROM can copy a tile column as is.
        buf=bytearray(6144)
        for y in range(192):
            for x in range(256):
                if pixels[y][x]: buf[(y//8)*256+(x//8)*8+y%8] |= 128>>(x%8)
        files[name+'.dat']=bytes(buf)
    return files

def script():
    """The laser sweep as geometry, before any encoding: one entry per step,
    (tile column whose outline appears or None, three beams). A beam is
    (x, y, dx, dy): BEAM pixels from (x, y) in steps of (dx, dy). It may lie
    partly or wholly off screen."""
    # The seed fixes the whole animation. The order of the rng calls is part of
    # it: reordering them produces a different sweep.
    rng=random.Random(9918)
    directions=[(1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,-1),(1,-1),(-1,1)]
    tracks=[]
    for track in range(3):
        states=[]
        for group in range(9):
            dx,dy=rng.choice(directions)
            x=rng.randint(20,235); y=rng.randint(20,171)
            # A long beam travels along its own direction for eight ticks.
            for age in range(8):
                tail=(age-3)*22-(BEAM-1)
                states.append((x+dx*tail,y+dy*tail,dx,dy))
        tracks.append(states)
    columns=list(range(4,28)); rng.shuffle(columns)
    return [(columns[t-t//3] if t%3!=2 else None,[track[t*2] for track in tracks]) for t in range(STEPS)]

def pixels(beam):
    """The pixels of a beam that are on screen."""
    x,y,dx,dy=beam
    return [(x+dx*k,y+dy*k) for k in range(BEAM) if 0<=x+dx*k<256 and 0<=y+dy*k<192]

def sprite_column(step,line):
    """Column of the pixel that a sprite pattern has on the given line."""
    return {1:line,-1:15-line,0:0}[step]

def sprite_patterns():
    data=bytearray()
    for step,lines in SPRITES:
        rows=[0x8000>>sprite_column(step,line) if line<lines else 0 for line in range(16)]
        # A 16x16 pattern is its left half top to bottom, then its right half.
        data+=bytes(row>>8 for row in rows)+bytes(row&255 for row in rows)
    return bytes(data)

def beam_sprites(beam,track):
    """Sprite attributes (Y, X, pattern name) that show a vertical or diagonal
    beam. The name byte also has the early clock in bit 7 and the number of
    the beam in its low two bits, which the VDP ignores for 16x16 sprites."""
    x,y,dx,dy=beam
    # Follow the beam downwards. Sprites stacked 16 lines apart never share a
    # scanline, so a beam takes one of the four sprites a scanline can show.
    if dy<0: x,y,dx=x+dx*(BEAM-1),y-(BEAM-1),-dx
    attributes=[]
    for part in range(0,BEAM,16):
        lines=min(16,BEAM-part)
        left=x+dx*part-(15 if dx<0 else 0); top=y+part
        if not any(0<=left+sprite_column(dx,line)<256 and 0<=top+line<192 for line in range(lines)): continue
        early=left<0  # the early clock shows a sprite 32 pixels to the left of X
        attribute=((top-1)&255,left+32 if early else left,SPRITES.index((dx,lines))*4|track|(0x80 if early else 0))
        assert attribute[0]!=208 and 0<=attribute[1]<256,attribute  # Y=208 would end the sprite list
        attributes.append(attribute)
    return attributes

def beam_run(beam,track):
    """A horizontal beam as bytes of the pattern table: (number of the beam;
    offset of the first byte, low and high; number of bytes, 8 apart; mask of
    the first byte; mask of the last byte). None if it is off screen."""
    shown=pixels(beam)
    if not shown: return None
    y=shown[0][1]; low=min(x for x,_ in shown); high=max(x for x,_ in shown)
    first=0xFF>>(low%8); last=(0xFF<<(7-high%8))&0xFF
    count=high//8-low//8+1
    # The ROM masks the last byte with both, also when it is the first byte.
    if count==1: first=last=first&last
    offset=(y//8)*256+(low//8)*8+y%8
    return (track,offset&255,offset>>8,count,first,last)

def beams():
    """One record per step: the number of sprites and three bytes for each,
    the tile column to reveal (255 = none), the number of horizontal runs and
    six bytes for each. The three beams of a step are numbered 0-2; the ROM
    colours each by its number."""
    data=bytearray()
    for reveal,step in script():
        sprites=[attribute for track,beam in enumerate(step) if beam[3] for attribute in beam_sprites(beam,track)]
        runs=[run for run in (beam_run(beam,track) for track,beam in enumerate(step) if not beam[3]) if run]
        # The VDP shows four sprites on a scanline and counts all 16 lines of each.
        tops=[(y+1)&255 for y,_,_ in sprites]
        assert all(sum((line-top)&255<16 for top in tops)<=4 for line in range(192)),'more than four sprites on a scanline'
        # The ROM does not combine runs, so two of them must not share a byte.
        cells=[(low|high<<8)+8*i for _,low,high,count,_,_ in runs for i in range(count)]
        assert len(cells)==len(set(cells)),'two runs share a byte'
        data.append(len(sprites))
        for attribute in sprites: data.extend(attribute)
        data.append(255 if reveal is None else reveal)
        data.append(len(runs))
        for run in runs: data.extend(run)
    return bytes(data)

if __name__=='__main__':
    files={**images(),'beams.dat':beams(),'sprites.dat':sprite_patterns()}
    if sys.argv[1:]==['--check']:
        stale=[name for name,data in files.items() if not (ASSETS/name).is_file() or (ASSETS/name).read_bytes()!=data]
        if stale: sys.exit('Differs from the generator output: '+', '.join(stale))
        print('assets/ matches the generator output')
    elif sys.argv[1:]: sys.exit(__doc__)
    else:
        for name,data in files.items():
            (ASSETS/name).write_bytes(data)
            print(f'Wrote assets/{name} ({len(data)} bytes)')
