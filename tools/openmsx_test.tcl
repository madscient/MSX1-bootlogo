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
proc slotted {ps ss addr} {
    debug read "slotted memory" [expr {($ps * 4 + [sub $ss]) * 0x10000 + $addr}]
}

# The RAM between the work area and the caller's stack, which the ROM must
# leave alone. Stops 256 bytes short of the stack pointer at INIT entry
# because the BIOS and interrupts use the stack below it.
proc above {sp} {
    set size [expr {$sp - 0x100 - 0xD8A0}]
    expr {$size > 0 ? [mem 0xD8A0 $size] : ""}
}

# What the machine itself says about the slot selected in page 3, read
# without going through the ROM under test.
proc ram_view {} {
    lassign [get_selected_slot 3] ps ss
    set device [machine_info slot $ps [sub $ss] 3]
    set pages 0
    set probes ""
    for {set page 0} {$page < 4} {incr page} {
        if {[machine_info slot $ps [sub $ss] $page] eq $device} { incr pages }
        append probes [format %02x [slotted $ps $ss [expr {$page * 0x4000 + 0x100}]]]
    }
    list slot=$ps/$ss kb=[expr {$pages * 16}] probes=$probes
}

proc on_init {} {
    incr ::calls
    set ::entry_sp [reg sp]
    set ret [peek16 $::entry_sp]
    rec init call=$::calls sp=$::entry_sp ret=$ret marker=[mem 0xD820 2] bios2b=[peek 0x002B] \
        guard=[mem $::entry_sp 16] above=[above $::entry_sp]
    set ::ret_bp [debug set_bp $ret "\[reg sp\] == [expr {$::entry_sp + 2}]" {guarded on_return}]
}

proc on_return {} {
    debug remove_bp $::ret_bp
    rec return call=$::calls marker=[mem 0xD820 2] guard=[mem $::entry_sp 16] \
        above=[above $::entry_sp] work=[mem 0xC000 0x18A0]
    # The BIOS may call INIT again through a mirror right away, so wait for
    # things to settle before looking at the result of this boot.
    catch {after cancel $::settle}
    set ::settle [after time $::env(LOGO_SETTLE) {guarded settled}]
}

proc on_mark {label} {
    if {[info exists ::seen($::boot,$label)]} return
    set ::seen($::boot,$label) 1
    rec mark name=$label
}

proc on_text {} {
    set font [peek16 0xD826]
    rec text font=$font glyphs=[mem $font 0x800] {*}[ram_view]
}

proc on_final {} {
    rec final own_slot=[peek 0xD822] ram_slot=[peek 0xD823] ram_kb=[peek 0xD824] \
        mirrored=[peek 0xD825] r7=[debug read "VDP regs" 7] \
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
        marker=[mem 0xD820 2] mode=[get_screen_mode] screen=$screen
    if {$::boot < $::env(LOGO_RESETS)} {
        incr ::boot
        set ::calls 0
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
rec setup machine=[machine_info config_name] version=[hex [openmsx_info version]] \
    cart=$cart_ps/$cart_ss \
    page2=[format %02x%02x [slotted $cart_ps $cart_ss 0x8000] [slotted $cart_ps $cart_ss 0x8001]]

debug set_bp $sym(init) $in_cart {guarded on_init}
debug set_bp $sym(tick_done) $in_cart {guarded {rec tick}}
debug set_bp $sym(count_ram) $in_cart {guarded {rec ram_before {*}[ram_view]}}
foreach label {sweep fill blue ram_stage} {
    debug set_bp $sym($label) $in_cart [list guarded [list on_mark $label]]
}
debug set_bp $sym(ram_text_visible) $in_cart {guarded on_text}
debug set_bp $sym(return_to_basic) $in_cart {guarded on_final}
after time $::env(LOGO_LIMIT) {guarded {rec limit}; finish}
