#!/usr/bin/env bash
# Stage 30: ignition-aware power. The Pi stays on until it has shut down cleanly, then cuts its own power.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
MODE=$(conf_get POWER_MODE diy)
HOLD=$(conf_get HOLD_GPIO 26)
case "$MODE" in
  diy)
    # GPIO3 is the ignition sense (and the Pi 4's wake pin), so the I2C bus that shares it stays off.
    # gpio-poweroff keeps the hold pin HIGH while running and drops it only after a clean halt.
    put_block "$BOOT_CFG" power "dtparam=i2c_arm=off
dtoverlay=gpio-poweroff,gpiopin=$HOLD,active_low=1"
    ;;
  carpihat)
    # TJD CarPiHAT: its own 12V supply and latch. Ignition switches the supply on by itself (so no wake
    # pin is needed); the Pi holds the latch on GPIO25 until it has halted. I2C stays on for its clock.
    RTC=$(conf_get RTC ds1307)
    put_block "$BOOT_CFG" power "dtparam=i2c_arm=on
dtoverlay=gpio-poweroff,gpiopin=$HOLD,active_low=1$( [ "$RTC" != none ] && printf '\ndtoverlay=i2c-rtc,%s' "$RTC")"
    # The Pi keeps the right time with no internet: the kernel reads the CarPiHAT's clock at boot.
    [ "$RTC" != none ] && { apt-get purge -y -qq fake-hwclock >/dev/null 2>&1 || true; }
    ;;
  none) put_block "$BOOT_CFG" power "# power management disabled (bench mode)";;
esac
install -m 644 /opt/headunit/systemd/headunit-power.service /etc/systemd/system/
systemctl daemon-reload 2>/dev/null || true   # (no-op when not booted, e.g. image builds)
systemctl enable headunit-power.service
say "power mode: $MODE (shutdown $(conf_get SHUTDOWN_DELAY 30)s after key-off; arms only after the key has been seen ON once)"
