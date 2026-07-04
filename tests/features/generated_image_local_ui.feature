Feature: Generated image local UI

  Scenario: Generated images are saved and shown in local transcript
    Given a Faltoochat session with a mocked generated image response
    When I ask to generate an image for the local UI
    Then the generated image is saved in the workspace
    And the streamed answer includes a generated image markdown link
    And the completed response includes a generated image markdown link
    And the chat history includes a display-only generated image markdown link
    And the chat history includes a developer note with the generated image path

  Scenario: Image-only responses create a local display message
    Given a Faltoochat session with a mocked image-only response
    When I ask to generate an image for the local UI
    Then the generated image is saved in the workspace
    And the streamed answer includes a generated image markdown link
    And the latest chat history item is a display-only generated image markdown link
    And the chat history includes a developer note with the generated image path


  Scenario: Multiple generated images get separate developer notes
    Given a Faltoochat session with a mocked multiple generated image response
    When I ask to generate an image for the local UI
    Then the chat history includes a developer note with the generated image path
