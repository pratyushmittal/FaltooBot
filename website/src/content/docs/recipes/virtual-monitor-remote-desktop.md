---
title: Virtual monitor / remote desktop
description: A small recipe for turning a headless Ubuntu machine into a desktop you can reach through RustDesk.
order: 40
---

## Why this recipe exists

We made this recipe for headless Ubuntu machines that still need to come back as a usable desktop after reboot.

The goal is simple:
- boot into a desktop session
- create a virtual monitor even without HDMI attached
- auto-login the real desktop user
- land directly in an XFCE session
- make RustDesk available after startup
- avoid a post-boot lock screen that blocks remote access

## Setup

### 1) Install the required packages

```bash
sudo apt-get update
sudo apt-get install -y \
  xfce4 \
  dbus-x11 \
  lightdm \
  xserver-xorg-video-dummy
```

### 2) Add the dummy monitor config

Create:

```bash
/etc/X11/xorg.conf.d/10-headless-dummy.conf
```

Use this content:

```conf
Section "Device"
    Identifier  "DummyDevice"
    Driver      "dummy"
    VideoRam    256000
EndSection

Section "Monitor"
    Identifier  "DummyMonitor"
    HorizSync   28.0-80.0
    VertRefresh 48.0-75.0
    Modeline    "1920x1080" 172.80 1920 2040 2248 2576 1080 1081 1084 1118
EndSection

Section "Screen"
    Identifier  "DummyScreen"
    Device      "DummyDevice"
    Monitor     "DummyMonitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080"
    EndSubSection
EndSection

Section "ServerLayout"
    Identifier  "DummyLayout"
    Screen      "DummyScreen"
EndSection
```

This makes Xorg expose a stable `1920x1080` desktop even when no physical monitor is connected.

### 3) Turn on LightDM autologin for the real desktop user

Create:

```bash
/etc/lightdm/lightdm.conf.d/50-remote-autologin.conf
```

Use content like this. This includes the exact LightDM autologin keys we used, with a concrete example user:

```ini
[Seat:*]
autologin-user=exedev
autologin-user-timeout=0
autologin-session=xfce
greeter-hide-users=true
allow-guest=false
```

This makes the machine log into the `exedev` user automatically and start XFCE on boot.

Replace `exedev` with the actual desktop account you plan to control over RustDesk. The important part is that LightDM should log into a real user session automatically, and that session should land in XFCE instead of stopping at the greeter or some other desktop.

### 4) Make sure a lock screen does not block remote access after boot

If `light-locker` is installed, remove it:

```bash
sudo apt-get purge -y light-locker
```

If you use some other screen locker, disable its autostart for the auto-login user too. After boot, RustDesk should reach the live XFCE desktop, not a locked screen.

### 5) Boot into graphical mode

```bash
sudo systemctl set-default graphical.target
```

### 6) Enable RustDesk

```bash
sudo systemctl enable rustdesk
sudo systemctl start rustdesk
```

## Reboot test

```bash
sudo reboot
```

## Verify after reboot

Run:

```bash
systemctl is-active lightdm
systemctl is-active rustdesk
systemctl get-default
loginctl list-sessions
ps -u remote -f
pgrep -af 'xfce4-session|light-locker' || true
```

You want to see:
- `lightdm` as `active`
- `rustdesk` as `active`
- `graphical.target` as the default target
- user `remote` logged in
- an XFCE session running
- no `light-locker` process, or any other post-boot lock screen blocking the session

## Why this works

The setup is small but complete:

```text
xserver-xorg-video-dummy
+ dummy Xorg config
+ LightDM
+ XFCE
+ auto-login user
+ RustDesk
+ no post-boot screen locker
+ graphical.target
= headless machine that still boots into a usable desktop
```

What each part does:

- `xserver-xorg-video-dummy`
  - This is the fake video driver. It lets Xorg start even when no physical monitor is attached.

- dummy Xorg config
  - This tells Xorg to actually use the dummy driver and gives it a virtual screen size such as `1920x1080`.
  - Without this, installing the dummy driver alone often is not enough; Xorg may still fail to start or may choose the wrong display setup.

- LightDM
  - This is the display manager.
  - It starts the graphical login screen and owns the main desktop display startup flow.
  - In this recipe, it is the thing that launches the X session automatically after boot.

- XFCE
  - This is the desktop environment that runs inside that X session.
  - You need some real desktop session so remote-control tools have an actual graphical session to connect to.
  - XFCE is used here because it is lightweight and reliable on servers.

- auto-login user
  - This tells LightDM to log in the chosen user automatically at boot.
  - That matters because many remote desktop setups are much more reliable when a real user session is already running, instead of waiting at a login screen.

- RustDesk
  - This is the remote desktop tool you use to connect to that running desktop.

- no post-boot screen locker
  - If `light-locker` or another lock screen starts right after boot, RustDesk may reach a locked desktop instead of the usable session you wanted.
  - For a headless machine that should always be reachable after restart, disabling that post-boot locker is usually the most reliable choice.

- `graphical.target`
  - This tells systemd to boot into graphical mode instead of a pure text/CLI target.
  - It ensures the display manager and desktop stack are part of normal boot.

Put together:
- the dummy driver creates a virtual monitor
- LightDM starts the display session
- auto-login gets a real user session running
- XFCE provides the desktop
- RustDesk lets you connect to it
- no post-boot locker blocks the session
- `graphical.target` makes the machine boot into that state every time

## In one line

If you only want the minimum reliable setup, this recipe is the answer: create a dummy display, auto-login the real user into XFCE, make sure no lock screen blocks the session after boot, enable RustDesk, and boot the machine into `graphical.target`.
