# Hellbot Magna 1

Firmware and settings for my Hellbot Magna 1 3D printer.

## Hardware

- Board: Geeetech GT2560 V3.0 (ATmega2560, 16 MHz, Arduino bootloader)
- Drivers: A4988
- USB serial: CH340, 250000 baud

## Backups

`backups/` holds the stock firmware as it was read off the board on 2026-10-05:

- `flash-2026-10-05.hex`: full flash, Hellbot's Marlin build ("Marlin 1.8.0.2") plus bootloader
- `eeprom-2026-10-05.hex`: stored settings
- `m503-2026-10-05.txt`: `M503` output, the same settings in readable form

Fuses: lfuse `0xFF`, hfuse `0xD8`, efuse `0xFD`, lock `0xFF`.

To restore the stock firmware:

```
avrdude -c wiring -p m2560 -P /dev/ttyUSB0 -b 115200 -D \
  -U flash:w:backups/flash-2026-10-05.hex:i \
  -U eeprom:w:backups/eeprom-2026-10-05.hex:i
```

The serial port is `root:uucp`, so your user needs to be in the `uucp` group.
