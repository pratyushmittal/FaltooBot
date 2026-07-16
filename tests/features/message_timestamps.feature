Feature: Message timestamps

  Scenario: New messages are saved with timestamps
    Given a Faltoochat session with a mocked text response
    When I ask a question
    Then the session's messages.json contains timestamps for my question and the response
