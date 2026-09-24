# EmailVerify

Bulk email verification on top of the [MailTester Ninja](https://mailtester.ninja/api/)
API, with a small web UI and a MailWizz export step. Runs on the VPS under
`/opt/emails`.

```
bin/verify_worker.py   the queue worker - calls the API at the plan's rate limit
bin/store.py           shared SQLite layer (verify.db: jobs, emails, cache, usage)
bin/webapp.py          Flask UI: upload a list, watch progress, download results
bin/process_verify.py  splits a finished job into MailWizz upload / bad / risky / re-verify
bin/build_index.py     builds a client's contact index (names, company) for the export
bin/api.py             token-protected HTTP endpoint for n8n
bin/ingest.py          queue CSV files from the shell instead of the browser
bin/set-key.sh         test and install a MailTester Ninja key
bin/fix_transient.py   one-off: re-open answers saved as final before they were retried
web/templates/         UI pages
deploy/systemd/        the three services
tests/                 runs everything against a fake API - no network, no credits
```

## What the answers mean

| API answer | Where it ends up |
|---|---|
| `ok` Accepted | `1 - UPLOAD to MailWizz` |
| `ko` Rejected, No MX, Disposable, Limited | `bad - do not send` |
| `mb` Catch-All | `unknown - risky` |
| Timeout, SPAM Block, MX Error, Greylisted | retried up to `max_attempts`, then `needs re-verify` |
| `--` Disabled / Invalid Key | never saved - the worker stands down and asks again |

Secrets (`mailtester.key`, `api.token`, `verify.conf.json`) and all client data
stay on the server and are git-ignored. `verify.conf.example.json` shows the
config shape.

## Deploy

```
cp bin/*.py bin/set-key.sh /opt/emails/bin/
cp web/templates/*.html /opt/emails/web/templates/
systemctl restart email-verify-worker email-verify-web
```

The live `verify.conf.json` overrides the worker's defaults, so its
`transient` list must name every answer to retry - compare it with
`verify.conf.example.json` after an update.

## Tests

```
python -m unittest discover -s tests -v
```

The worker finishes the address in hand on SIGTERM, and anything it had
claimed becomes claimable again after 15 minutes, so a restart loses nothing.
