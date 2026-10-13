Feature: Claude CLI bridge against the real CLI

  Scenario: Memory carries across turns and a Bash call is reported
    Given a real Claude session on haiku
    When I ask it to remember "mango", then ask which word, then ask it to echo "smoke-ok"
    Then the second reply mentions "mango"
    And the third reply mentions "smoke-ok"
    And the third turn reported a run_shell_call with "echo smoke-ok"
    And the history holds only user turns and 3 turn markers
    And the transcript reader finds the conversation

  Scenario: A turn Claude starts after background work does not shift later replies
    Given a real Claude session on haiku with a background-turn handler
    When I ask it to run "sleep 8; echo BG-DONE" in the background and wait for its own turn
    And I ask it to reply exactly "second"
    Then the handler received a reply
    And the last reply mentions "second"
