from src.bot import MAX_MESSAGE_CHARS, build_username_messages


def test_renders_usernames_only():
    messages = build_username_messages(["naval", "alice"])

    assert len(messages) == 1
    body = messages[0]
    assert "2 username(s) extracted" in body
    assert "1. <code>@naval</code>" in body
    assert "2. <code>@alice</code>" in body
    # No follower counts, bios, names or profile links.
    assert "Followers" not in body
    assert "Bio" not in body
    assert "https://" not in body


def test_long_lists_are_split_under_the_telegram_limit():
    usernames = [f"user{i:04d}" for i in range(500)]
    messages = build_username_messages(usernames)

    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE_CHARS for m in messages)
    # Every username survives the split exactly once.
    joined = "\n".join(messages)
    assert all(f"<code>@{u}</code>" in joined for u in usernames)
    assert joined.count("<code>") == len(usernames)


def test_empty_list_still_renders_a_header():
    assert build_username_messages([]) == ["🔍 <b>0 username(s) extracted:</b>\n"]
