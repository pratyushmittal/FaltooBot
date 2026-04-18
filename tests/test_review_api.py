from pathlib import Path

from faltoobot.faltoochat.review_api import (
    Reviews,
    review_to_message_item,
    reviews_prompt,
    upsert_review,
)


def test_reviews_prompt_groups_reviews_by_filename_and_wraps_names_in_backticks() -> (
    None
):
    prompt = reviews_prompt(
        [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 2,
                "line_number_end": 3,
                "code": "line a\nline b",
                "comment": "First alpha comment",
            },
            {
                "filename": Path("beta.py"),
                "line_number_start": 8,
                "line_number_end": 8,
                "code": "beta line",
                "comment": "Only beta comment",
            },
            {
                "filename": Path("alpha.py"),
                "line_number_start": 5,
                "line_number_end": 6,
                "code": "line c",
                "comment": "Second alpha comment",
            },
        ]
    )

    assert (
        prompt
        == """# Comments in code review

## File name `alpha.py`

### Line `2-3`

Code:

```
line a
line b
```

Comment:
First alpha comment

### Line `5-6`

Code:

```
line c
```

Comment:
Second alpha comment

---

## File name `beta.py`

### Line `8-8`

Code:

```
beta line
```

Comment:
Only beta comment"""
    )


def test_reviews_prompt_prefers_file_line_numbers_when_present() -> None:
    prompt = reviews_prompt(
        [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 20,
                "line_number_end": 22,
                "file_line_number_start": 13,
                "file_line_number_end": 15,
                "code": "line a",
                "comment": "Use file lines",
            }
        ]
    )

    assert "### Line `13-15`" in prompt
    assert "### Line `20-22`" not in prompt


def test_upsert_review_deletes_existing_review_when_comment_is_blank() -> None:
    reviews: Reviews = [
        {
            "filename": Path("alpha.py"),
            "line_number_start": 2,
            "line_number_end": 2,
            "code": "b = 2",
            "comment": "Keep",
        }
    ]

    result = upsert_review(
        reviews,
        {
            "filename": Path("alpha.py"),
            "line_number_start": 2,
            "line_number_end": 2,
            "code": "b = 2",
            "comment": "   ",
        },
    )

    assert result == "deleted"
    assert reviews == []


def test_review_to_message_item_wraps_prompt_as_user_message() -> None:
    message = review_to_message_item(
        [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 2,
                "line_number_end": 2,
                "code": "b = 2",
                "comment": "Fix this",
            }
        ]
    )

    assert message["role"] == "user"
    assert message["type"] == "message"
    assert message["content"][0]["type"] == "input_text"
    assert "alpha.py" in message["content"][0]["text"]
