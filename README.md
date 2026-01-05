## Notes

- Recommended Python version: **3.12**. Using Python 3.13 with aiogram may cause startup issues (e.g., timeout handling in polling) on Railway, so prefer 3.12 for stable deployments.

## Smartlink cover rules

- External covers: provide a public `cover_url` (must be `http/https`).
- Telegram uploads: store the Telegram `file_id` in `cover_source` as `{ "type": "telegram", "file_id": "..." }`. Do not rehost or download the image when indexing.
- Validation: Telegram `file_id` must be non-empty and non-numeric. Malformed covers are logged and skipped during indexing.

### Indexing payload

Exactly one of the following is sent to indexing:

- `cover_url` (external link)
- `cover_source.telegram.file_id`

Never send both values in the same payload.
