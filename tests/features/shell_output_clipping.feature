Feature: Long shell output

  Scenario: A long failing test run keeps its summary
    Given a command prints a long log and ends with a test summary
    When the assistant runs it with the shell tool
    Then the output keeps the start and the test summary
    And the output says how much was cut from the middle
