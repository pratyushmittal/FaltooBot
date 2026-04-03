from random import choice

PLACEHOLDERS = [
    "Ask anything. The bot warmed up its tiny keyboard.",
    "Type a question. The pixels are paying attention.",
    "Drop a prompt. The bot put on its problem-solving cap.",
    "Ask away. The coffee has kicked in.",
    "Type something clever. The bot loves a good puzzle.",
    "Ask boldly. The semicolons are standing by.",
    "Share a question. The rubber duck is listening too.",
    "Type a prompt. The gremlins are on their best behavior.",
    "Ask nicely. The stack traces are feeling helpful today.",
    "Drop a thought. The bot brought extra curiosity.",
    "Ask a tricky one. The debugger enjoys hide and seek.",
    "Type a question. The keyboard is ready for adventure.",
    "Ask anything. The cache remembered where we left off.",
    "Drop a prompt. The bot sharpened its pencils.",
    "Ask away. The compiler seems optimistic today.",
    "Type your idea. The bot packed snacks and patience.",
    "Ask a messy question. Those are often the fun ones.",
    "Type something. The bot already opened a fresh notepad.",
    "Ask a question. The packets arrived wearing tiny hats.",
    "Drop a prompt. The bot promises at least one good idea.",
    "Ask away. The logs are ready to spill their secrets.",
    "Type a question. The cursor is leaning forward.",
    "Ask gently. Even stubborn bugs have feelings.",
    "Drop some words. The bot enjoys tasteful chaos.",
    "Ask anything. The little gears are spinning happily.",
    "Type a prompt. The bot has both opinions and backup plans.",
    "Ask a weird one. Weird questions often find the shortcut.",
    "Drop a thought. The bot swept the crumbs off the desk.",
    "Ask away. The terminal is pretending not to be nervous.",
    "Type a question. The answer may contain useful wizardry.",
    "Say the important part. The bot can handle the plot twist.",
]


def get_random_placeholder() -> str:
    return choice(PLACEHOLDERS)
