Feature: MiniMax H3 video generation

  Scenario: Faltoobot downloads a completed OpenRouter video
    Given a fake OpenRouter video service
    When I generate a video with the Faltoobot command
    Then the H3 request contains the selected video options
    And the generated MP4 is saved
