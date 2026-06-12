set fallback

run:
	uv run faltoochat

[positional-arguments]
@test *args='':
	uv run pytest -n auto $@

[positional-arguments]
@test-local *args='':
	uv run pytest -n auto -m "not external" $@

test-failed:
	uv run pytest -n auto --lf

website:
	cd website && npm run build && npm run preview
