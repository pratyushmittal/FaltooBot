Feature: WhatsApp image generation

  Scenario: User receives the image in the same WhatsApp response
    Given a fake WhatsApp agent
    When I ask WhatsApp to create an image of a cat
    Then I receive an image of a cat
