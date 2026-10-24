#!/bin/bash
# One-time, on the Pi: let the pi user talk to the ReSpeaker XVF3800's USB control interface
# (needed for xvf_host / parameter changes). Run: sudo /home/pi/household-mcp/scripts/respeaker-udev.sh
set -e
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="001a", MODE="0666", TAG+="uaccess"' > /etc/udev/rules.d/99-respeaker.rules
udevadm control --reload && udevadm trigger
echo "rule installed; re-plug not needed"
