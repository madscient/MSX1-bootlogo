# Recorder that tools/test_openmsx.py runs inside openMSX. It only observes the
# machine and writes records; every judgement is made in the Python script.
# Environment: LOGO_OUT (record file), LOGO_SYMBOLS ("label address ..."),
# LOGO_RESETS (resets after the first boot), LOGO_SETTLE and LOGO_LIMIT
# (emulated seconds).

set renderer none
set throttle off
array set sym $::env(LOGO_SYMBOLS)
set out [open $::env(LOGO_OUT) w]
set boot 0
set calls 0
set stage ""

# One record per line: type, then key=value fields without spaces.
proc rec {type args} {
    puts $::out [concat [list $type boot=$::boot t=[machine_info time]] $args]
    flush $::out
}
proc hex {data} { binary scan $data H* digits; return $digits }
proc mem {addr size} { hex [debug read_block memory $addr $size] }

# A Tcl error inside a breakpoint callback only shows up in the openMSX
# console, which nobody sees here, so turn it into a record.
proc guarded {script} {
    if {[catch {uplevel #0 $script} msg]} { rec error msg=[hex $msg] }
}

# ss is "X" for a primary slot that is not expanded.
proc sub {ss} { expr {$ss eq "X" ? 0 : $ss} }
proc slotted_at {ps ss addr} { expr {($ps * 4 + [sub $ss]) * 0x10000 + $addr} }
proc slotted {ps ss addr} { debug read "slotted memory" [slotted_at $ps $ss $addr] }

# The RAM of page 3 that the ROM must leave alone: everything below its work
# area, and from the end of that up to the caller's stack. Stops 256 bytes
# short of the stack pointer at INIT entry because the BIOS and interrupts
# use the stack below it.
proc free_ram {sp} {
    set end [expr {$::sym(work) + $::sym(work_size)}]
    set size [expr {$sp - 0x100 - $end}]
    list below=[mem 0xC000 [expr {$::sym(work) - 0xC000}]] above=[expr {$size > 0 ? [mem $end $size] : ""}]
}

# What the machine itself says about the slot selected in page 3, read
# without going through the ROM under test.
proc ram_view {} {
    lassign [get_selected_slot 3] ps ss
    set device [machine_info slot $ps [sub $ss] 3]
    set pages 0
    set probes ""
    foreach addr {0x0100 0x4100 0x8100 0xC100 0xE100} {
        if {$addr < 0xE000 && [machine_info slot $ps [sub $ss] [expr {$addr >> 14}]] eq $device} { incr pages }
        append probes [format %02x [slotted $ps $ss $addr]]
    }
    set kb [expr {$pages * 16}]
    # machine_info names one device per 16 KiB page, so an 8 KiB RAM looks
    # like 16. Tell them apart by writing: the lower half of page 3 is RAM if
    # a value put at C100h stays there and does not show up at E100h too.
    set low [slotted $ps $ss 0xC100]
    set high [slotted $ps $ss 0xE100]
    foreach value {0x55 0xAA 0x33} { if {$value != $low && $value != $high} break }
    debug write "slotted memory" [slotted_at $ps $ss 0xC100] $value
    set ram [expr {[slotted $ps $ss 0xC100] == $value && [slotted $ps $ss 0xE100] == $high}]
    debug write "slotted memory" [slotted_at $ps $ss 0xC100] $low
    if {!$ram} { incr kb -8 }
    list slot=$ps/$ss kb=$kb probes=$probes
}

# The sprite table and what selects the step and colour on screen.
proc beam_view {} {
    list jiffy=[peek 0xFC9E] step=[peek $::sym(w_stage)] r1=[debug read "VDP regs" 1] \
        spr=[hex [debug read_block VRAM 0x1B00 0x80]]
}

proc on_init {} {
    incr ::calls
    set ::stage ""
    set ::entry_sp [reg sp]
    set ret [peek16 $::entry_sp]
    rec init call=$::calls sp=$::entry_sp ret=$ret bios2b=[peek 0x002B] \
        rg1sav=[peek 0xF3E0] r1=[debug read "VDP regs" 1] bottom=[peek16 0xFC48] himem=[peek16 0xFC4A] \
        guard=[mem $::entry_sp 16] {*}[free_ram $::entry_sp]
    set ::ret_bp [debug set_bp $ret "\[reg sp\] == [expr {$::entry_sp + 2}]" {guarded on_return}]
}

proc on_return {} {
    debug remove_bp $::ret_bp
    rec return call=$::calls rg1sav=[peek 0xF3E0] r1=[debug read "VDP regs" 1] \
        guard=[mem $::entry_sp 16] {*}[free_ram $::entry_sp] work=[mem $::sym(work) $::sym(work_size)]
    # The BIOS calls INIT once for every copy of the ROM it finds in the slot,
    # so wait for things to settle before looking at the result of this boot.
    catch {after cancel $::settle}
    set ::settle [after time $::env(LOGO_SETTLE) {guarded settled}]
}

# due is the tick deadline the stage starts on; the sweep counts its colour
# frames from there.
proc on_mark {label} {
    if {[info exists ::seen($::boot,$label)]} return
    set ::seen($::boot,$label) 1
    set ::stage $label
    rec mark name=$label due=[peek $::sym(w_due)] sprpat=[hex [debug read_block VRAM 0x3800 0xC0]]
}

# During the sweep a tick carries the whole screen, and every frame in
# between the sprite table and the colour bytes that differ from F1h.
proc on_tick {} {
    if {$::stage eq "sweep"} {
        rec tick {*}[beam_view] pattern=[hex [debug read_block VRAM 0x0000 0x1800]] \
            colors=[hex [debug read_block VRAM 0x2000 0x1800]]
    } else {
        rec tick
    }
}

proc on_halt {} {
    if {$::stage ne "sweep"} return
    rec frame {*}[beam_view] cells=[hex [string map [list \xF1 {}] [debug read_block VRAM 0x2000 0x1800]]]
}

proc on_text {} {
    set font [peek16 $::sym(w_font)]
    rec text font=$font glyphs=[mem $font 0x800] {*}[ram_view]
}

proc on_final {} {
    rec final ram_slot=[peek $::sym(w_ram_slot)] ram_kb=[peek $::sym(w_ram_kb)] r7=[debug read "VDP regs" 7] \
        pattern=[hex [debug read_block VRAM 0x0000 0x1800]] \
        names=[hex [debug read_block VRAM 0x1800 0x300]] \
        colors=[hex [debug read_block VRAM 0x2000 0x1800]] \
        sprite=[debug read VRAM 0x1B00]
}

proc settled {} {
    set pc [reg pc]
    set screen ""
    catch {set screen [hex [get_screen]]}
    set pages {}
    foreach page {0 1 2 3} { lappend pages [join [get_selected_slot $page] /] }
    rec settled calls=$::calls pc=$pc pc_slot=[lindex $pages [expr {$pc >> 14}]] pages=[join $pages ,] \
        mode=[get_screen_mode] screen=$screen
    if {$::boot < $::env(LOGO_RESETS)} {
        incr ::boot
        set ::calls 0
        set ::stage ""
        reset
    } else {
        finish
    }
}

proc finish {} {
    close $::out
    exit
}

lassign [machine_info external_slot slota] cart_ps cart_ss
set in_cart "\[pc_in_slot $cart_ps [sub $cart_ss]\]"
# The first four bytes of a ROM header: "AB" and the INIT address.
proc header {ps ss addr} {
    set bytes ""
    for {set i 0} {$i < 4} {incr i} { append bytes [format %02x [slotted $ps $ss [expr {$addr + $i}]]] }
    return $bytes
}
rec setup machine=[machine_info config_name] version=[hex [openmsx_info version]] \
    cart=$cart_ps/$cart_ss page1=[header $cart_ps $cart_ss 0x4000] page2=[header $cart_ps $cart_ss 0x8000]

debug set_bp $sym(init) $in_cart {guarded on_init}
debug set_bp $sym(tick_done) $in_cart {guarded on_tick}
debug set_bp $sym(tick_halt) $in_cart {guarded on_halt}
debug set_bp $sym(count_ram) $in_cart {guarded {rec ram_before {*}[ram_view]}}
foreach label {sweep beams_off fill blue ram_stage} {
    debug set_bp $sym($label) $in_cart [list guarded [list on_mark $label]]
}
debug set_bp $sym(ram_text_visible) $in_cart {guarded on_text}
debug set_bp $sym(return_to_basic) $in_cart {guarded on_final}
after time $::env(LOGO_LIMIT) {guarded {rec limit}; finish}
