"""Regenerate the binary assets that src/logo.asm pulls in with incbin.
Standard library only. Run from any directory.

  python tools/gen_assets.py          write assets/solid.dat, outline.dat, beams.dat
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

def beams():
    # The seed fixes the whole animation. The order of the rng calls is part of
    # it: reordering them produces a different beams.dat.
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
                head=(age-3)*22
                pts=[(x+dx*k,y+dy*k) for k in range(head-66,head+1)]
                pts=[(px,py) for px,py in pts if 0<=px<256 and 0<=py<192]
                states.append((pts[0][0],pts[0][1],dx&255,dy&255,len(pts)) if pts else (0,0,0,0,0))
        tracks.append(states)
    columns=list(range(4,28)); rng.shuffle(columns)
    # 36 records of 16 bytes: the tile column to reveal (255 = none), then one
    # (x, y, dx, dy, length) segment per track.
    script=bytearray()
    for t in range(36):
        script.append(columns[t-t//3] if t%3!=2 else 255)
        for track in tracks: script.extend(track[t*2])
    return bytes(script)

if __name__=='__main__':
    files={**images(),'beams.dat':beams()}
    if sys.argv[1:]==['--check']:
        stale=[name for name,data in files.items() if not (ASSETS/name).is_file() or (ASSETS/name).read_bytes()!=data]
        if stale: sys.exit('Differs from the generator output: '+', '.join(stale))
        print('assets/ matches the generator output')
    elif sys.argv[1:]: sys.exit(__doc__)
    else:
        for name,data in files.items():
            (ASSETS/name).write_bytes(data)
            print(f'Wrote assets/{name} ({len(data)} bytes)')
