Feature: Message timestamps

  Scenario: Local transcript stores timestamps for new messages
    Given a Faltoochat session with a mocked text response
    When I ask a timestamped question
    Then the local transcript stores timestamps for the new messages

  Scenario: Text message timestamps are sent as model-visible text
    Given a saved text message with a timestamp
    When the history is trimmed for OpenAI
    Then the timestamp is included in the text sent to OpenAI

  Scenario: Image-only messages are not given synthetic text blocks
    Given a saved image-only message with a timestamp
    When the history is trimmed for OpenAI
    Then no timestamp text block is added to the image message

  Scenario: Timestamp text is reproduced from stored metadata
    Given a saved text message with a timestamp
    When the history is trimmed for OpenAI twice
    Then both trimmed histories match without changing the saved message
