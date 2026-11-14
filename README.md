# omaprofile — profile picker for Omarchy

Chromium keeps one process for all profiles, so a link opened from another
app lands in whichever profile happened to be used last. `omaprofile`
registers itself as the default browser, pops an Omarchy shell picker with
the profiles, and opens the link in the one you chose
(`chromium --profile-directory=<dir> <url>`).

The same picker fronts any other "which one?" step. Contexts such as
`terminal` are defined in the config with their choices and commands, and
scripts can call the generic `omaprofile pick`.

## Requirements
- [Omarchy](https://omarchy.org) with shell plugins (`omarchy-shell`,
  `omarchy plugin`); tested on 4.5.
- Chromium, Omarchy's default browser. Other Chromium-family browsers work
  with `browser.command` and `browser.configDir` set (see Config).
- Python 3.11+ (Omarchy ships it). No other dependencies.

## Install
```sh
omarchy plugin add https://github.com/llstrk/omaprofile.git --enable
```
`omarchy plugin add` (alias `omarchy plugin install`) clones the repository
into `~/.config/omarchy/plugins/dry.omaprofile/` and enables the picker
overlay. A few seconds later the overlay asks to finish setting up and lists
what that changes; nothing outside the plugin folder is touched until you
pick **Set it up**. **Not now** asks again the next time the shell starts,
**Don't ask again** stops asking. Setting up (or running
`~/.config/omarchy/plugins/dry.omaprofile/bin/omaprofile install` yourself):
- writes `~/.local/bin/omaprofile`, a small script that runs the clone;
- writes `~/.config/omaprofile/config.json` with the defaults, unless one exists;
- writes `~/.local/share/applications/omaprofile.desktop` and makes it the
  default browser, after recording the current link handlers;
- writes `~/.local/share/applications/chromium.desktop`, a copy of
  Chromium's entry that sends Omarchy web apps through the picker (see
  [How it plugs in](#how-it-plugs-in)). A `chromium.desktop` you had there is
  kept and put back on uninstall.

Last, the overlay asks whether each profile should get its own theme (No is
the default); see [A theme per profile](#a-theme-per-profile).

Open a link from any app to try it. If Chromium has never run there are no
profiles yet, so links open in plain Chromium until it has made one.

### Update
```sh
omarchy plugin update dry.omaprofile
```
The update pulls the overlay and the CLI together (the CLI runs from the
clone); when the shell reloads the overlay it also refreshes
`~/.local/bin/omaprofile` and `chromium.desktop` if they changed. Updating
from 0.2.x puts the web app launchers it rewrote back to Omarchy's form and
removes its `require("hypr.omaprofile")` line and hook file from
`~/.config/hypr/`.

### A theme per profile
Omarchy's Chromium theme is a mandatory policy, so every profile looks the
same. As a recommended policy it only applies when a profile first starts;
after that each profile can pick its own theme ("Customize Chromium" on a new
tab), which makes profiles easy to tell apart.
```sh
omaprofile policy             # mandatory or recommended
omaprofile policy recommend   # make it recommended (asks for your password)
omaprofile policy restore     # make it mandatory again
```
Recommended, it no longer follows Omarchy theme changes. Chrome, Edge and Brave
are handled the same way where Omarchy set them up. If omaprofile made the
change, `omaprofile uninstall` reverts it.

### Uninstall
```sh
omarchy plugin remove dry.omaprofile
```
As the overlay unloads it sees its folder go, and a small floating terminal
asks whether to undo omaprofile's changes (j/k or arrows, Enter; run from a
copy of the CLI that install keeps in `~/.local/share/omaprofile/cleanup/`).
**Undo its changes**
restores the link handlers recorded at install
(Omarchy's own `omarchy-browser.desktop` routing included), removes
omaprofile's `chromium.desktop` and `~/.local/bin/omaprofile`, and makes Omarchy's theme policy mandatory again if
omaprofile changed it; the config stays. **Leave them for now** (or Esc)
changes nothing: links open in the browser directly, and the next link after
the next login asks again. The same goes if the shell was not running to see
the removal.
`omaprofile uninstall` does all of that but keeps the plugin, disabled.
Where install recorded nothing, Omarchy's Hyprland list goes back to
`omarchy-browser.desktop` and `mimeapps.list` to `chromium.desktop`; pass
another desktop id for the latter: `omaprofile uninstall brave-browser.desktop`.

### From a git checkout (development)
```sh
git clone https://github.com/llstrk/omaprofile.git ~/Work/omaprofile
~/Work/omaprofile/bin/omaprofile install
```
Run from any other folder, `install` copies the overlay into the plugins
folder instead and links the CLI to the checkout; `git pull` updates the CLI,
re-run `install` to update the overlay. `uninstall` then removes the copied
overlay too. If the plugins folder already holds a clone made by
`omarchy plugin add`, install refuses and points at that clone instead.

## Troubleshooting
Links opened from other apps have no terminal, so errors (bad config, the
shell not running, a rule naming a missing profile) show up as a
notification. Run the same command in a terminal to see the message there,
e.g. `omaprofile https://example.com`. A rule whose profile no longer
exists is skipped with a notification and the picker is shown instead.

## Layout
- `bin/omaprofile` — launcher; `~/.local/bin/omaprofile` is a script that
  runs it, falling back to the browser if it is gone.
- `omaprofile/` — Python package, stdlib only:
  `cli.py` dispatch, `browser.py` profiles/rules/launch/default-browser,
  `extensions.py` extension sync, `contexts.py` config-defined contexts,
  `picker.py` overlay round trip, `config.py` paths and defaults,
  `install.py` deployment, `policy.py` Omarchy's Chromium policy.
- `manifest.json` — the `dry.omaprofile` plugin manifest, at the root so
  `omarchy plugin add` can install the repository as is.
- `plugin/Omaprofile.qml` — the picker overlay.
- `tests/` — `python3 -m unittest discover -s tests` runs everything,
  including the feature scenarios. All tests run in a throwaway home
  (`tests/sandbox.py`).
- `features/` — behaviour specs in Gherkin, run by a small stdlib runner
  (`tests/gherkin.py`) against the real CLI in a git clone, with Omarchy's
  commands faked. `python3 tests/gherkin.py` prints each step's result.
  Scenarios tagged `@nest` are checked by eye in an omadev nest.
- Config: `~/.config/omaprofile/config.json`.
- Cleanup copy: `~/.local/share/omaprofile/cleanup/`, the CLI kept outside
  the plugin folder so removing the plugin can still uninstall.
- Install record: `~/.local/state/omaprofile/install.json` (previous link
  handlers, restored by `uninstall`); `setup-declined` next to it after
  "Don't ask again".
- Desktop entry: `~/.local/share/applications/omaprofile.desktop`
  (default for http/https/text/html/about/unknown).

## Commands
Same grammar as `omarchy <group> <action>`.
```
omaprofile browser open [flags...] [url]     pick a profile, open the link
omaprofile browser launch <profile> [args]   open as a profile, no picker
omaprofile browser profiles                  list profiles
omaprofile webapp open <url> [flags...]      pick a profile, open as a web app
omaprofile chromium [args...]                Omarchy's chromium.desktop: --app=URL picks
                                             a profile, anything else starts Chromium
omaprofile extensions [sync]                 pick a profile (or all), sync extensions
omaprofile extensions sync all|<profile>     sync from the first profile, no picker
omaprofile extensions list                   first profile's extensions vs the others
omaprofile <context> open [args...]          pick a choice, run its command
omaprofile <context> list                    list the context's choices
omaprofile contexts                          list configured contexts
omaprofile pick [--title T] [--subtitle S] CHOICE...
                                             generic picker; prints chosen id
                                             CHOICE = id|label|color|glyph|detail
omaprofile install                           plugin, default browser, web app route
omaprofile install --auto                    the overlay's call: ask to install if needed
omaprofile uninstall [fallback.desktop]      undo install, restore the previous browser
omaprofile policy [recommend|restore]        Omarchy's Chromium theme policy: show,
                                             make it recommended, make it mandatory again
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
  (`fnmatch`), `host` matched against the link's host (case-insensitive),
  `url` against the full URL. A rule needs a `profile` and at least one of
  `host`/`url`. `profile` is a display name (`Work`) or directory (`Profile 1`).
- `browser.command` is the browser binary; profiles are read from
  `~/.config/chromium/Local State` unless `browser.configDir` says otherwise.
- A context's choice needs an `id` and a `command`; the command runs
  through `bash -c` with any extra arguments from
  `omaprofile <context> open ...` appended. Optional keys per choice:
  `label`, `detail`, `color`, `glyph`. The default `terminal` context uses
  Omarchy's own `tmux` and `herdr` launchers.
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
- Web apps: Omarchy starts every web app (launchers, keybindings, and
  launch-or-focus) with `omarchy-launch-webapp`. With a default browser it
  does not know, omaprofile among them, that runs the first word of
  `chromium.desktop`'s Exec with `--app=<url>`, and looks in
  `~/.local/share/applications/` first. omaprofile's `chromium.desktop` there
  is Chromium's own entry with that first word replaced by
  `~/.local/bin/omaprofile chromium`: `--app=<url>` picks a profile and opens
  the web app in it, anything else (the Chromium menu entry, or Chromium as
  the handler of a file type) starts Chromium unchanged, and New Window and
  New Incognito Window still start Chromium directly. Web apps you add later
  need nothing extra, and no launcher or Hyprland file is edited.
  `omaprofile uninstall` removes that entry.
  Browser rules apply to web apps as well, so a `host` rule fixes an app to
  a profile without the picker.
- Round trip with the overlay is file based like `omarchy.image-picker`:
  the CLI passes `selectionFile`/`doneFile` in the summon payload and waits
  for `doneFile` to appear. Without a running shell it fails with an error.

## Extensions
The first profile (`Default`) is the source. `omaprofile extensions` opens
the picker with "All profiles" plus every other profile, each showing what it
is missing; choosing one opens the Chrome Web Store page of each missing
extension in that profile, and "Add to Chromium" finishes it. Chromium signs
extension settings in `Preferences`, so they cannot be written in directly.
Only Web Store installs are compared: Omarchy's `--load-extension` ones,
component and policy extensions already reach every profile. Extra
extensions in other profiles are left alone.
The browser profile picker also ends with a "Manage extensions" row that
opens the same sync picker; when a link was waiting, the profile picker
comes back afterwards so the link still opens. Bind it with e.g.
`omaprofile extensions` in `~/.config/hypr/bindings.lua`.
