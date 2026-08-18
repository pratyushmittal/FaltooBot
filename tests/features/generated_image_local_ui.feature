Feature: Generated image local UI

  Scenario: Generated images are saved and shown in local transcript
    Given a Faltoochat session with a mocked generated image response
    When I ask to generate an image for the local UI
    Then the generated image is saved in the workspace
    And the streamed answer includes a generated image markdown link
    And the completed OpenAI response does not include the generated image markdown
    And the chat history places each visible developer image message after its image call

  Scenario: Image-only responses create a visible developer message
    Given a Faltoochat session with a mocked image-only response
    When I ask to generate an image for the local UI
    Then the generated image is saved in the workspace
    And the streamed answer includes a generated image markdown link
    And the latest chat history item is a visible developer image message

  Scenario: Multiple generated images get separate developer messages
    Given a Faltoochat session with a mocked multiple generated image response
    When I ask to generate an image for the local UI
    Then the chat history places each visible developer image message after its image call
