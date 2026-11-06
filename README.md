# omaprofile — profile picker for Omarchy

Chromium keeps one process for all profiles, so a link opened from another
app lands in whichever profile happened to be used last. `omaprofile`
registers itself as the default browser, pops an Omarchy shell picker with
the profiles, and opens the link in the one you chose
(`chromium --profile-directory=<dir> <url>`).

The same picker fronts any other "which one?" step. Contexts such as
`terminal` are defined in the config with their choices and commands, and
scripts can call the generic `omaprofile pick`.

## Layout
- `bin/omaprofile` — launcher, symlinked to `~/.local/bin/omaprofile`.
- `omaprofile/` — Python package, stdlib only, about 500 lines:
  `cli.py` dispatch, `browser.py` profiles/rules/launch/default-browser,
  `contexts.py` config-defined contexts, `picker.py` overlay round trip,
  `config.py` paths and defaults, `install.py` deployment.
- `plugin/` — the `dry.omaprofile` shell overlay (`manifest.json`,
  `Omaprofile.qml`); `omaprofile install` copies it to
  `~/.config/omarchy/plugins/dry.omaprofile/` and enables it.
- `hypr/omaprofile.lua` — Hyprland hook that routes Omarchy's web app
  keybindings through `omaprofile webapp open`; installed to
  `~/.config/hypr/omaprofile.lua`.
- `tests/` — `python3 -m unittest discover -s tests`.
- Config: `~/.config/omaprofile/config.json`.
- Desktop entry: `~/.local/share/applications/omaprofile.desktop`
  (default for http/https/text/html/about/unknown).

## Commands
Same grammar as `omarchy <group> <action>`.
```
omaprofile browser open [flags...] [url]     pick a profile, open the link
omaprofile browser launch <profile> [args]   open as a profile, no picker
omaprofile browser profiles                  list profiles
omaprofile webapp open <url> [flags...]      pick a profile, open as a web app
omaprofile <context> open [args...]          pick a choice, run its command
omaprofile <context> list                    list the context's choices
omaprofile contexts                          list configured contexts
omaprofile pick [--title T] [--subtitle S] CHOICE...
                                             generic picker; prints chosen id
                                             CHOICE = id|label|color|glyph|detail
omaprofile install | uninstall               plugin + default browser
omaprofile config                            print the config
```
A bare URL, a browser flag, or no argument at all means `browser open`.
That is what the desktop entry and `omarchy launch browser` (Super+Shift+Enter)
send. `--private` becomes `--incognito`; other `--flags` pass through to the
browser. Cancelling the picker drops the link. Anything else that is not a
known group or context is an error, so a typo never opens a browser.

## Picker keys
`1`-`9` pick directly, `j`/`k`/arrows move, Enter picks, Esc or a click
outside cancels, a letter picks the first choice whose label starts with it.
Keys are ignored for the first 250 ms after the overlay opens, so a held
Enter from the keybind or typing aimed elsewhere cannot pick.
Profile colors and initials come from Chromium's profile color seed and name.

For scripts and tests: `omarchy-shell shell call dry.omaprofile choose <index>`.

## Config
```json
{
  "browser": {
    "command": "chromium",
    "rules": [
      { "host": "github.com",      "profile": "Work" },
      { "host": "*.github.com",    "profile": "Work" },
      { "url":  "https://x.com/*", "profile": "Personal" }
    ]
  },
  "contexts": {
    "terminal": {
      "title": "Open terminal",
      "choices": [
        { "id": "shell", "label": "Shell", "color": "#cacccc", "command": "omarchy launch terminal" },
        { "id": "tmux",  "label": "tmux",  "color": "#1bb91f", "command": "omarchy launch terminal tmux" },
        { "id": "herdr", "label": "herdr", "color": "#f3b2be", "command": "omarchy launch terminal herdr" }
      ]
    }
  }
}
```
- Rules skip the picker. First match wins; patterns are shell globs
  (`fnmatch`), matched against the host or the full URL. `profile` is a
  display name (`Work`) or directory (`Profile 1`).
- `browser.command` is the browser binary; profiles are read from
  `~/.config/chromium/Local State` unless `browser.configDir` says otherwise.
- A context's choice `command` runs through `bash -c` with any extra
  arguments from `omaprofile <context> open ...` appended. Optional keys per
  choice: `label`, `detail`, `color`, `glyph`.
- To use the terminal picker, bind e.g. `omaprofile terminal open` in
  `~/.config/hypr/bindings.lua`.

## How it plugs in
- `xdg-open` and any app resolving `x-scheme-handler/https` run
  `omaprofile browser open <url>` via the desktop entry.
- `omarchy launch browser` takes the first word of the desktop Exec and runs
  it under `uwsm-app` with just the URL (or nothing); the dispatcher treats
  that as `browser open`.
- Launched apps run via `setsid uwsm-app --` with stdio on `/dev/null`;
  context commands go through `bash -c`.
- Web apps go through `omaprofile webapp open <url>`, which runs Chromium's
  `--app=<url>` in the chosen profile. Two hooks make that happen:
  `omaprofile install` rewrites the `Exec=omarchy-launch-webapp` line of every
  web app desktop file in `~/.local/share/applications/`, and
  `~/.config/hypr/hyprland.lua` loads `require("hypr.omaprofile")` before
  `require("default.hypr.omarchy")`, which redefines `o.launch_webapp` and
  `o.launch_webapp_sole` so the preinstalled keybindings use omaprofile too.
  Re-run `omaprofile install` after `omarchy webapp install` adds a new app.
  `omaprofile uninstall` restores the Exec lines and removes the hook file
  (the `require` line in hyprland.lua stays; delete it by hand).
  Browser rules apply to web apps as well, so a `host` rule fixes an app to
  a profile without the picker.
- Round trip with the overlay is file based like `omarchy.image-picker`:
  the CLI passes `selectionFile`/`doneFile` in the summon payload and waits
  for `doneFile` to appear. Without a running shell it fails with an error.

## Ideas
- "Always use this profile for this site" key in the picker that appends a rule.
- Remember the last choice per host and preselect it.
