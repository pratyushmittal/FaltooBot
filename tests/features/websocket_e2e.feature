Feature: OpenAI websocket streaming

  Scenario Outline: <auth> websocket prewarm streams a follow-up
    Given Config has <auth> and websocket=true
    When I start a Faltoochat session
    Then websocket session gets warmed up
    And websocket session has a previous_response_id
    When I ask the assistant to reply with "FALTOO_WS_ONE"
    And I stream the answer
    Then the latest assistant answer contains "FALTOO_WS_ONE"
    When I ask the assistant to reply with "FALTOO_WS_TWO"
    And I stream the answer
    Then the latest assistant answer contains "FALTOO_WS_TWO"
    And the websocket session kept response state across turns

    Examples:
      | auth           |
      | OpenAI API key |
      | Codex OAuth    |

  Scenario Outline: <auth> websocket input token cache increases after long skill context
    Given Config has <auth> and websocket=true
    And workspace has a large crons skill
    When I start a Faltoochat session
    And I ask the assistant to read the crons skill
    And I stream the answer
    Then the latest assistant answer contains "CRONS_SKILL_READ"
    And the latest usage has total tokens more than 2000
    When I say thanks
    And I stream the answer
    Then the latest assistant answer is not empty
    And the input cache tokens should have never fallen in the full messages history
    When I restart the current Faltoochat session
    And I say "what was my previous message"
    And I stream the answer
    Then the latest assistant answer contains "thanks"
    And the input cache tokens should have never fallen in the full messages history

    Examples:
      | auth           |
      | OpenAI API key |
      | Codex OAuth    |

  Scenario: Wrong API key websocket prewarm fails without retries
    Given Config has wrong OpenAI API key and websocket=true
    When I start a Faltoochat session
    Then websocket auth error is raised
    And websocket prewarm is not retried
