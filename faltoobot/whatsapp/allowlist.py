MIN_ALLOWLIST_DIGITS = 8


def _phone(chat: str) -> str:
    if not chat.endswith("@s.whatsapp.net"):
        return ""
    phone = chat.split("@", 1)[0]
    return phone if len(phone) >= MIN_ALLOWLIST_DIGITS else ""


def _phone_matches(left: str, right: str) -> bool:
    return left.endswith(right) or right.endswith(left)


def matches_allowed_chats(allowed_chats: set[str], source_ids: set[str]) -> bool:
    if not allowed_chats:
        # comment: an empty allowlist should block everyone until explicitly configured.
        return False
    if allowed_chats & source_ids:
        return True

    allowed_phones = {_phone(chat) for chat in allowed_chats} - {""}
    source_phones = {_phone(chat) for chat in source_ids} - {""}
    return any(
        _phone_matches(allowed_phone, source_phone)
        for allowed_phone in allowed_phones
        for source_phone in source_phones
    )
