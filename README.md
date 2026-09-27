# card-public-db-bridge

Минимальный публичный GitHub Actions bridge для контролируемого rollout Mining v49.

- без дампов БД и истории команд;
- без FTP и DB credentials;
- endpoint хранится только в GitHub Actions secret `CARD_MINE_ENDPOINT`;
- разрешены только `inspect_mine_v49` и `apply_mine_v49`;
- результат хранится только как временный Actions artifact.

## Настройка

Создай repository secret `CARD_MINE_ENDPOINT` в `Settings → Secrets and variables → Actions`.
