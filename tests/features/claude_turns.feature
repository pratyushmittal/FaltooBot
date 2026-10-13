Feature: Telling Claude's turns apart

  Claude echoes each prompt when it takes it. Output after an echo answers that
  prompt; output with no echo before it is a turn Claude started by itself.

  Scenario: Output after the echo answers the prompt
    Given a Claude process with a sent prompt
    When Claude sends: echo, text "hi", result "hi"
    Then the prompt's reply is "hi"
    And the handler received nothing

  Scenario: Output with no echo before it is Claude's own turn
    Given a Claude process with no sent prompt
    When Claude sends: task notification, text "bg", result "bg"
    Then the handler received "bg"

  Scenario: A prompt sent during Claude's own turn is answered after it
    Given a Claude process with a sent prompt
    When Claude sends: text "bg", result "bg", echo, text "hi", result "hi"
    Then the handler received "bg"
    And the prompt's reply is "hi"

  Scenario: Claude takes the prompt in the middle of its own turn
    Given a Claude process with a sent prompt
    When Claude sends: text "bg", echo, text "hi", result "hi"
    Then the handler received ""
    And the prompt's reply is "hi"

  Scenario: Replayed task notifications are not prompt echoes
    Given a Claude process with a sent prompt
    When Claude sends: task notification echo, text "bg", result "bg", echo, text "hi", result "hi"
    Then the handler received "bg"
    And the prompt's reply is "hi"

  Scenario: Sub-agent output never reaches the answer
    Given a Claude process with a sent prompt
    When Claude sends: echo, sub-agent text "LEAKED", text "hi", result "hi"
    Then the prompt's reply is "hi"

  Scenario: A slash command is answered without an echo
    Given a Claude process with a sent slash command
    When Claude sends: result ""
    Then the prompt's reply is ""
    And the handler received nothing

  Scenario: Without a handler, Claude's own replies are kept for the one-shot run
    Given a Claude process with no sent prompt and no handler
    When Claude sends: text "late", result "late"
    Then the kept replies are "late"

  Scenario: The idle stop waits while background work runs
    Given a Claude process with no sent prompt and an idle timeout of 0 seconds
    When Claude sends: background tasks 1
    Then the process is still running after the idle timeout

  Scenario: A turn Claude started can be interrupted
    Given a Claude process with no sent prompt
    When Claude sends: text "bg"
    And I interrupt Claude's own turn
    And Claude sends: result ""
    Then the interrupt request was written to Claude
    And the handler received ""
