Feature: Store false image generation replay

  Scenario: Generated image calls are replayed after trimming history
    Given a completed generated image call with response-only metadata
    When the history is trimmed for a follow-up
    Then the generated image call is replayed with only OpenAI input fields

  Scenario: Streamed generated image calls are stored as completed
    Given a streamed generated image call with a result
    When the response item is stored in history
    Then the stored image call is completed

  Scenario: Display-only generated image markdown is not replayed
    Given an assistant message with display-only generated image markdown
    When the history is trimmed for a follow-up
    Then the display-only generated image markdown is omitted
