Feature: Hand-off between backends

  Background:
    Given a WhatsApp chat on OpenAI with an archived history and a live history

  Scenario: An OpenAI chat is handed to Claude
    When I hand the chat off to "claude"
    Then the hand-off document has the OpenAI summary
    And the hand-off note lists the archive before the live history file
    And the chat now uses a new Claude session that starts with the hand-off note
    And the previous session file is unchanged
    And the verification reply came from Claude

  Scenario: A dry run only writes the document
    When I hand the chat off to "claude" as a dry run
    Then the hand-off document exists
    And the chat still uses the previous session

  Scenario: A Claude chat is handed back to OpenAI
    When I hand the chat off to "claude"
    And I hand the chat off to "openai"
    Then the latest hand-off document has the Claude summary
    And the hand-off note lists Claude's transcript
    And the chat now uses an OpenAI session

  Scenario Outline: WhatsApp /handoff
    When the WhatsApp user sends "<command>"
    Then WhatsApp replied "<reply>"

    Examples:
      | command          | reply                                   |
      | /handoff claude  | Handing this chat over to claude...     |
      | /handoff gemini  | Usage: /handoff <openai\|claude>         |
