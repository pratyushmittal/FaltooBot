Feature: Post-response hooks

  Scenario: Incremental diff only includes changes after the snapshot
    Given a git workspace with an initial page
    And the user changed the page before the snapshot
    When I capture a post-response snapshot
    And the assistant changes the page and creates a new file
    Then the incremental diff contains only the assistant changes

  Scenario: Incremental diff works before the first commit
    Given a git workspace with no commits
    When I capture a post-response snapshot
    And the assistant creates a new file
    Then the incremental diff contains the new file

  Scenario: Manual all hook diff includes staged and unstaged changes
    Given a git workspace with staged and unstaged changes
    When I build the hook diff for "all"
    Then the hook diff contains "cached-change.txt"
    And the hook diff contains "worktree-change.txt"

  Scenario: Manual unstaged hook diff skips staged changes
    Given a git workspace with staged and unstaged changes
    When I build the hook diff for "unstaged"
    Then the hook diff does not contain "cached-change.txt"
    And the hook diff contains "worktree-change.txt"

  Scenario: Hooks load from global and project scopes
    Given global and project hook files
    When I load hooks for the workspace
    Then the loaded hooks are "Global,Project"

  Scenario: Hook trigger returns feedback
    Given a hook file named "Check HTML" with model "hook-model"
    And the structured hook trigger returns true
    When I run the hook
    Then the hook is triggered
    And the hook feedback is "feedback"
    And only the trigger uses a structured response format

  Scenario: Hook trigger skips review
    Given a hook file named "Check HTML" with model "hook-model"
    And the structured hook trigger returns false
    When I run the hook
    Then the hook is skipped
    And review is not called

  Scenario: Hook review receives transcript context
    Given a hook file named "Check HTML" with model "config-model"
    And the structured hook trigger returns true
    And transcript context exists
    When I run the hook
    Then review receives the transcript context

  Scenario: Hook triggers are checked together
    Given two hook files
    And the structured hook trigger skips the first hook and runs the second hook
    When I run the hook
    Then batched hook statuses show one skipped and one triggered
    And trigger is called once

  Scenario: Hook sub-agent calls Responses API directly
    Given a fake streaming Responses client
    When the structured hook sub-agent runs
    Then it requests streaming structured Responses input

  Scenario: Streaming answer reports a skipped hook
    Given a hook-enabled session with one hook
    And the incremental diff is "diff"
    And the hook run result is skipped
    When the assistant answer is streamed
    Then hook status events are running and skipped

  Scenario: Streaming answer reruns after hook feedback
    Given a hook-enabled session with one hook
    And the incremental diff is "diff"
    And the hook run result has feedback "fix it"
    When the assistant answer is streamed
    Then the assistant is rerun after hook feedback
