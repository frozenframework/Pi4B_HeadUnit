#!/usr/bin/env bash
# Stage 70: AM/FM radio on the RTL-SDR Blog V4 (tier 3, or RADIO=1).
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
if [ "$(conf_get RADIO 0)" != 1 ]; then say "radio: skipped (RADIO=0 in this tier)"; exit 0; fi
# The rtl-sdr package installs a blacklist for the DVB-TV driver so the SDR tools can claim the dongle.
[ -f /etc/modprobe.d/rtl-sdr-blacklist.conf ] || echo "blacklist dvb_usb_rtl28xxu" > /etc/modprobe.d/rtl-sdr-blacklist.conf
say "radio: rtl_fm ready. AM uses the V4's built-in HF upconverter; Hudiy's FM player can use the same dongle (one at a time)."
