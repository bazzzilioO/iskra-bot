## Notes

- Recommended Python version: **3.12**. Using Python 3.13 with aiogram may cause startup issues (e.g., timeout handling in polling) on Railway, so prefer 3.12 for stable deployments.

## Owner display name

- `owner_display_name` is captured on the first smartlink save and must be treated as immutable afterward.
- Use `owner_display_name` (or display names like `artist`/`title`) for UI text; never use slug values for display.
- Updates to smartlinks must preserve the originally saved `owner_display_name` even if the Telegram profile name changes.

## Smartlink cover rules

- External covers: provide a public `cover_url` (must be `http/https`).
- Telegram uploads: store the Telegram `file_id` in `cover_source` as `{ "type": "telegram", "file_id": "..." }`. Do not rehost or download the image when indexing.
- Validation: Telegram `file_id` must be non-empty and non-numeric. Malformed covers are logged and skipped during indexing.

### Indexing payload

Exactly one of the following is sent to indexing:

- `cover_url` (external link)
- `cover_source.telegram.file_id`

Never send both values in the same payload.
