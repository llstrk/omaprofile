-- Route Omarchy's web app keybindings through omaprofile so a profile is
-- picked (or a rule applies) before the web app opens.
--
-- Load it from ~/.config/hypr/hyprland.lua BEFORE Omarchy's defaults:
--   require("hypr.omaprofile")
--   require("default.hypr.omarchy")
-- The helpers module is cached by require(), so the defaults see these
-- overrides when they build the preinstalled web app bindings.

require("default.hypr.helpers")

function o.launch_webapp(url)
  return "omaprofile webapp open " .. o.shell_quote(url)
end

function o.launch_webapp_sole(name, url)
  return "omarchy-launch-or-focus " .. o.shell_quote(name) .. " " .. o.shell_quote(o.launch_webapp(url))
end
