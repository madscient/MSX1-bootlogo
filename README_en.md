# MSX1 Boot Logo Animation

[日本語](README.md)

A 16KB cartridge ROM for the MSX1. On reset it plays a logo animation, shows the size of the main RAM, and then lets BASIC start as usual. It also runs on machines with 8KB of RAM.

![Preview of the animation](docs/images/preview.gif)

The preview is rendered from the screen that the verification program draws; it is not a recording of an emulator. The RAM size text is drawn with a stand-in font; a real machine shows it in its own system font. The preview loops, but the ROM plays once and then goes on to BASIC.

## Usage

There are two ROM images. They show the same thing and differ only in the address they are placed at.

| File | Location | When to use |
|---|---|---|
| `msx_logo_page1.rom` | 4000h–7FFFh (page 1) | Use this one normally |
| `msx_logo_page2.rom` | 8000h–BFFFh (page 2) | When the ROM has to sit in page 2 |

Releases contain both images. If you build them yourself, the steps under "Build" below produce both.

Insert an image into a cartridge slot of an emulator and reset the machine as an MSX1. The images are not files with a BLOAD header.

**Set the ROM type to a 16KB ROM without a mapper that is placed in the page of the image only.**

| Emulator | For page 1 | For page 2 |
|---|---|---|
| openMSX | `Normal4000` | `Normal8000` |
| blueMSX | Normal 4000H | BASIC ROM |

If you let the emulator pick the ROM type, a 16KB ROM is placed so that the same contents also appear in the other page (a mirror); this was confirmed with openMSX and blueMSX. With that placement an original BIOS starts the ROM twice, so the animation plays twice. Operation with a mirrored placement is not guaranteed.

## What it does

| From the start | Effect |
|---|---|
| 0–0.5 s | Black screen |
| 0.5–4.1 s | Up to three laser beams fly vertically, horizontally and diagonally, flickering between colours, while parts of the outline appear |
| 4.1–4.3 s | The complete white outline is shown |
| 4.3–5.5 s | The inside of the outline is filled with white from left to right |
| 5.5–6.7 s | The background turns blue from top to bottom, leaving a black box around the logo |
| 6.7–7.5 s | The finished screen is shown |
| About 7.5 s | The main RAM is measured and `Main RAM : ##KB` appears below the logo |
| The next 2 s | The logo and the RAM size stay on screen |
| About 9.5 s | Control returns to the BIOS start-up, which goes on to BASIC as usual |

- The times count from the moment the cartridge has set up the screen. They do not include the start-up display or delays of the machine's own BIOS.
- The animation has the same length on 50Hz and 60Hz machines.
- There is no sound.
- The lasers move the same way every time.
- The lasers change colour every two frames, in the order white, light yellow, cyan. The three beams on screen at one time always have different colours.
- The blue is colour 4 of the fixed MSX1 palette. It is a different shade from the blue of the MSX2 boot screen.
- The logo was redrawn as an outline with the MSX2 boot screen as a reference. It is not a pixel-exact copy.
- Automatic start-up from other cartridges or a boot disk follows the normal behaviour of the machine's BIOS.

## RAM size display

The ROM shows the amount of RAM in the slot selected as main RAM, for the standard MSX1 sizes of 8 / 16 / 32 / 48 / 64KB. It is not the amount of free memory in BASIC.

The following are not counted.

- The total size of a memory mapper beyond 64KB (with a 128KB mapper, for example, anything beyond the 64KB visible at that moment is not counted)
- The sum of expansion RAM spread over several slots
- Unusual configurations that are mirrored in units smaller than 8KB

## Known limitations

- The ROM has not been tested on real hardware. It has been tested in emulators (openMSX and blueMSX). What has been confirmed is listed under "検証状況" (verification status) in the [technical specification](docs/technical.md#検証状況), which is written in Japanese.
- When the ROM is also mirrored into the other page, the animation plays twice (see "Usage" above).
- On machines with built-in software, such as the HB-11, that software starts after the animation instead of BASIC. This is the normal start-up behaviour of those machines.

## Build

You need Python 3 and one of the following Z80 assemblers. All three have been confirmed to produce identical ROMs.

- [zmac](http://48k.ca/zmac.html) 18oct2022
- [sjasmplus](https://github.com/z00m128/sjasmplus) 1.24.0
- [Pasmo](https://pasmo.speccy.org/) 0.5.4.beta2

```text
python tools/build.py
```

The ROMs are `build/msx_logo_page1.rom` and `build/msx_logo_page2.rom` (16,384 bytes each). Add `--page 1` or `--page 2` to build only one of them.

For each assembler, the script uses the path in the environment variable below if it is set, and otherwise looks on PATH. It tries them from the top and uses the first one it finds. With an option such as `--asm zmac` it uses that assembler only.

| Assembler | Environment variable | Name looked up on PATH |
|---|---|---|
| zmac | `ZMAC_EXE` | `zmac` |
| sjasmplus | `SJASMPLUS_EXE` | `sjasmplus` |
| Pasmo | `PASMO_EXE` | `pasmo` |

All the build script does is run the assembler from the root of the repository and pad the resulting image with FFh to 16KB. Other assemblers have not been tried. The source uses nothing but `0x` hexadecimal numbers, `org`, `equ`, `db`, `dw`, `ds`, `include`, `incbin`, and labels with a colon at the start of a line.

## Verification

Two scripts check the built ROMs (confirmed with Python 3.13). Both check the two images one after the other and end with a non-zero exit code if any check fails. Add `--page 1` or `--page 2` to check only one image.

```text
python tools/verify.py
python tools/test_openmsx.py
```

`tools/verify.py` is a limited verification program that executes the Z80 instructions of the ROM and handles the BIOS calls in their place. It is not an emulator of a whole MSX. It writes its result to `build/verification.json`. If [Pillow](https://python-pillow.org/) is installed, it also writes the preview images (`build/preview.gif` and others).

`tools/test_openmsx.py` actually boots the ROM in [openMSX](https://openmsx.org/) and checks it. openMSX is looked up through the environment variable `OPENMSX_DIR` (the folder that holds the openMSX executable), and otherwise on PATH. It opens no window and does not change your openMSX settings. It writes its result to `build/openmsx_test.json`.

With an openMSX machine name, as in `python tools/test_openmsx.py --machine Sony_HB-10`, the script tests on that machine. Machines with their original BIOS need their system ROMs; these are looked up in `share/systemroms` of the openMSX installation and in the folders listed in the environment variable `MSX_ROM_DIRS` (separated like PATH).

## More information

The binary specification, the use of memory, how the RAM size is measured, how to regenerate the logo image data, and the scope of the verification are described in [docs/technical.md](docs/technical.md) (in Japanese).

## License

[MIT License](LICENSE).

## Trademark

The MSX logo is a trademark of MSX Licensing Corporation.
