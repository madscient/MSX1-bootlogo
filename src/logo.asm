; MSX1 16 KiB cartridge. This is the whole ROM: src/page1.asm and
; src/page2.asm set rom_base, the address it runs at, and include it.
; Built by tools/build.py.
; The include and incbin paths are relative to the repository root, the one
; form that zmac, sjasmplus and Pasmo all resolve without options when run
; from there. Keep to the syntax those three share (0x literals, equ,
; db/dw/ds, include, incbin).
; BIOS remains mapped in page 0; BIOS-provided RAM/stack in page 3.
; Work RAM is work..work+work_size-1 and nothing else: the bottom of the
; 8 KiB that every MSX has, far below the BIOS/BASIC stack (around F0xxh).
; The screen is not mirrored in RAM. What lies under a horizontal beam is
; rebuilt from the outline in ROM and the revealed-column flags; vertical
; and diagonal beams are sprites and leave the pattern table alone.
; Beam records: sprite count, (Y, X, name) per sprite, reveal column, run
; count, (beam, offset low, offset high, bytes, first mask, last mask) per
; run. The three beams of a step are numbered 0-2 and each has its own colour.

color_frames equ 2 ; frames a beam keeps one colour

work equ 0xE000
work_size equ 0x60
w_stage equ work+0 ; step counter of the running stage
w_color equ work+1 ; index into colors
w_color_due equ work+2 ; JIFFY low byte at which the colour moves on
w_ink equ work+3 ; colour code of the run in progress
w_rate equ work+4 ; frames per 100 ms: 6 at 60 Hz, 5 at 50 Hz
w_due equ work+5 ; JIFFY low byte at which the running tick ends
w_next equ work+6 ; (2 bytes) next beam record
w_shown equ work+8 ; (2 bytes) beam record on screen
w_half equ work+10 ; half frame carried between 50 ms ticks at 50 Hz
w_laser equ work+11 ; 1 during the sweep: 100 ms ticks and colour rotation
w_runs equ work+12 ; (2 bytes) run list of the record on screen
w_sprmode equ work+14 ; sprite size and magnification bits of R1 at entry
w_runmode equ work+15 ; what runs does: 0 erase, 1 draw, 2 recolour
w_lastmask equ work+16 ; mask of the last byte of the run in progress
w_ram_slot equ work+17 ; slot ID of the RAM in page 3
w_ram_kb equ work+18 ; RAM found, in KiB
w_font equ work+19 ; (2 bytes) system font
w_modes equ work+21 ; (3 bytes) how pages 0-2 of the RAM slot are reached
w_sub_bios equ work+24 ; secondary slot register of the RAM's primary slot
w_sub_ram equ work+25 ; the same with the RAM's subslot in page 0 as well
w_inks equ work+26 ; (3 bytes) colour code of beam 0, 1 and 2
w_saved equ work+0x20 ; (5 bytes) what was at the probe addresses
w_found equ work+0x28 ; (5 bytes) KiB found at each probe address
w_revealed equ work+0x40 ; (32 bytes) per tile column: 1 once its outline is on screen

    org rom_base
    db 0x41,0x42
    dw init,0,0,0
    ds 6,0
init:
    di
    ; Keep the BIOS caller stack: INIT must return to the ROM scanner.
    im 1
    ld a,15
    ld (0xF3E9),a ; FORCLR
    xor a
    ld (0xF3EA),a ; BAKCLR, transparent = backdrop
    ld (0xF3EB),a ; BDRCLR
    ld a,2
    call 0x005F ; CHGMOD: SCREEN 2, standard name/pattern tables
    call 0x0041 ; DISSCR
    ld hl,w_revealed
    ld de,w_revealed+1
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
    ; Sprites are 16x16, not magnified. A mode change keeps these two bits,
    ; so the caller's setting is saved here and put back on exit.
    ld a,(0xF3E0) ; RG1SAV
    ld b,a
    and 3
    ld (w_sprmode),a
    ld a,b
    and 0xFC
    or 2
    ld b,a
    ld c,1
    call 0x0047 ; WRTVDP
    ld hl,sprites
    ld de,0x3800
    ld bc,192
    call 0x005C ; LDIRVM: sprite patterns
    ld a,(0x002B)
    and 0x80
    ld a,6
    jp z,rate_ready
    ld a,5
rate_ready:
    ld (w_rate),a
    xor a
    ld (w_half),a
    ld (w_laser),a
    call 0x0044 ; ENASCR
    ei
    ld a,(0xFC9E)
    ld (w_due),a
    ld b,10 ; 0.5 second black
    call delay
    ld hl,beams
    ld (w_next),hl
    ; The colour clock starts with the sweep and runs on the tick grid.
    xor a
    ld (w_stage),a
    call color_set
    ld a,(w_due)
    add a,color_frames
    ld (w_color_due),a
    ld a,1
    ld (w_laser),a
sweep:
    call color_step
    ld hl,(w_next)
    ld (w_shown),hl
    call put_sprites
    ld a,(hl)
    inc hl
    ld (w_runs),hl
    cp 255
    jp z,no_reveal
    ld hl,outline
    call merge_column
no_reveal:
    ld hl,(w_runs)
    ld a,1
    call runs
    ld (w_next),hl
    call tick
    ld hl,(w_runs)
    xor a
    call runs
    ld a,(w_stage)
    inc a
    ld (w_stage),a
    cp 36
    jp c,sweep
beams_off:
    xor a
    ld (w_laser),a
    ld hl,0x1B00
    ld a,208
    call write_byte
    ld b,4
    call delay
    ld a,4
    ld (w_stage),a
fill:
    ld a,(w_stage)
    ld hl,solid
    call merge_column
    call tick
    ld a,(w_stage)
    inc a
    ld (w_stage),a
    cp 28
    jp c,fill
    xor a
    ld (w_stage),a
blue:
    ; One 8-scanline band per tick, proceeding top to bottom.
    ld a,(w_stage)
    add a,0x20
    ld h,a
    ld l,0
    ld b,0
blue_byte:
    ; The black box is tile columns 4-27 of bands 6-13 and of the lower four
    ; lines of band 5: x=32-223, y=44-111.
    ld a,l
    cp 32
    jp c,outside
    cp 224
    jp nc,outside
    ld a,h
    cp 0x25
    jp c,outside
    cp 0x2E
    jp nc,outside
    cp 0x25
    jp nz,inside
    ld a,l
    and 7
    cp 4
    jp c,outside
inside:
    ld a,0xF1
    jp color_write
outside:
    ld a,0xF4
color_write:
    call write_byte
    inc hl
    djnz blue_byte
    call tick
    ld a,(w_stage)
    inc a
    ld (w_stage),a
    cp 24
    jp c,blue
    ld bc,0x0407
    call 0x0047
    ld b,16
    call delay
ram_stage:
    call count_ram
    ; Pick the string with the exact text Main RAM : ##KB.
    ; KiB/8 is 1 for 8 KiB, then 2 per 16 KiB: an offset into the word table.
    ld a,(w_ram_kb)
    srl a
    srl a
    srl a
    and 0xFE
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
    ld (w_due),a
    xor a
    ld (w_half),a
    ld b,40
    call delay
return_to_basic:
    ld a,15
    ld (0xF3E9),a
    ld a,4
    ld (0xF3EA),a
    ld (0xF3EB),a
    ld a,(0xF3E0) ; RG1SAV
    and 0xFC
    ld b,a
    ld a,(w_sprmode)
    or b
    ld b,a
    ld c,1
    call 0x0047 ; WRTVDP: the caller's sprite size
    ld a,1
    call 0x005F ; restore a text screen before normal BIOS boot continues
init_return:
    ; Leave no data behind: clear the whole work RAM so the BASIC free area
    ; is all zero again.
    ld hl,work
    ld de,work+1
    ld bc,work_size-1
    ld (hl),0
    ldir
    ret

; Fixed deadline scheduling: drawing time is included in each 50 ms tick.
; w_rate=6 (NTSC) or 5 (PAL), divided by two with a carried remainder.
; The difference remains below 128 frames, including across JIFFY wrap.
; During the sweep the wait also rotates the beam colour.
; Preserves all registers.
tick:
    push af
    push bc
    ld a,(w_rate)
    ld b,a
    ld a,(w_laser)
    or a
    jp nz,tick_interval_ready
    ld a,(w_half)
    add a,b
    ld b,a
    and 1
    ld (w_half),a
    ld a,b
    srl a
    ld b,a
tick_interval_ready:
    ld a,(w_due)
    add a,b
    ld (w_due),a
    ld b,a
tick_wait:
    ld a,(0xFC9E)
    sub b
    jp p,tick_done
    ld a,(w_laser)
    or a
    jp z,tick_halt
    push bc
    push de
    push hl
    call color_step
    jp z,tick_colored
    ; Recolour right after the frame interrupt, before the display starts.
    ld hl,(w_shown)
    call put_sprites
    ld hl,(w_runs)
    ld a,2
    call runs
tick_colored:
    pop hl
    pop de
    pop bc
tick_halt:
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

; Move the beam colour on once its deadline has passed. The deadline advances
; by a fixed step, so the colour follows the frame count and not the calls.
; Returns Z if the colour is unchanged, NZ if it changed. Destroys A,B,DE,HL.
color_step:
    ld a,(w_color_due)
    ld b,a
    ld a,(0xFC9E)
    sub b
    jp p,color_next
    xor a
    ret
color_next:
    ld a,b
    add a,color_frames
    ld (w_color_due),a
    ld a,(w_color)
    inc a
    cp 3
    jp c,color_set
    xor a
; A=index into colors. Returns NZ. Destroys A,BC,DE,HL.
color_set:
    ld (w_color),a
    ld e,a
    ld d,0
    ld hl,colors
    add hl,de
    ld de,w_inks
    ld bc,3
    ldir
    or 1 ; NZ
    ret
colors:
    ; White, light yellow, cyan. Beam 0 takes the indexed colour and beams 1
    ; and 2 the next two, so the three never share one; hence the repeat.
    db 15,11,7,15,11

; A=byte with the number of a beam in its low two bits -> A=the colour of
; that beam. Preserves BC,DE,HL.
beam_ink:
    push hl
    push de
    and 3
    ld e,a
    ld d,0
    ld hl,w_inks
    add hl,de
    ld a,(hl)
    pop de
    pop hl
    ret

; A=tile column, HL=full 6144-byte SCREEN 2 image in ROM.
; Copy 8 bytes for this column from each of 24 tile rows to VRAM.
; Destroys A,BC,DE,HL.
merge_column:
    push hl
    push af
    ld e,a
    ld d,0
    ld hl,w_revealed
    add hl,de
    ld (hl),1 ; this column now shows its ROM image
    pop af
    call col_address
    ex de,hl
    pop hl
    add hl,de
    ld c,24
merge_block:
    ; HL=ROM source, DE=pattern table address of one tile. Its 8 bytes are
    ; consecutive, so the address is set once and the VDP counts up.
    di
    ld a,e
    out (0x99),a
    ld a,d
    or 0x40
    out (0x99),a
    ld b,8
merge_byte:
    ld a,(hl)
    inc hl
    call vram_out
    djnz merge_byte
    ei
    inc d ; the same column one tile row down
    push bc
    ld bc,248
    add hl,bc
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

; HL -> sprite list of a beam record: count, then Y, X and pattern name per
; sprite. The name byte has the early clock in bit 7 and the number of the
; beam in its low two bits; the VDP ignores those two for 16x16 sprites, so
; the byte goes out as it is. Writes them as sprites 0.. in the colours of
; their beams and ends the sprite list after them.
; Returns HL after the list. Destroys A,BC,DE.
put_sprites:
    ld b,(hl)
    inc hl
    di
    xor a
    out (0x99),a
    ld a,0x5B ; 1B00h, the sprite attribute table, for writing
    out (0x99),a
    ld a,b
    or a
    jp z,sprites_end
sprite_next:
    ld a,(hl)
    inc hl
    call vram_out ; Y
    ld a,(hl)
    inc hl
    call vram_out ; X
    ld d,(hl)
    inc hl
    ld a,d
    and 0x7F
    call vram_out ; pattern name
    ld a,d
    call beam_ink
    ld e,a
    ld a,d
    and 0x80
    or e
    call vram_out ; early clock and colour
    djnz sprite_next
sprites_end:
    ld a,208
    call vram_out
    ei
    ret

; A -> VRAM, at the address set before. Called rather than inlined: the call
; and return keep successive writes more than 8 us apart.
vram_out:
    out (0x98),a
    ret

; HL -> run list of a beam record: count, then per run the number of its
; beam, the pattern table offset of its first byte (2 bytes), its number of
; bytes, and the masks of its first and last byte. A run is a horizontal
; beam: bytes 8 apart.
; A=0 erase, 1 draw in the beam colour, 2 recolour.
; Returns HL after the list. Destroys A,BC,DE.
runs:
    ld (w_runmode),a
    ld a,(hl)
    inc hl
    or a
    ret z
    ld b,a
run_next:
    push bc
    ld a,(hl)
    inc hl
    call beam_ink
    ld (w_ink),a
    ld e,(hl)
    inc hl
    ld d,(hl)
    inc hl
    ld b,(hl)
    inc hl
    ld c,(hl)
    inc hl
    ld a,(hl)
    inc hl
    ld (w_lastmask),a
    push hl
    ex de,hl
run_byte:
    ; HL=pattern table offset, B=bytes left, C=mask: the first mask, then FFh
    ld a,b
    cp 1
    jp nz,run_cell
    ld a,(w_lastmask)
    and c
    ld c,a
run_cell:
    call cell
    ld c,0xFF
    ld de,8
    add hl,de
    djnz run_byte
    pop hl
    pop bc
    djnz run_next
    ret

; One byte of a run. HL=pattern table offset, C=mask.
; Preserves BC and HL. Destroys A,DE.
cell:
    push bc
    push hl
    ld a,(w_runmode)
    cp 2
    jp z,cell_ink
    ; What lies under the beam: the outline from ROM if the tile column is
    ; revealed, else nothing.
    ld a,l
    srl a
    srl a
    srl a
    ld e,a
    ld d,0
    push hl
    ld hl,w_revealed
    add hl,de
    ld a,(hl)
    pop hl
    or a
    jp z,cell_base
    push hl
    ld de,outline
    add hl,de
    ld a,(hl)
    pop hl
cell_base:
    ld b,a
    ld a,(w_runmode)
    or a
    ld a,b
    jp z,cell_erase
    or c
    call write_byte
cell_ink:
    ld a,(w_ink)
    add a,a
    add a,a
    add a,a
    add a,a
    or 1 ; beam colour on black
    jp cell_color
cell_erase:
    call write_byte
    ld a,0xF1
cell_color:
    ; The colour table has the pattern table's layout, 2000h above it.
    ld b,a
    ld a,h
    add a,0x20
    ld h,a
    ld a,b
    call write_byte
    pop hl
    pop bc
    ret
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
; Page 3 (stack, work area) is accessed directly so its slot selection is
; never touched. Pages 0-2 are probed through ram_read and ram_write.
; Probe 4 x 16KB in this slot plus the upper half of page 3, which is all
; the RAM of an 8KB machine, using distinct tags in TWO passes.
; Mirrored pages therefore count once; ROM/open bus cannot pass both tags.
; E100h is written last, so C100h fails if it is only a mirror of E100h.
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
    ld (w_ram_slot),a
    and 0x80
    jp z,ram_slot_ready
    ; Page 3 is this very slot, so FFFFh reads back its secondary slot
    ; register (inverted). Never trust the SLTTBL shadow here: a wrong
    ; subslot makes RDSLT/WRSLT switch page 3 away from the stack.
    ld a,(0xFFFF)
    ld b,a
    ld a,255
    sub b
    ld b,a
    ld (w_sub_bios),a
    srl a
    srl a
    srl a
    srl a
    and 12
    ld c,a ; subslot of page 3, placed as in a slot ID
    ld a,(w_ram_slot)
    or c
    ld (w_ram_slot),a
    ld a,c
    srl a
    srl a
    ld c,a
    ld a,b
    and 0xFC
    or c
    ld (w_sub_ram),a
ram_slot_ready:
    ; How pages 0-2 of the RAM slot are reached, one byte per page in w_modes.
    ; 1: RDSLT/WRSLT.
    ; 0: not probed. In the slot of the BIOS, pages 0 and 1 are the BIOS and
    ;    BASIC, so nothing is written to the system ROM.
    ; 2: only page 0 of another subslot of slot 0. RDSLT/WRSLT would switch
    ;    the BIOS away under itself there, so ram_read and ram_write switch
    ;    the subslot themselves.
    ld a,(0xFCC1) ; EXPTBL: main ROM slot
    and 0x80
    ld b,a
    ld hl,w_modes
    ld a,(w_ram_slot)
    cp b
    jp z,modes_bios_slot
    and 3
    ld a,1
    jp nz,modes_page_0
    ld a,2
modes_page_0:
    ld (hl),a
    inc hl
    ld (hl),1
    jp modes_ready
modes_bios_slot:
    ld (hl),0
    inc hl
    ld (hl),0
modes_ready:
    inc hl
    ld (hl),1
    ld hl,0x0100
    call ram_read
    ld (w_saved),a
    ld hl,0x4100
    call ram_read
    ld (w_saved+1),a
    ld hl,0x8100
    call ram_read
    ld (w_saved+2),a
    ld a,(0xC100) ; page 3: direct access
    ld (w_saved+3),a
    ld a,(0xE100)
    ld (w_saved+4),a
    ld hl,0x0100
    ld e,81
    call ram_write
    ld hl,0x4100
    ld e,98
    call ram_write
    ld hl,0x8100
    ld e,115
    call ram_write
    ld a,132
    ld (0xC100),a ; page 3: direct access
    ld a,149
    ld (0xE100),a
    ld hl,0x0100
    call ram_read
    cp 81
    ld a,0
    jp nz,probe_0_0
    ld a,16
probe_0_0:
    ld (w_found),a
    ld hl,0x4100
    call ram_read
    cp 98
    ld a,0
    jp nz,probe_0_1
    ld a,16
probe_0_1:
    ld (w_found+1),a
    ld hl,0x8100
    call ram_read
    cp 115
    ld a,0
    jp nz,probe_0_2
    ld a,16
probe_0_2:
    ld (w_found+2),a
    ld a,(0xC100) ; page 3: direct access
    cp 132
    ld a,0
    jp nz,probe_0_3
    ld a,16
probe_0_3:
    ld (w_found+3),a
    ld a,(0xE100)
    cp 149
    ld a,0
    jp nz,probe_0_4
    ld a,8
probe_0_4:
    ld (w_found+4),a
    ld hl,0x0100
    ld e,174
    call ram_write
    ld hl,0x4100
    ld e,157
    call ram_write
    ld hl,0x8100
    ld e,140
    call ram_write
    ld a,123
    ld (0xC100),a ; page 3: direct access
    ld a,106
    ld (0xE100),a
    ld hl,0x0100
    call ram_read
    cp 174
    jp z,probe_1_0
    xor a
    ld (w_found),a
probe_1_0:
    ld hl,0x4100
    call ram_read
    cp 157
    jp z,probe_1_1
    xor a
    ld (w_found+1),a
probe_1_1:
    ld hl,0x8100
    call ram_read
    cp 140
    jp z,probe_1_2
    xor a
    ld (w_found+2),a
probe_1_2:
    ld a,(0xC100) ; page 3: direct access
    cp 123
    jp z,probe_1_3
    xor a
    ld (w_found+3),a
probe_1_3:
    ld a,(0xE100)
    cp 106
    jp z,probe_1_4
    xor a
    ld (w_found+4),a
probe_1_4:
    ld a,(w_saved)
    ld e,a
    ld hl,0x0100
    call ram_write
    ld a,(w_saved+1)
    ld e,a
    ld hl,0x4100
    call ram_write
    ld a,(w_saved+2)
    ld e,a
    ld hl,0x8100
    call ram_write
    ld a,(w_saved+3)
    ld (0xC100),a
    ld a,(w_saved+4)
    ld (0xE100),a
    ; Page 3 counts 16 if its lower half is RAM, else what its upper half gave.
    ld a,(w_found+3)
    or a
    jp nz,page_3_counted
    ld a,(w_found+4)
page_3_counted:
    ld c,a
    ld a,(w_found)
    add a,c
    ld c,a
    ld a,(w_found+1)
    add a,c
    ld c,a
    ld a,(w_found+2)
    add a,c
    ld (w_ram_kb),a
    ei
    ret

; HL=address in page 0-2 of the RAM slot. Returns A=the byte there, or 0 for
; a page that is not probed (no probe tag is 0). Interrupts must be off.
; Destroys BC,DE.
ram_read:
    call ram_mode
    or a
    ret z
    cp 2
    jp z,ram_read_direct
    ld a,(w_ram_slot)
    call 0x000C ; RDSLT
    ret
ram_read_direct:
    ld a,(w_sub_ram)
    ld (0xFFFF),a
    ld b,(hl)
    ld a,(w_sub_bios)
    ld (0xFFFF),a
    ld a,b
    ret

; HL=address in page 0-2 of the RAM slot, E=byte to write there. A page that
; is not probed is left alone. Interrupts must be off. Destroys AF,BC,DE.
ram_write:
    call ram_mode
    or a
    ret z
    cp 2
    jp z,ram_write_direct
    ld a,(w_ram_slot)
    call 0x0014 ; WRSLT
    ret
ram_write_direct:
    ; No BIOS call and no interrupt between the two writes: page 0 does not
    ; show the BIOS in between.
    ld a,(w_sub_ram)
    ld (0xFFFF),a
    ld (hl),e
    ld a,(w_sub_bios)
    ld (0xFFFF),a
    ret

; HL=address in page 0-2 -> A=the byte of w_modes for that page.
; Preserves DE,HL. Destroys BC.
ram_mode:
    ld a,h
    srl a
    srl a
    srl a
    srl a
    srl a
    srl a
    ld c,a
    ld b,0
    push hl
    ld hl,w_modes
    add hl,bc
    ld a,(hl)
    pop hl
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
    ld (w_font),hl
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
    ld hl,(w_font)
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
    dw text8,text16,text32,text48,text64
text8:
    db "Main RAM :  8KB",0
text16:
    db "Main RAM : 16KB",0
text32:
    db "Main RAM : 32KB",0
text48:
    db "Main RAM : 48KB",0
text64:
    db "Main RAM : 64KB",0

sprites:
    incbin "assets/sprites.dat"
outline:
    incbin "assets/outline.dat"
solid:
    incbin "assets/solid.dat"
; No fill up to the end of the page here: zmac rejects a ds fill value above
; 127, so tools/build.py pads the image with FFh to 16 KiB.
rom_end:
