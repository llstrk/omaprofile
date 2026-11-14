Feature: Knowing about updates and updating from omaprofile
  omaprofile tells people when a new version is out, with an icon in the bar
  and a line in the picker, and updates itself through Omarchy's plugin
  updater, which keeps its fast-forward, validation and rollback.

  The steps run the real omaprofile from a git clone of a local test
  repository, with Omarchy's commands and notifications replaced by fakes that
  record what they were asked to do. Steps about the bar icon and the picker
  check what the QML is given (the status the widget binds to, the picker's
  payload). Scenarios tagged @nest are about what is drawn on screen: they are
  checked by eye in an omadev nest and recorded for the pull request.

  Background:
    Given omaprofile 0.3.0 was installed with "omarchy plugin add"
    And omaprofile is set up
    And the update icon is on the bar

  Rule: omaprofile checks for a new version once a day, in the background

    Scenario: A new version is out
      Given version 0.4.0 is published
      When the daily check runs
      Then the bar shows the update icon with "omaprofile 0.4.0 available"

    Scenario: Nothing new
      When the daily check runs
      Then the bar does not show the update icon

    Scenario: The check runs at most once a day
      Given the last check was 2 hours ago
      And version 0.4.0 is published
      When the daily check runs
      Then the bar does not show the update icon

    Scenario: A day later it checks again
      Given the last check was 25 hours ago
      And version 0.4.0 is published
      When the daily check runs
      Then the bar shows the update icon with "omaprofile 0.4.0 available"

    Scenario: Offline, the daily check stays quiet and remembers what it knew
      Given version 0.4.0 was found by an earlier check
      And the repository cannot be reached
      When the daily check runs
      Then no error is shown
      And the bar shows the update icon with "omaprofile 0.4.0 available"

    Scenario: Update checks can be turned off
      Given the config has update checks turned off
      And version 0.4.0 is published
      When the daily check runs
      Then the bar does not show the update icon

    Scenario: Checking by hand says what it found
      Given version 0.4.0 is published
      When I run "omaprofile update --check"
      Then it prints "omaprofile 0.4.0 is available (you have 0.3.0)"

    Scenario: Checking by hand when up to date
      When I run "omaprofile update --check"
      Then it prints "omaprofile 0.3.0 is up to date"

    Scenario: Checking by hand while offline says so
      Given the repository cannot be reached
      When I run "omaprofile update --check"
      Then it fails with "could not reach"

  Rule: The picker mentions an update that is waiting

    Scenario: The picker's footer
      Given version 0.4.0 was found by an earlier check
      When a link opens the profile picker
      Then the picker's footer says "0.4.0 available · U to update"

    Scenario: No footer when up to date
      When a link opens the profile picker
      Then the picker has no footer

  Rule: A notification only for people without the icon, once per version

    Scenario: With the icon on the bar there is no notification
      Given version 0.4.0 is published
      When the daily check runs
      Then no notification is shown

    Scenario: Without the icon, a notification says so
      Given the update icon is not on the bar
      And version 0.4.0 is published
      When the daily check runs
      Then a notification says "omaprofile 0.4.0 is available" with the buttons "Update" and "Not now"

    Scenario: The same version is not announced twice
      Given the update icon is not on the bar
      And version 0.4.0 is published
      And the daily check ran yesterday
      When the daily check runs
      Then no notification is shown

    Scenario: A newer version is announced again
      Given the update icon is not on the bar
      And version 0.4.0 is published
      And the daily check ran yesterday
      And version 0.4.1 is published
      When the daily check runs
      Then a notification says "omaprofile 0.4.1 is available" with the buttons "Update" and "Not now"

    Scenario: Update on the notification opens the updater
      Given the update icon is not on the bar
      And version 0.4.0 is published
      And notification buttons get clicked: "Update"
      When the daily check runs
      Then a floating terminal opens running "omaprofile update"

  Rule: Updating asks first, then goes through Omarchy's updater

    Scenario: Clicking the bar icon opens the updater
      Given version 0.4.0 was found by an earlier check
      When I click the update icon
      Then a floating terminal opens running "omaprofile update"

    Scenario: Updating shows what's new, then updates
      Given version 0.4.0 is published with the changes:
        | Profiles can be pinned to a site                  |
        | The picker remembers the last profile per web app |
      And version 0.4.0 was found by an earlier check
      When I run "omaprofile update" and answer "y"
      Then it prints "omaprofile 0.3.0 → 0.4.0"
      And it prints "Profiles can be pinned to a site"
      And it prints "The picker remembers the last profile per web app"
      And Omarchy's plugin updater was run for dry.omaprofile
      And it prints "Updated to 0.4.0"
      And omaprofile 0.4.0 is installed
      And the bar does not show the update icon

    Scenario: Changes from every version in between are listed
      Given version 0.3.1 is published with the changes:
        | Fix the picker on vertical bars |
      And version 0.4.0 is published with the changes:
        | Profiles can be pinned to a site |
      When I run "omaprofile update" and answer "n"
      Then it prints "Fix the picker on vertical bars"
      And it prints "Profiles can be pinned to a site"

    Scenario: Saying no changes nothing
      Given version 0.4.0 was found by an earlier check
      When I run "omaprofile update" and answer "n"
      Then Omarchy's plugin updater was not run
      And omaprofile 0.3.0 is installed
      And the bar shows the update icon with "omaprofile 0.4.0 available"

    Scenario: Omarchy rolls a failed update back
      Given version 0.4.0 was found by an earlier check
      And Omarchy's plugin updater will reject the update
      When I run "omaprofile update" and answer "y"
      Then it fails with "Update failed"
      And omaprofile 0.3.0 is installed
      And the bar shows the update icon with "omaprofile 0.4.0 available"

    Scenario: Already up to date
      When I run "omaprofile update"
      Then it prints "omaprofile 0.3.0 is up to date"
      And Omarchy's plugin updater was not run

    Scenario: Hiding the icon until the next version
      Given version 0.4.0 was found by an earlier check
      When I right-click the update icon
      Then the bar does not show the update icon
      When version 0.4.1 is published
      And the daily check runs a day later
      Then the bar shows the update icon with "omaprofile 0.4.1 available"

    Scenario: An install from a source checkout updates with git
      Given omaprofile runs from a source checkout instead
      And version 0.4.0 is published
      When I run "omaprofile update"
      Then it prints "git pull"
      And Omarchy's plugin updater was not run

  Rule: Existing installs get the icon with their next update

    Scenario: Updating from 0.2.0 places the icon and says what's new
      Given the update icon is not on the bar
      And omaprofile was updated from 0.2.0
      When the plugin reloads
      Then the update icon is put on the right of the bar, unless the user placed it already
      And a notification says "omaprofile 0.3.0 installed"

    Scenario: The "installed" notification is shown once
      Given omaprofile was updated from 0.2.0
      And the plugin reloaded and said "omaprofile 0.3.0 installed"
      When the plugin reloads
      Then no notification is shown

  Rule: New installs offer the icon from the start

    Scenario: Omarchy suggests the right of the bar for the icon
      Then the plugin manifest declares a bar widget that goes on the right by default

    Scenario: The setup prompt mentions the icon
      Given omaprofile has not been set up yet
      When the plugin loads
      Then the setup prompt says "shows an icon in the bar when an update is out"

  Rule: One version number, with release notes

    Scenario: The version is the same everywhere
      Then manifest.json, pyproject.toml and the newest CHANGELOG.md entry name the same version

  Rule: What is drawn on screen (checked in an omadev nest)

    @nest
    Scenario: The icon sits on the right and leaves no gap while hidden
      Given the update icon is on the bar
      When there is no update
      Then the bar looks exactly as it did before omaprofile was installed
      When version 0.4.0 is found
      Then an update icon appears on the right of the bar, with the tooltip "omaprofile 0.4.0 available"

    @nest
    Scenario: The icon appears after "omarchy plugin update" without restarting the shell
      Given omaprofile 0.2.0 is installed
      When the user runs "omarchy plugin update dry.omaprofile"
      Then the update icon is on the bar without a shell restart

    @nest
    Scenario: Pressing U in the picker opens the updater
      Given version 0.4.0 was found by an earlier check
      When a link opens the profile picker and the user presses U
      Then the picker closes and a floating terminal runs "omaprofile update"

    @nest
    Scenario: Removing only the icon keeps the picker working
      When the user removes the update icon from the bar
      Then links still open the profile picker
