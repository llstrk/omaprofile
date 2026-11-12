-- Route Omarchy's web app keybindings through omaprofile so a profile is
-- picked (or a rule applies) before the web app opens.
--
-- `omaprofile install` copies this file to ~/.config/hypr/omaprofile.lua and
-- loads it from ~/.config/hypr/hyprland.lua BEFORE Omarchy's defaults:
--   require("hypr.omaprofile")
--   require("default.hypr.omarchy")
-- The helpers module is cached by require(), so the defaults see these
-- overrides when they build the preinstalled web app bindings.
--
-- If a future Omarchy drops or renames these helpers, this does nothing and
-- the bindings keep Omarchy's own launcher instead of breaking the config.

local ok = pcall(require, "default.hypr.helpers")
if not ok or type(o) ~= "table" or type(o.shell_quote) ~= "function"
    or type(o.launch_webapp) ~= "function" then
  return
end

function o.launch_webapp(url)
  return "omaprofile webapp open " .. o.shell_quote(url)
end

function o.launch_webapp_sole(name, url)
  return "omarchy-launch-or-focus " .. o.shell_quote(name) .. " " .. o.shell_quote(o.launch_webapp(url))
end
