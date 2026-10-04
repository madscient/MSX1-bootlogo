; MSX1 16 KiB cartridge. Built by tools/build.py.
; The incbin paths are relative to the repository root, the one form that
; zmac, sjasmplus and Pasmo all resolve without options when run from there.
; Keep to the syntax those three share (0x literals, db/dw/ds, incbin).
; BIOS remains mapped in page 0; BIOS-provided RAM/stack in page 3.
; Work RAM is kept far below the BIOS/BASIC stack (around F0xxh or lower):
; C000-D7FF = displayed SCREEN 2 pattern image (including laser beams).
; D800-D847 = variables/probe backup, D880-D89F = revealed-column flags.
; Nothing at D8A0h or above is written, so the caller's stack is safe.
; The persistent image is not copied to RAM: a beam is erased from the
; outline in ROM when its column is already revealed, otherwise with 0.
; D820/D821=mirror marker, D822=own slot ID, D825=mirrored flag.
; D826/D827=system font address.
; D806=next beam record, D808=old beam record, D803=draw/erase.
; Beam records: reveal column + three (x,y,dx,dy,length) segments.
    org 0x4000
    db 0x41,0x42
    dw init,0,0,0
    ds 6,0
init:
    di
    ; Keep the BIOS caller stack: INIT must return to the ROM scanner.
    im 1
    ; --- Guard against a second INIT call through a page-2 mirror. ---
    ; A plain 16 KiB ROM is commonly mirrored at 8000h (emulators, simple
    ; cartridges). The BIOS scanner then finds "AB" again at 8000h and calls
    ; INIT a second time. Detect the mirror with RDSLT on our own slot and
    ; skip the second call. D820/D821=marker, D822=own slot, D825=mirrored.
    call 0x0138 ; RSLREG
    srl a
    srl a
    and 3 ; primary slot selected in page 1 = this cartridge
    ld e,a
    ld d,0
    ld hl,0xFCC1 ; EXPTBL
    add hl,de
    ld a,(hl)
    and 0x80
    or e
    ld c,a
    and 0x80
    jp z,own_slot_ready
    ld hl,0xFCC5 ; SLTTBL: page-1 secondary slot is already in bits 3-2
    add hl,de
    ld a,(hl)
    and 12
    or c
    ld c,a
own_slot_ready:
    ld a,c
    ld (0xD822),a
    ld hl,0x8000
    call 0x000C ; RDSLT
    cp 0x41
    jp nz,not_mirrored
    ld a,(0xD822)
    ld hl,0x8001
    call 0x000C
    cp 0x42
    jp nz,not_mirrored
    ld a,(0xD820)
    cp 0x5A
    jp nz,first_call
    ld a,(0xD821)
    cp 0xA5
    jp nz,first_call
    ; Second call via the mirror: consume the marker and return at once.
    xor a
    ld (0xD820),a
    ld (0xD821),a
    ret
first_call:
    ld a,1
    jp mirror_known
not_mirrored:
    xor a
mirror_known:
    ld (0xD825),a
    ld a,15
    ld (0xF3E9),a ; FORCLR
    xor a
    ld (0xF3EA),a ; BAKCLR, transparent = backdrop
    ld (0xF3EB),a ; BDRCLR
    ld a,2
    call 0x005F ; CHGMOD: SCREEN 2, standard name/pattern tables
    call 0x0041 ; DISSCR
    ld hl,0xC000
    ld de,0xC001
    ld bc,6143
    ld (hl),0
    ldir
    ld hl,0xD880 ; revealed-column flags
    ld de,0xD881
    ld bc,31
    ld (hl),0
    ldir
    ld hl,0
    ld bc,6144
    xor a
    call 0x0056 ; FILVRM: clear patterns
    ld hl,0x2000
    ld bc,6144
    ld a,0xF1 ; white ink, fixed black paper
    call 0x0056
    ld hl,0x1B00
    ld a,208 ; sprite terminator
    call 0x004D
    ld a,(0x002B)
    and 0x80
    ld a,6
    jp z,rate_ready
    ld a,5
rate_ready:
    ld (0xD804),a
    xor a
    ld (0xD80A),a ; half-frame accumulator for PAL 3/2-frame ticks
    ld (0xD80B),a ; 0=50ms, 1=100ms laser cadence
    call 0x0044 ; ENASCR
    ei
    ld a,(0xFC9E)
    ld (0xD805),a
    ld b,10 ; 0.5 second black
    call delay
    ld hl,beams
    ld (0xD806),hl
    ld a,1
    ld (0xD80B),a
    xor a
    ld (0xD800),a
sweep:
    ld hl,(0xD806)
    ld (0xD808),hl
    ld a,(hl)
    inc hl
    ld (0xD806),hl
    cp 255
    jp z,no_reveal
    ld hl,outline
    call merge_column
no_reveal:
    ld a,1
    ld (0xD803),a
    call three_beams
    call tick
    ; Restore exactly these beams, preserving the accumulated outline.
    ld hl,(0xD808)
    inc hl
    ld (0xD806),hl
    xor a
    ld (0xD803),a
    call three_beams
    ld a,(0xD800)
    inc a
    ld (0xD800),a
    cp 36
    jp c,sweep
    xor a
    ld (0xD80B),a
    ld b,4
    call delay
    ld a,4
    ld (0xD800),a
fill:
    ld a,(0xD800)
    ld hl,solid
    call merge_column
    call tick
    ld a,(0xD800)
    inc a
    ld (0xD800),a
    cp 28
    jp c,fill
    xor a
    ld (0xD800),a
blue:
    ; One 8-scanline band per tick, proceeding top to bottom.
    ld a,(0xD800)
    add a,0x20
    ld h,a
    ld l,0
    ld b,0
blue_byte:
    ld a,h
    cp 0x26
    jp c,outside
    cp 0x2E
    jp nc,outside
    ld a,l
    cp 32
    jp c,outside
    cp 224
    jp nc,outside
    ld a,0xF1
    jp color_write
outside:
    ld a,0xF4
color_write:
    call write_byte
    inc hl
    djnz blue_byte
    call tick
    ld a,(0xD800)
    inc a
    ld (0xD800),a
    cp 24
    jp c,blue
    ld bc,0x0407
    call 0x0047
    ld b,16
    call delay
ram_stage:
    call count_ram
    ; Pick one of four strings with the exact text Main RAM : ##KB.
    ld a,(0xD824)
    srl a
    srl a
    srl a
    sub 2
    ld e,a
    ld d,0
    ld hl,ram_labels
    add hl,de
    ld e,(hl)
    inc hl
    ld d,(hl)
    ex de,hl
    ld de,0x0F40 ; 15 characters from x=64, y=120, below the logo
    call draw_text
ram_text_visible:
    ; Start a fresh two-second deadline AFTER measuring and drawing.
    ld a,(0xFC9E)
    ld (0xD805),a
    xor a
    ld (0xD80A),a
    ld b,40
    call delay
return_to_basic:
    ld a,15
    ld (0xF3E9),a
    ld a,4
    ld (0xF3EA),a
    ld (0xF3EB),a
    ld a,1
    call 0x005F ; restore a text screen before normal BIOS boot continues
init_return:
    ; Leave no data behind: clear the whole work RAM (C000-D89F) so the
    ; BASIC free area is all zero again, exactly as before INIT.
    ld a,(0xD825)
    push af
    ld hl,0xC000
    ld de,0xC001
    ld bc,0x189F
    ld (hl),0
    ldir
    pop af
    ; Only when mirrored: leave the marker so the mirror call is skipped.
    or a
    ret z
    ld a,0x5A
    ld (0xD820),a
    ld a,0xA5
    ld (0xD821),a
    ret

; Fixed deadline scheduling: drawing time is included in each 50 ms tick.
; D804=6 (NTSC) or 5 (PAL), divided by two with a carried remainder.
; The difference remains below 128 frames, including across JIFFY wrap.
tick:
    push af
    push bc
    ld a,(0xD804)
    ld b,a
    ld a,(0xD80B)
    or a
    jp nz,tick_interval_ready
    ld a,(0xD80A)
    add a,b
    ld b,a
    and 1
    ld (0xD80A),a
    ld a,b
    srl a
    ld b,a
tick_interval_ready:
    ld a,(0xD805)
    add a,b
    ld (0xD805),a
    ld b,a
tick_wait:
    ld a,(0xFC9E)
    sub b
    jp p,tick_done
    halt
    jp tick_wait
tick_done:
    pop bc
    pop af
    ret
delay:
    call tick
    djnz delay
    ret

; A=tile column, HL=full 6144-byte SCREEN 2 image in ROM.
; Copy 8 bytes for this column from each of 24 tile rows to RAM image+VRAM.
merge_column:
    push hl
    push af
    ld e,a
    ld d,0
    ld hl,0xD880
    add hl,de
    ld (hl),1 ; this column now shows its ROM image
    pop af
    call col_address
    ex de,hl
    pop hl
    add hl,de
    ld a,d
    add a,0xC0
    ld d,a
    ld c,24
merge_block:
    ld b,8
merge_byte:
    ld a,(hl)
    ld (de),a
    push hl
    push de
    ex de,hl
    ld a,h
    sub 0xC0
    ld h,a
    ld a,(de)
    call write_byte
    pop de
    pop hl
    inc hl
    inc de
    djnz merge_byte
    push bc
    ld bc,248
    add hl,bc
    ex de,hl
    add hl,bc
    ex de,hl
    pop bc
    dec c
    jp nz,merge_block
    ret

; A=column 0..31 -> HL=pattern offset.
col_address:
    ld l,a
    ld h,0
    add hl,hl
    add hl,hl
    add hl,hl
    ret

 ; Draw or erase three moving laser segments from the script.
three_beams:
    call segment
    call segment
    call segment
    ret
segment:
    ld hl,(0xD806)
    ld de,0xD810
    ld bc,5
    ldir
    ld (0xD806),hl
    ld a,(0xD814)
    or a
    ret z
    ld b,a
segment_pixel:
    push bc
    call pixel
    ld a,(0xD812)
    ld c,a
    ld a,(0xD810)
    add a,c
    ld (0xD810),a
    ld a,(0xD813)
    ld c,a
    ld a,(0xD811)
    add a,c
    ld (0xD811),a
    pop bc
    djnz segment_pixel
    ret
pixel:
    ld a,(0xD810)
    and 7
    ld e,a
    ld d,0
    ld hl,masks
    add hl,de
    ld c,(hl)
    ld a,(0xD811)
    ld b,a
    and 7
    ld e,a
    ld a,(0xD810)
    and 248
    or e
    ld l,a
    ld a,b
    srl a
    srl a
    srl a
    ld h,a
    push hl
    ld a,(0xD803)
    or a
    jp z,erase_pixel
    ld a,h
    add a,0xC0
    ld h,a
    ld a,(hl)
    or c
    ld (hl),a
    jp pixel_write
erase_pixel:
    ; Restore the byte: outline from ROM if the column is revealed, else 0.
    ld a,l
    srl a
    srl a
    srl a
    ld e,a
    ld d,0
    push hl
    ld hl,0xD880
    add hl,de
    ld a,(hl)
    pop hl
    or a
    jp z,erase_store
    push hl
    ld de,outline
    add hl,de
    ld a,(hl)
    pop hl
erase_store:
    ld c,a
    ld a,h
    add a,0xC0
    ld h,a
    ld (hl),c
    ld a,c
pixel_write:
    pop hl
    call write_byte
    ret
masks:
    db 128,64,32,16,8,4,2,1
beams:
    incbin "assets/beams.dat"

; A=data, HL=VRAM address. Preserve all registers and the caller's A.
; Interrupts disabled across both address bytes AND data to prevent a BIOS
; status read from resetting the VDP latch. Access spacing exceeds 8 us.
write_byte:
    di
    push af
    ld a,l
    out (0x99),a
    ld a,h
    or 0x40
    out (0x99),a
    pop af
    push af
    pop af
    out (0x98),a
    ei
    ret

; Identify the physical RAM slot that is already selected in page 3.
; Do NOT use RAMAD0..3: those are not guaranteed on diskless MSX1.
; Pages 0-2 are probed with RDSLT/WRSLT; page 3 (stack, work area) is
; accessed directly so its slot selection is never touched.
; RDSLT/WRSLT preserve mappings, but may destroy AF/BC/DE.
; Probe 4 x 16KB in this slot, using distinct tags in TWO passes.
; Mirrored pages therefore count once; ROM/open bus cannot pass both tags.
; Original probe bytes are captured first and all restored before EI.
count_ram:
    di
    call 0x0138
    srl a
    srl a
    srl a
    srl a
    srl a
    srl a
    ld e,a
    ld d,0
    ld hl,0xFCC1 ; EXPTBL
    add hl,de
    ld a,(hl)
    and 0x80
    or e
    ld (0xD823),a
    and 0x80
    jp z,ram_slot_ready
    ; Page 3 is this very slot, so FFFFh reads back its secondary slot
    ; register (inverted). Never trust the SLTTBL shadow here: a wrong
    ; subslot makes RDSLT/WRSLT switch page 3 away from the stack.
    ld a,(0xFFFF)
    ld b,a
    ld a,255
    sub b
    srl a
    srl a
    srl a
    srl a
    and 12
    ld c,a
    ld a,(0xD823)
    or c
    ld (0xD823),a
ram_slot_ready:
    ld hl,0x0100
    ld a,(0xD823)
    call 0x000C
    ld (0xD840),a
    ld hl,0x4100
    ld a,(0xD823)
    call 0x000C
    ld (0xD841),a
    ld hl,0x8100
    ld a,(0xD823)
    call 0x000C
    ld (0xD842),a
    ld a,(0xC100) ; page 3: direct access
    ld (0xD843),a
    ld hl,0x0100
    ld e,81
    ld a,(0xD823)
    call 0x0014
    ld hl,0x4100
    ld e,98
    ld a,(0xD823)
    call 0x0014
    ld hl,0x8100
    ld e,115
    ld a,(0xD823)
    call 0x0014
    ld a,132
    ld (0xC100),a ; page 3: direct access
    ld hl,0x0100
    ld a,(0xD823)
    call 0x000C
    cp 81
    ld a,0
    jp nz,probe_0_0
    ld a,16
probe_0_0:
    ld (0xD844),a
    ld hl,0x4100
    ld a,(0xD823)
    call 0x000C
    cp 98
    ld a,0
    jp nz,probe_0_1
    ld a,16
probe_0_1:
    ld (0xD845),a
    ld hl,0x8100
    ld a,(0xD823)
    call 0x000C
    cp 115
    ld a,0
    jp nz,probe_0_2
    ld a,16
probe_0_2:
    ld (0xD846),a
    ld a,(0xC100) ; page 3: direct access
    cp 132
    ld a,0
    jp nz,probe_0_3
    ld a,16
probe_0_3:
    ld (0xD847),a
    ld hl,0x0100
    ld e,174
    ld a,(0xD823)
    call 0x0014
    ld hl,0x4100
    ld e,157
    ld a,(0xD823)
    call 0x0014
    ld hl,0x8100
    ld e,140
    ld a,(0xD823)
    call 0x0014
    ld a,123
    ld (0xC100),a ; page 3: direct access
    ld hl,0x0100
    ld a,(0xD823)
    call 0x000C
    cp 174
    jp z,probe_1_0
    xor a
    ld (0xD844),a
probe_1_0:
    ld hl,0x4100
    ld a,(0xD823)
    call 0x000C
    cp 157
    jp z,probe_1_1
    xor a
    ld (0xD845),a
probe_1_1:
    ld hl,0x8100
    ld a,(0xD823)
    call 0x000C
    cp 140
    jp z,probe_1_2
    xor a
    ld (0xD846),a
probe_1_2:
    ld a,(0xC100) ; page 3: direct access
    cp 123
    jp z,probe_1_3
    xor a
    ld (0xD847),a
probe_1_3:
    ld a,(0xD840)
    ld e,a
    ld hl,0x0100
    ld a,(0xD823)
    call 0x0014
    ld a,(0xD841)
    ld e,a
    ld hl,0x4100
    ld a,(0xD823)
    call 0x0014
    ld a,(0xD842)
    ld e,a
    ld hl,0x8100
    ld a,(0xD823)
    call 0x0014
    ld a,(0xD843)
    ld (0xC100),a
    ld c,0
    ld a,(0xD844)
    add a,c
    ld c,a
    ld a,(0xD845)
    add a,c
    ld c,a
    ld a,(0xD846)
    add a,c
    ld c,a
    ld a,(0xD847)
    add a,c
    ld c,a
    ld (0xD824),a
    ei
    ret

; HL=zero-terminated string, DE=VRAM pattern address of the first character.
; Each character's 8x8 pattern is copied from the system font into the
; SCREEN 2 pattern table. The font is the one the BIOS uses (CGPNT) when it
; lives in the main ROM, otherwise the main ROM font (CGTABL at 0004h).
draw_text:
    push hl
    ld hl,(0x0004) ; CGTABL
    ld a,(0xF91F) ; CGPNT slot
    ld b,a
    ld a,(0xFCC1) ; EXPTBL: main ROM slot
    sub b
    jp nz,font_ready
    ld hl,(0xF920) ; CGPNT address
font_ready:
    ld (0xD826),hl
    pop hl
text_loop:
    ld a,(hl)
    or a
    ret z
    push hl
    push de
    ld l,a
    ld h,0
    add hl,hl
    add hl,hl
    add hl,hl
    ex de,hl
    ld hl,(0xD826)
    add hl,de
    pop de
    push de
    ld bc,8
    call 0x005C ; LDIRVM
    pop hl
    ld bc,8
    add hl,bc
    ex de,hl
    pop hl
    inc hl
    jp text_loop
ram_labels:
    dw text16,text32,text48,text64
text16:
    db "Main RAM : 16KB",0
text32:
    db "Main RAM : 32KB",0
text48:
    db "Main RAM : 48KB",0
text64:
    db "Main RAM : 64KB",0

outline:
    incbin "assets/outline.dat"
solid:
    incbin "assets/solid.dat"
; No fill up to 8000h here: zmac rejects a ds fill value above 127, so
; tools/build.py pads the image with FFh to 16 KiB.
rom_end:
