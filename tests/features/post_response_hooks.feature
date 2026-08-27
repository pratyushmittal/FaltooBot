Feature: Post-response hooks

  Scenario: Incremental diff only includes changes after the snapshot
    Given a git workspace with an initial page
    And the user changed the page before the snapshot
    And I capture a post-response snapshot
    When the assistant changes the page and creates a new file
    Then the incremental diff contains only the assistant changes

  Scenario: Incremental diff works before the first commit
    Given a git workspace with no commits
    And I capture a post-response snapshot
    When the assistant creates a new file
    Then the incremental diff contains the new file

  Scenario: Incremental hook diff is empty after switching branches
    Given a git workspace with an initial page
    When the assistant switches branches
    Then the incremental hook diff is empty

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

  Scenario: Hook trigger returns review feedback
    Given a hook file named "Check HTML"
    And the structured hook trigger selects the hook
    And the hook review returns "Use semantic HTML."
    And HTML changes exist after a post-response snapshot
    When I run the hooks
    Then the hook is triggered
    And the hook feedback is "Use semantic HTML."

  Scenario: Hook review finds no important feedback
    Given a hook file named "Check HTML"
    And the structured hook trigger selects the hook
    And the hook review returns "No refactoring is needed. <NO_MAJOR_CHANGES>"
    And HTML changes exist after a post-response snapshot
    When I run the hooks
    Then the hook is triggered without feedback

  Scenario: Hook trigger skips the configured prompt
    Given a hook file named "Check HTML"
    And the structured hook trigger selects no hooks
    And HTML changes exist after a post-response snapshot
    When I run the hooks
    Then the hook is skipped
    And no hook feedback is returned

  Scenario: Hook triggers are checked together
    Given two hook files
    And the structured hook trigger selects only the second hook
    And HTML changes exist after a post-response snapshot
    When I run the hooks
    Then batched hook statuses show one skipped and one triggered
    And trigger is called once

  Scenario: Hook review uses the main session and saves its turn
    Given a fake hook review stream
    When the hook review runs
    Then it uses the main model and session context
    And the hook turn is saved separately

  Scenario: Hook trigger uses structured Responses output
    Given a fake structured Responses client
    When the structured hook trigger runs
    Then it requests structured Responses input

  Scenario: Hook trigger retries an unknown hook name
    Given a fake structured Responses client
    And the parsed trigger returns an unknown hook first
    When the structured hook trigger runs
    Then it retries with a correction for the unknown hook

  Scenario: Streaming answer reports a skipped hook
    Given a hook-enabled session with one hook
    And the incremental diff is "diff"
    And the hook run result is skipped
    When the assistant answer is streamed
    Then hook status events are running and skipped

  Scenario: Streaming answer reruns after hook feedback
    Given a hook-enabled session with one hook
    And the incremental diff is "diff"
    And the hook review returns feedback "fix it"
    When the assistant answer is streamed
    Then the assistant is rerun with the hook feedback
