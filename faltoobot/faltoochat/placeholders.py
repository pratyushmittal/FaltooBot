from random import choice

PLACEHOLDERS = [
    "Ask anything. The bot had coffee.",
    "Ask softly. The electrons are eavesdropping.",
    "Type a question. The goblins crave context.",
    "Drop a prompt. The bot is wearing its clever pants.",
    "Ask boldly. The typo police are off duty.",
    "Whisper a question. The circuit is blushing.",
    "Type something juicy. The bot loves tasteful chaos.",
    "Ask away. The silicon is feeling chatty.",
    "Say the thing. The little gremlins are unionized now.",
    "Type a question. The bot stretched first.",
    "Ask nicely. The packets are in a romantic mood.",
    "Drop a prompt. The math is already sweating.",
    "Ask anything. The drama engine is warmed up.",
    "Type your thought. The bot brought extra sass.",
    "Ask away. The keyboard yearns for meaning.",
    "Feed me a prompt. The semicolons demand tribute.",
    "Type with confidence. The bot can smell hesitation.",
    "Ask a question. The cache is emotionally available.",
    "Drop some words. The bot is suspiciously awake.",
    "Ask anything. The bugs are hiding under the sofa.",
    "Type a prompt. The machine has gossip to process.",
    "Ask gently. The stack is tender today.",
    "Say something smart. The bot loves a challenge.",
    "Type a question. The fan noise means ambition.",
    "Ask boldly. The compiler enjoys foreplay.",
    "Drop a prompt. The bot shaved milliseconds for this.",
    "Ask a messy question. Clean answers are overrated.",
    "Type anything. The void already read your draft.",
    "Ask away. The bot is caffeinated and morally flexible.",
    "Say the weird part too. That is usually the important part.",
    "Type a prompt. The bot packed snacks and opinions.",
]


def get_random_placeholder() -> str:
    return choice(PLACEHOLDERS)
