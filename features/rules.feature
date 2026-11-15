Feature: Making a rule from the picker
  A rule opens a site's links in one profile without asking. Instead of
  writing it into config.json by hand, Shift+Enter in the picker opens the
  link in the chosen profile and adds the rule, so the site's links open
  there from then on.

  The steps run the real omaprofile with Omarchy's commands, Chromium and
  notifications replaced by fakes, as in updates.feature. Picking in the
  picker is answering its round trip the way the overlay does; scenarios
  tagged @nest are about the overlay itself and are checked in an omadev nest.

  Background:
    Given omaprofile 0.3.0 was installed with "omarchy plugin add"
    And omaprofile is set up
    And Chromium has the profiles "Personal" and "Work"

  Rule: Shift+Enter opens the link and remembers the profile for its site

    Scenario: The picker offers it for a link
      When a link to "https://github.com/llstrk/omaprofile" opens the profile picker
      Then the picker offers to remember the profile for "github.com"

    Scenario: Remembering a profile
      When "Work" is picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      Then Chromium opens "https://github.com/llstrk/omaprofile" in "Profile 1"
      And the config has the rules:
        | host       | profile |
        | github.com | Work    |
      And a notification says "github.com opens in Work from now on"

    Scenario: The site's next link skips the picker
      Given "Work" was picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      When a link to "https://github.com/notifications" opens
      Then the profile picker is not shown
      And Chromium opens "https://github.com/notifications" in "Profile 1"

    Scenario: The rule is for that host only
      Given "Work" was picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      When a link to "https://gist.github.com/llstrk" opens
      Then the profile picker is shown

    Scenario: Enter alone makes no rule
      When "Work" is picked with Enter for "https://github.com/llstrk/omaprofile"
      Then Chromium opens "https://github.com/llstrk/omaprofile" in "Profile 1"
      And the config has no rules

    Scenario: Rules already there stay first, and the rest of the config is kept
      Given the config has update checks turned off and already has the rules:
        | host  | profile  |
        | x.com | Personal |
      When "Work" is picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      Then the config has the rules:
        | host       | profile  |
        | x.com      | Personal |
        | github.com | Work     |
      And the config still has update checks turned off

    Scenario: Without a config file, one is written with the defaults and the rule
      Given there is no config file
      When "Work" is picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      Then the config has the rules:
        | host       | profile |
        | github.com | Work    |
      And the config has the default terminal context

    Scenario: A rule for a profile that is gone is replaced
      Given the config already has the rules:
        | host       | profile |
        | github.com | Old     |
      When "Work" is picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      Then the config has the rules:
        | host       | profile |
        | github.com | Work    |

    Scenario: Web apps make rules too
      When "Work" is picked with Shift+Enter for the web app "https://app.hey.com"
      Then Chromium opens the web app "https://app.hey.com" in "Profile 1"
      And the config has the rules:
        | host        | profile |
        | app.hey.com | Work    |

  Rule: It is offered only where a rule could match

    Scenario: A new window has no site
      When a new browser window opens the profile picker
      Then the picker does not offer to remember the profile

    Scenario: A file has no host
      When a link to "file:///home/me/page.html" opens the profile picker
      Then the picker does not offer to remember the profile

    Scenario: Shift+Enter on "Manage extensions" only manages extensions
      When "Manage extensions" is picked with Shift+Enter for "https://github.com/llstrk/omaprofile"
      Then the config has no rules

  Rule: The picker shows how

    @nest
    Scenario: A hint under the profiles
      When a link to "https://github.com/llstrk/omaprofile" opens the profile picker
      Then a line under the rows says "Shift+Enter: always open github.com with this profile"
      And with an update waiting, the update line is shown under it

    @nest
    Scenario: Shift+Enter and Shift+click both remember
      When "Work" is picked with Shift+Enter, or with a Shift+click on its row
      Then the link opens in Work and the rule is added
