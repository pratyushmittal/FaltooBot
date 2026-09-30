Feature: Claude through OpenRouter

  Scenario: Claude keeps its reasoning and prompt cache across a tool call
    Given Config uses an OpenRouter Claude model
    When I ask Claude a hard question that needs the weather tool
    Then the weather tool was called
    And the reasoning in history keeps Claude's signature
    And the request after the tool call reads from the prompt cache
    When I ask which city Claude checked
    Then the latest assistant answer contains "Lucknow"
