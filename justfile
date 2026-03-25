set fallback

run:
	uv run faltoochat

[positional-arguments]
@test *args='':
	uv run pytest $@

[positional-arguments]
@test-local *args='':
	uv run pytest -m "not external" $@

test-failed:
	uv run pytest --lf
