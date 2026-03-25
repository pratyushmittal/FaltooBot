from pathlib import Path
from typing import Literal, TypeAlias, TypedDict

from faltoobot.gpt_utils import MessageItem


class Review(TypedDict):
    filename: Path
    line_number_start: int
    line_number_end: int
    code: str
    comment: str


Reviews: TypeAlias = list[Review]


def _reviews_by_filename(reviews: Reviews) -> dict[Path, Reviews]:
    grouped: dict[Path, Reviews] = {}
    for review in reviews:
        filename = review["filename"]
        grouped.setdefault(filename, []).append(review)
    return grouped


def reviews_prompt(reviews: Reviews) -> str:
    grouped_reviews = _reviews_by_filename(reviews)
    lines = [
        "# Comments in code review",
        "",
        "Please address these comments in code review.",
        "",
    ]
    filenames = list(grouped_reviews)
    for index, filename in enumerate(filenames):
        lines.extend([f"## File name `{filename}`", ""])
        for review in grouped_reviews[filename]:
            lines.extend(
                [
                    f"### Line `{review['line_number_start']}-{review['line_number_end']}`",
                    "",
                    "Code:",
                    "",
                    "```",
                    review["code"],
                    "```",
                    "",
                    "Comment:",
                    review["comment"],
                    "",
                ]
            )
        if index < len(filenames) - 1:
            lines.extend(["---", ""])
    return "\n".join(lines).strip()


def review_to_message_item(reviews: Reviews) -> MessageItem:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": reviews_prompt(reviews)}],
    }


def get_review(
    reviews: Reviews,
    *,
    filename: Path,
    line_number_start: int,
    line_number_end: int,
) -> Review | None:
    for review in reviews:
        if review["filename"] != filename:
            continue
        if line_number_end < review["line_number_start"]:
            continue
        if review["line_number_end"] < line_number_start:
            continue
        return review
    return None


def upsert_review(
    reviews: Reviews, review: Review
) -> Literal["added", "updated", "deleted", "ignored"]:
    comment = review["comment"].strip()
    existing = get_review(
        reviews,
        filename=review["filename"],
        line_number_start=review["line_number_start"],
        line_number_end=review["line_number_end"],
    )
    if not comment:
        if existing is None:
            return "ignored"
        reviews.remove(existing)
        return "deleted"

    review["comment"] = comment
    if existing is None:
        reviews.append(review)
        return "added"
    reviews[reviews.index(existing)] = review
    return "updated"
