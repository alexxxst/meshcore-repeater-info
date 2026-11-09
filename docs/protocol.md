# Используемые запросы MeshCore

Документ фиксирует, откуда берётся каждое поле отчёта. Реализация опирается
на библиотеку [`meshcore_py`](https://github.com/meshcore-dev/meshcore_py) и
прошивку [`MeshCore`](https://github.com/meshcore-dev/MeshCore).

## Контакты

`CMD_GET_CONTACTS` возвращает контакт-лист компаньона. Репитеры имеют
`type == 2` (`ADV_TYPE_REPEATER`). Для каждого контакта есть полный public key,
`adv_name`, сохранённый исходящий путь и его длина, а также `last_advert` —
время последнего адверта по часам репитера. В отчёт это время пишется в UTC
в колонке `last_advert_utc`.

## Login

`CMD_SEND_LOGIN` (`0x1A`) с полным public key и паролем. Успешный ответ
содержит:

- права (`is_admin`);
- `server_timestamp` — время репитера в момент login (UTC, Unix time);
- `acl_permissions`;
- `fw_ver_level` — уровень прошивки, по которому понятно, поддерживается ли
  `GET_OWNER_INFO`.

Пустой пароль сначала проверяется по ACL компаньона, затем по паролю
администратора и гостевому паролю. Поэтому успешный вход с пустым паролем
может означать как ACL, так и пустой гостевой пароль; в отчёте он помечается
`login_method=empty`. После admin-входа репитер сохраняет компаньон в ACL;
guest-запись во флеш не сохраняется.

## `GET_OWNER_INFO` — версия, имя, owner info

Бинарный запрос `REQ_TYPE_GET_OWNER_INFO` (`0x07`) после login. Ответ:

```
<firmware version>\n<node name>\n<owner info>
```

Доступен гостевой сессии, без admin. В simple repeater появляется с
`FIRMWARE_VER_LEVEL >= 2` (прошивка v1.12.0+). Это основной способ получить
версию, имя репитера и контакт владельца.

## `STATUS`

Бинарный запрос `REQ_TYPE_GET_STATUS` (`0x01`). Используются поля
`RepeaterStats`:

| Поле отчёта | Поле ответа |
|---|---|
| `uptime` | `total_up_time_secs` |
| `tx_airtime_pct` | `total_air_time_secs / uptime` |
| `rx_airtime_pct` | `total_rx_air_time_secs / uptime` |
| `noise_floor_dbm` | `noise_floor` |
| `debug_flags` | `err_events` (в `meshcore_py` — `full_evts`) |

## `NEIGHBOURS`

Бинарный запрос `REQ_TYPE_GET_NEIGHBOURS` (`0x06`). Запрашивается всего одна
запись (`count=1`), потому что ответ всегда начинается с общего количества
соседей. В отчёт попадает только `neighbours_count`.

## Flood / path

Прошивка компаньона выбирает flood, только если у контакта
`out_path_len == OUT_PATH_UNKNOWN` (`0xFF`). Иначе запрос уходит по
сохранённому path. Буквальный direct/zero-hop мы не навязываем: path может
оказаться zero-hop только если его так построил сам компаньон.

- `CMD_RESET_PATH` (`0x0D`) сбрасывает маршрут контакта в unknown.
- После этого `SEND_LOGIN`, `SEND_BINARY_REQ` и `SEND_TXT_MSG` уходят флудом.
- Когда компаньон выучивает путь, он присылает `PUSH_CODE_PATH_UPDATED`
  (`PATH_UPDATE`) с public key.

Логика утилиты:

1. каждый репитер начинается с flood;
2. после успешного flood-запроса и `PATH_UPDATE` проверяем сохранённый path
   следующим запросом;
3. если path не прошёл — до конца опроса репитера остаёмся на flood.

Ответ репитера может прийти по свежему пути из запроса; это решение
репитера, запросы утилиты при этом остаются флудом.

## Admin fallback

Если сессия случайно оказалась admin, а `GET_OWNER_INFO` не ответил,
используются remote CLI команды:

- `ver`;
- `get name`;
- `get owner.info`.

Они тоже отправляются в текущем режиме (path или flood).
