# Macon Geothermal Heat Pump Control
Use CN17, Pin A1 and B1 to connect your RS485 adapter. See Photo...

Scripts for controlling Macon geothermal heat pumps via Modbus RTU (Protocol V1.3).

## Files
- **maconread2db.py**: Reads and logs up to 29 registers + 28 bits (e.g., temperatures, statuses, errors) + volumeflow.
- **write_freq.py** (v1.8.3): Sets DHW setpoint (Reg 2004=45°C), hot water ΔT (Reg 2007=5°C), host control (Reg 2056) based on brine pump status (Reg 2136 Bit 3), compressor frequency (Reg 2057=70 Hz), checks errors (Reg 2137), and supports soft reset (Reg 2000).

## Setup
- Install pymodbus: `pip3 install pymodbus`
- Configure MODBUS_PORT (e.g., /dev/ttyAMA0) in scripts.
- Logs saved to `/home/gh/macon/macon_control.log`.

## Usage
- Run `./write_freq.py` to set DHW parameters (single-attempt operations).
- Run `./maconread2db.py` to monitor system status.

## Protocol Reference
Based on Macon Protocol V1.3: Modbus RTU, baud 2400, slave ID 1. No dedicated reset register; use Reg 2000 (OFF→ON) for soft reset.

