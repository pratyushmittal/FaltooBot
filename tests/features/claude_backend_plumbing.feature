Feature: Backend selection per session

  Scenario: Existing sessions without a backend keep using OpenAI
    Given a saved session file without a backend key
    When I load the session
    Then the session backend is "openai"

  Scenario: New sessions take the configured default backend
    Given config.toml sets the llm backend to "claude"
    When I create a new session
    Then the session backend is "claude"
    And the saved messages.json keeps the backend key

  Scenario: Update pins an explicit backend on sessions that predate backends
    Given a saved session file without a backend key
    When the update migrations run
    Then the saved messages.json keeps the backend key
    And the session file's modification time is unchanged
    And running the migrations again changes nothing

  Scenario: Changing the config does not move an existing session
    Given a saved session file with backend "openai"
    And config.toml sets the llm backend to "claude"
    When I load the session
    Then the session backend is "openai"

  Scenario: A Claude session streams through the Claude bridge
    Given a saved session file with backend "claude"
    And the Claude bridge is faked
    And the OpenAI stream is faked
    When I ask a question
    Then the Claude bridge answered
    And the OpenAI stream was not used

  Scenario: An OpenAI session never touches the Claude bridge
    Given a saved session file with backend "openai"
    And the Claude bridge is faked
    And the OpenAI stream is faked
    When I ask a question
    Then the OpenAI stream answered
    And the Claude bridge was not used

  Scenario: Config renders the new sections with validated defaults
    Given a config with an invalid claude effort and a negative idle timeout
    When I render the config
    Then the rendered config has the llm_backend key and the claude section
    And the claude effort is "medium" and the idle timeout is 0
