Feature: Seeing and changing rules in a terminal
  Rules open a site's links in one profile without asking. Shift+Enter in the
  picker adds them; anything else meant editing config.json by hand. Running
  omaprofile in a terminal now opens an editor that lists the rules in the
  order they are tried and adds, changes, moves and deletes them.

  Keys in the editor: j/k or the arrows move, Enter changes the rule under the
  cursor (or adds one on the "+ Add a rule" row), a adds, d deletes after a y,
  J and K move the rule down and up, q or Esc quits. Typing a pattern, Enter
  goes on and Esc cancels. Every change is saved to config.json right away.

  The steps run the real omaprofile in a pseudo-terminal, as in the other
  features, and read what the editor shows from a copy of its screen that
  it writes when OMAPROFILE_SCREEN_FILE is set. Scenarios tagged
  @nest are about how it looks and are checked in an omadev nest.

  Background:
    Given omaprofile 0.4.0 was installed with "omarchy plugin add"
    And omaprofile is set up
    And Chromium has the profiles "Personal" and "Work"

  Rule: omaprofile in a terminal opens the editor; without one it still opens the browser

    Scenario: Running omaprofile in a terminal
      Given the config already has the rules:
        | host       | profile |
        | github.com | Work    |
      When I run "omaprofile" in a terminal
      Then the rules editor lists:
        | pattern    | profile | note |
        | github.com | Work    |      |
      And the profile picker is not shown

    Scenario: omaprofile rules opens it too
      When I run "omaprofile rules" in a terminal
      Then the rules editor is shown

    Scenario: Super+Shift+Enter still opens the browser
      When I run "omaprofile" without a terminal
      Then the profile picker is shown

    Scenario: A link still opens the picker from a terminal
      When I run "omaprofile https://example.com" in a terminal
      Then the profile picker is shown

    Scenario: Without a terminal, omaprofile rules says it needs one
      When I run "omaprofile rules" without a terminal
      Then it fails with "needs a terminal"

    Scenario: q quits
      When I run "omaprofile" in a terminal
      Then the rules editor is shown
      When I press "q"
      Then the editor has quit

  Rule: The list shows every rule in the order they are tried

    Scenario: No rules yet
      When I open the rules editor
      Then the rules editor lists no rules
      And the editor says "No rules yet. Shift+Enter in the picker adds one, or press a."

    Scenario: Host and URL rules, and a profile that is gone
      Given the config already has the host and URL rules:
        | host         | url             | profile  |
        | github.com   |                 | Work     |
        |              | https://x.com/* | Personal |
        | gitlab.com   |                 | Old      |
      When I open the rules editor
      Then the rules editor lists:
        | pattern         | profile  | note         |
        | github.com      | Work     |              |
        | https://x.com/* | Personal | url          |
        | gitlab.com      | Old      | profile gone |

    Scenario: omaprofile rules list prints them for scripts
      Given the config already has the rules:
        | host       | profile |
        | github.com | Work    |
        | gitlab.com | Old     |
      When I run "omaprofile rules list"
      Then it prints "1  github.com  Work"
      And it prints "2  gitlab.com  Old  (profile gone)"

  Rule: Adding a rule

    Scenario: A host rule
      When I open the rules editor
      And I add a rule for "docs.rs" with "Personal"
      Then the config has the rules:
        | host    | profile  |
        | docs.rs | Personal |
      And the editor says "Saved: docs.rs opens in Personal"

    Scenario: A pattern with :// is a URL rule
      When I open the rules editor
      And I add a rule for "https://x.com/*" with "Personal"
      Then the config has the rules:
        | url             | profile  |
        | https://x.com/* | Personal |

    Scenario: Hosts are kept in lower case
      When I open the rules editor
      And I add a rule for "Docs.RS" with "Work"
      Then the config has the rules:
        | host    | profile |
        | docs.rs | Work    |

    Scenario: New rules go last
      Given the config already has the rules:
        | host       | profile |
        | github.com | Work    |
      When I open the rules editor
      And I add a rule for "*.github.com" with "Personal"
      Then the config has the rules:
        | host         | profile  |
        | github.com   | Work     |
        | *.github.com | Personal |

    Scenario: A pattern that already has a rule changes that rule
      Given the config already has the rules:
        | host       | profile |
        | github.com | Work    |
        | docs.rs    | Work    |
      When I open the rules editor
      And I add a rule for "github.com" with "Personal"
      Then the config has the rules:
        | host       | profile  |
        | github.com | Personal |
        | docs.rs    | Work     |

    Scenario: A host with a path is refused
      When I open the rules editor
      And I add a rule for "github.com/llstrk" with "Work"
      Then the editor says "A host has no path. For part of a site use a URL pattern, like https://github.com/llstrk/*"
      And the config has no rules

    Scenario: Esc while typing adds nothing
      When I open the rules editor
      And I press "a"
      And I type "docs.rs"
      And I press "Esc"
      Then the config has no rules
      And the rules editor lists no rules

    Scenario: Without a config file, one is written with the defaults and the rule
      Given there is no config file
      When I open the rules editor
      And I add a rule for "docs.rs" with "Personal"
      Then the config has the rules:
        | host    | profile  |
        | docs.rs | Personal |
      And the config has the default terminal context

    Scenario: Before Chromium has made a profile there is nothing to pick
      Given Chromium has no profiles yet
      When I open the rules editor
      And I press "a"
      Then the editor says "Chromium has no profiles yet. Open it once, then add rules."

  Rule: Changing, moving and deleting rules

    Background:
      Given the config has update checks turned off and already has the rules:
        | host       | profile  |
        | github.com | Work     |
        | docs.rs    | Personal |
        | gitlab.com | Old      |

    Scenario: Enter changes the pattern and the profile
      When I open the rules editor
      And I change rule 3 to "gitlab.com" with "Work"
      Then the config has the rules:
        | host       | profile  |
        | github.com | Work     |
        | docs.rs    | Personal |
        | gitlab.com | Work     |
      And the config still has update checks turned off

    Scenario: The pattern starts out as the old one
      When I open the rules editor
      And I move to rule 2
      And I press "Enter"
      Then the pattern being typed is "docs.rs"

    Scenario: The profile starts out as the old one
      When I open the rules editor
      And I move to rule 2
      And I press "Enter"
      And I press "Enter"
      Then the profile under the cursor is "Personal"

    Scenario: J moves a rule down, K moves it up
      When I open the rules editor
      And I move to rule 1
      And I press "J"
      Then the config has the rules:
        | host       | profile  |
        | docs.rs    | Personal |
        | github.com | Work     |
        | gitlab.com | Old      |
      When I press "K"
      Then the config has the rules:
        | host       | profile  |
        | github.com | Work     |
        | docs.rs    | Personal |
        | gitlab.com | Old      |

    Scenario: d deletes after a y
      When I open the rules editor
      And I move to rule 2
      And I press "d"
      Then the editor says "Delete docs.rs → Personal? y/n"
      When I press "y"
      Then the config has the rules:
        | host       | profile |
        | github.com | Work    |
        | gitlab.com | Old     |
      And the config still has update checks turned off

    Scenario: Any other key keeps it
      When I open the rules editor
      And I move to rule 2
      And I press "d"
      And I press "n"
      Then the config has the rules:
        | host       | profile  |
        | github.com | Work     |
        | docs.rs    | Personal |
        | gitlab.com | Old      |

    Scenario: A rule the picker adds while the editor is open is kept
      When I open the rules editor
      And "Work" is picked with Shift+Enter for "https://codeberg.org/x"
      And I move to rule 2
      And I press "d"
      And I press "y"
      Then the config has the rules:
        | host         | profile |
        | github.com   | Work    |
        | gitlab.com   | Old     |
        | codeberg.org | Work    |
      And the rules editor lists:
        | pattern      | profile | note         |
        | github.com   | Work    |              |
        | gitlab.com   | Old     | profile gone |
        | codeberg.org | Work    |              |

  Rule: Omaprofile in the app launcher opens the editor in a floating terminal

    The desktop entry is also the default browser, so links sent to it still
    open the picker. Super+Shift+Enter runs only the entry's first word,
    without a terminal, so it still opens the browser (see above).

    Scenario: Opening Omaprofile from the app launcher
      Given omaprofile's desktop entry is set up
      When Omaprofile is opened from the app launcher
      Then the rules editor opens in a floating terminal
      And the profile picker is not shown

    Scenario: Links sent to the same entry still open the picker
      Given omaprofile's desktop entry is set up
      When the desktop entry opens a link to "https://example.com"
      Then the profile picker is shown
      And no floating terminal opens

    Scenario: The entry's New Window action still opens the picker
      Given omaprofile's desktop entry is set up
      When the desktop entry's "New Window" action runs
      Then the profile picker is shown

    Scenario: An install from 0.4.0 gets the new entry when the plugin reloads
      Given omaprofile's desktop entry is the one 0.4.0 wrote
      When the plugin reloads
      And Omaprofile is opened from the app launcher
      Then the rules editor opens in a floating terminal

    @nest
    Scenario: From Super+Space
      When Omaprofile is picked in the app launcher
      Then the rules editor opens in a floating, centred terminal, like Omarchy's other TUIs
      And q closes it

  Rule: It looks like the rest of Omarchy

    @nest
    Scenario: Theme colours and a hint line
      When I run "omaprofile" in a terminal in an omadev nest
      Then the editor uses the current theme's colours
      And a line under the rules shows the keys
      And a rule whose profile is gone is marked in the theme's warning colour
