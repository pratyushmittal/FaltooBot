Feature: MiniMax H3 OpenRouter integration

  Scenario: OpenRouter generates a real H3 video
    Given OpenRouter video generation is enabled
    When I generate a five second H3 video
    Then OpenRouter returns a playable MP4
