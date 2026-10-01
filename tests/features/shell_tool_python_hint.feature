Feature: Python hint in the shell tool

  Scenario: The shell tool says to use uv on machines without python
    Given the machine has python3 but no python command
    When the assistant gets the shell tool
    Then the tool description says to use uv run python

  Scenario: The shell tool stays quiet on machines with python
    Given the machine has a python command
    When the assistant gets the shell tool
    Then the tool description does not mention a missing python command
