Feature: Translating Claude Code's output

  Scenario: A recorded tool turn becomes a run_shell_call and its output
    When I translate the recorded tool turn
    Then the events include a run_shell_call item with the command "echo hi"
    And the events include a function_call_output with "hi"

  Scenario: A recorded text turn streams its answer, thinking and rate limits
    When I translate the recorded text turn
    Then the streamed answer is "pong"
    And a thinking block was opened
    And the five-hour rate limit is reported

  Scenario: Queued user messages and developer notes travel as one prompt
    Given a pending history of user "first", developer "clean up", user "second"
    When I render the prompt
    Then the prompt contains "first" and "second"
    And the developer note is wrapped in a system-reminder block

  Scenario: Attachments are referenced where they are, never copied
    When I reference an attachment inside the workspace and one outside it
    Then the inside one is referenced by its relative path
    And the outside one is referenced by its absolute path
    And nothing was copied into the workspace

  Scenario: The transcript reader keeps the conversation and drops Claude Code bookkeeping
    Given a Claude transcript with a skill body, a compaction summary, a slash command, an interrupt note, a task notification and a harness note
    When I read the transcript
    Then the user turns are "hello" and the background update for "tests passed"
    And the assistant answer "hi there" is kept

  Scenario Outline: WhatsApp sends the reply of a turn Claude started
    When Claude finishes its own turn with "<reply>" in a WhatsApp chat
    Then WhatsApp sent <sent>

    Examples:
      | reply                | sent                            |
      | background finished  | "background finished"           |
      | [noreply]            | nothing                         |
