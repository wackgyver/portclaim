-- Optional profile: add to ~/.config/hypr/input.lua after reviewing local settings.
-- Hyprland 0.56 Lua API; native three-finger drag needs compositor/libinput support.
-- This file is NOT applied by the installer. No keybinding or fallback pointer changes.
hl.device({
  name = "portclaim-magic-trackpad",
  sensitivity = 0.0,
  accel_profile = "adaptive",
  natural_scroll = true,
  scroll_method = "2fg",
  scroll_factor = 1.0,
  tap_to_click = true,
  clickfinger_behavior = true,
  tap_and_drag = false,
  drag_3fg = 1,
  drag_lock = 0,
  disable_while_typing = false,
})
