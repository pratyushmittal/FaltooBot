Feature: Driving the claude process

  Scenario: One process serves several turns
    Given a Claude session
    When I ask "hello" and then "again"
    Then the replies are "echo: hello" and "echo: again"
    And claude was started once, with a new conversation id
    And the history holds only user turns and 2 turn markers
    And the last usage reports total tokens
    And the displayed history includes Claude's answer from its transcript

  Scenario: The system prompt, skills and flags reach Claude
    Given a Claude session
    When I ask "hello" and then "again"
    Then the system prompt Claude received contains the chat instructions and the chat key
    And the rendered skills include "background-notifications" with the chat key substituted
    And claude was started with --replay-user-messages and no disallowed tools

  Scenario: Cancelling a turn interrupts Claude and the process survives
    Given a Claude session
    When I cancel a "slow" turn and then ask "hello"
    Then the last reply is "echo: hello"
    And claude was started once, with a new conversation id
    And the history has an interrupted turn marker

  Scenario: A crash is reported and the next turn resumes the conversation
    Given a Claude session
    When I ask "hello", then "crash now", then "again", tolerating the crash
    Then the crash error mentions "fake claude crashed"
    And the last reply contains "crash now" and "again"
    And claude was started twice, the second time resuming the same conversation

  Scenario: Compaction is a turn on the live process
    Given a Claude session
    When I ask "hello", compact, and ask "again"
    Then compaction succeeded
    And the replies are "echo: hello" and "echo: again"

  Scenario: The idle timeout closes the process and the next turn resumes
    Given a Claude session with an idle timeout of 0 seconds
    When I ask "hello" and then "again"
    Then claude was started twice, the second time resuming the same conversation

  Scenario: A tool result larger than asyncio's default line limit is read
    Given a Claude session
    When I ask "big output" and then "hello"
    Then the replies are "big done" and "echo: hello"

  Scenario: Skills are synced, not rewritten, when the process restarts
    Given a Claude session
    And a workspace skill "temp-skill" exists
    When I ask "hello", the process stops, the workspace skill "temp-skill" is removed and I ask "again"
    Then the bundled skill files were not rewritten on restart
    And the rendered skill "temp-skill" is gone

  Scenario: Finishing a one-shot session waits for claude to exit
    Given a Claude session
    When I ask "hello" and finish the session
    Then no Claude process is left running

  Scenario: A turn that ends in an error is not sent again
    Given a Claude session
    When I ask "fail please", tolerating the error, and then ask "hello"
    Then the last reply is "echo: hello"

  Scenario: The interrupted note survives the cancelled turn
    Given a Claude session
    When I cancel a "slow" turn the way the TUI does
    Then the history ends with the interrupted note

  Scenario: Compaction keeps a pending note for the next turn
    Given a Claude session
    When Claude has answered "hello" and a developer note "remember tea" is pending
    And I compact and then ask "again"
    Then the last reply contains "remember tea" and "again"

  Scenario: A newer Claude conversation in the same workspace does not take over
    Given a Claude session
    When I ask "hello", another Claude run in the workspace writes a newer conversation, and I ask "again"
    Then claude was started twice, the second time resuming the same conversation
    And the displayed history shows "echo: hello" and not the other conversation

  Scenario: Asking again with nothing pending is a quiet no-op
    Given a Claude session
    When I ask "hello" and then ask for an answer with nothing pending
    Then the last reply is empty
    And the history holds only user turns and 1 turn markers
