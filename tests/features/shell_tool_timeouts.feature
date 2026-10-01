Feature: Shell commands in headless sessions

  Scenario: A search without a path finishes when faltoochat runs headless
    Given faltoochat runs with stdin connected to an open pipe
    When the assistant searches the workspace with rg and no path
    Then the search returns the match without timing out

  Scenario: A Python script that times out keeps what it printed
    When the assistant runs a Python script that prints and then hangs
    Then the timed-out output still shows what the script printed
