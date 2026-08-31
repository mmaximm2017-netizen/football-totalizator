# Custom GPT Read-Only API Setup

The application does not create database roles or change production permissions.
Run `docs/totish_gpt_readonly_role.sql.example` manually as the Neon database owner after
replacing its placeholders. Use a new long random password and construct the Neon
connection string for `totish_gpt_reader`.

```sh
openssl rand -hex 32
```

Set only these values in the production `.env`; do not commit them:

```env
TOTISH_GPT_API_KEY=<openssl-random-value>
TOTISH_GPT_DATABASE_URL=postgresql://totish_gpt_reader:...@.../...
```

Restart the existing application deployment by its normal operational procedure,
then verify without exposing the token in shell history where possible:

```sh
curl -H "Authorization: Bearer $TOTISH_GPT_API_KEY" https://totish.ru/api/gpt/health
```

Expected response includes `database: "ok"` and `read_only: true`. Import
`docs/totish_gpt_openapi.yaml` into Custom GPT Actions, configure HTTP Bearer
authentication with `TOTISH_GPT_API_KEY`, and use the text in
`docs/totish_custom_gpt_instructions.md` as the GPT instructions.

The SQL role has `SELECT` only. The API separately starts read-only transactions,
rolls them back on close, and has no commit function. Both layers are required:
the PostgreSQL role remains the physical protection if API code or a GPT Action is
wrong.
