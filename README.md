# DragBizz Infra

Shared Redis infrastructure for Auth, Core, Finance, Utility and Brain.

Services connect on the private `redis-private` network using the `redis` or `dragbizz-redis` alias, DB `0`. Each service has its own ACL user and password. Redis has no published host port.

The data volume defaults to `redis-data` and can be changed with `REDIS_VOLUME_NAME`. Configuration, credential scripts and backup tools are under `redis/`.

Run `bash setup.sh -y` for a non-interactive development setup, or `bash setup.sh` to enter the Redis hostname and confirm startup. Use `bash setup.sh --prod -y` on production, where service directories are named `Auth`, `Core`, `Utility`, `Brain` and `Finance`. The setup creates/reuses the matching Redis secrets, syncs them into sibling service `.env` files, then starts Redis.

See [SETUP.MD](SETUP.MD) for setup details.
