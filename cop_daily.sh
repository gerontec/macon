#!/bin/bash
cd /home/pi/python
./cop_report.py --from "$(date +%Y-%m-%d) 10:00" > /home/pi/cop_daily.log 2>&1
