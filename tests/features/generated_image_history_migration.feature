Feature: Generated image history migration

  Scenario: Old generated image histories get developer path notes
    Given a saved chat history with an old generated image call
    When update migrations run
    Then the update summary includes the generated image developer migration
    And the generated image is saved in the workspace
    And the history includes a developer note with the local image path

  Scenario: Histories that already have generated image developer notes are unchanged
    Given a saved chat history that already has a generated image developer note
    When update migrations run
    Then the update summary is empty
    And the saved chat history is unchanged
