# Omarchy integration (opt-in)

PortClaim's Linux receiver is not an Omarchy fork. The installer does not modify
Hyprland, disable other pointers, remap keys, or configure Handy.

1. Back up `~/.config/hypr/input.lua` and inspect `hyprctl devices`.
2. Review `trackpad.lua` and add its device-specific profile to your user config.
   It targets Hyprland 0.56's Lua API. Three-finger drag requires matching
   compositor/libinput support; do not combine it blindly with three-finger swipes.
3. Run `hyprctl reload` then `hyprctl configerrors`. Restore the backup if invalid.
4. Test fine pointer movement, two-finger scrolling, physical clicks, and drag.
   Application support determines kinetic scrolling and pinch behavior.

The receiver's AppIndicator uses the existing system tray. If no compatible tray
is available, controls stay visible rather than becoming inaccessible.

For Handy/dictation, select `portclaim_mic.monitor` in the application, or configure
app-specific ALSA/Pulse routing if it only offers Default. Do not change the whole
desktop's default microphone merely to route one application. Configure shortcuts
separately, check for collisions, and update Omarchy's keybinding learner whenever
remapping one. No specific shortcut is reserved by this package.
