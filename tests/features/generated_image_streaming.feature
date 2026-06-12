Feature: Generated image streaming

  Scenario: Generated image appears across streaming response surfaces
    Given a Faltoochat session with mocked OpenAI stream
    When I ask to generate an image of a cat
    Then the mocked stream includes a generated image markdown link
    And the completed response includes the generated image markdown link
    And the chat history includes the generated image markdown link
