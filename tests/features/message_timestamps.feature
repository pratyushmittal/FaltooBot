Feature: Message timestamps

  Scenario: Local transcript stores timestamp keys for new messages
    Given a Faltoochat session with a mocked text response
    When I ask a timestamped question
    Then the local transcript stores timestamp keys for the new messages

  Scenario: User message timestamps are stripped before OpenAI
    Given a saved user text message with a timestamp
    When the history is trimmed for OpenAI
    Then the timestamp key is stripped from the user message sent to OpenAI

  Scenario: Assistant message timestamps are stripped before OpenAI
    Given a saved assistant text message with a timestamp
    When the history is trimmed for OpenAI
    Then the timestamp key is stripped from the assistant message sent to OpenAI

  Scenario: Image-only message timestamps are stripped before OpenAI
    Given a saved image-only message with a timestamp
    When the history is trimmed for OpenAI
    Then the timestamp key is stripped without adding text to the image message

  Scenario: Timestamp stripping does not mutate saved messages
    Given a saved user text message with a timestamp
    When the history is trimmed for OpenAI twice
    Then both trimmed histories match without changing the saved message
