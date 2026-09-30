Feature: Stop WhatsApp responses

  Scenario: Stop and resume work while preserving conversation history
    Given WhatsApp with a blocking model response
    When the user checks status, stops the response, resumes, and stops pending work
    Then commands respond immediately and only the requested responses run
    And tool history is repaired without losing notifications or user prompts

  Scenario Outline: Immediate group commands respect the bot mention
    Given an allowed group with a pending response
    When a stop command mentions "<recipient>"
    Then the pending response is "<outcome>"

    Examples:
      | recipient   | outcome   |
      | 15555550999 | cancelled |
      | 15555550888 | unchanged |
