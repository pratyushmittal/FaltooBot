Feature: Store false image generation replay

  Scenario: Generated image calls are replayed after trimming history
    Given a completed generated image call with response-only metadata
    When the history is trimmed for a follow-up
    Then the generated image call is replayed with only OpenAI input fields

  Scenario: Streamed generated image calls are stored without mutation
    Given a streamed generated image call with a result
    When the response item is stored in history
    Then the stored image call keeps the OpenAI status
