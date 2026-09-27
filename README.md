# card-public-db-bridge

Минимальный публичный GitHub Actions bridge для контролируемого rollout Mining v49.

## Безопасность

- В репозитории нет DB/FTP credentials, дампов БД или истории результатов.
- Endpoint хранится только в GitHub Actions secret `CARD_MINE_ENDPOINT`.
- Разрешены только `inspect_mine_v49` и `apply_mine_v49`.
- Workflow запускается только при изменении `command.json` в `main` или вручную через `workflow_dispatch`.
- Pull request сам по себе не запускает bridge и не получает Actions secrets.
- Перед выполнением workflow проверяет whitelist действия и наличие secret.
- Результат не коммитится в репозиторий: используется временный Actions artifact.

## Управление

`command.json`:

```json
{
  "request_id": "unique-request-id",
  "action": "inspect_mine_v49"
}
```

Изменение файла в `main` запускает bridge. Для записи используется только `apply_mine_v49`.

## Staged rollout

Сначала выполнить `inspect_mine_v49`. Если `runtime_v49_contract=false`, сначала обновить production runtime и только затем запускать `apply_mine_v49`.

