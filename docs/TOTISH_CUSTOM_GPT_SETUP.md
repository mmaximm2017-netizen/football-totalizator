# Custom GPT Read-Only API Setup

The application does not create database roles or change production permissions.
Run `docs/totish_gpt_readonly_role.sql.example` manually as the Neon database owner after
replacing its placeholders. Use a new long random password and construct the Neon
connection string for `totish_gpt_reader`.

Before relying on the role, inspect effective grants. PostgreSQL privileges are
additive: `PUBLIC` CREATE on `public` or `PUBLIC` TEMPORARY on the database also
applies to the reader. Do not revoke either from `PUBLIC` without an
application-wide privilege review.

```sql
SELECT has_schema_privilege('totish_gpt_reader', 'public', 'CREATE') AS can_create,
       has_database_privilege('totish_gpt_reader', current_database(), 'TEMPORARY') AS can_temp;
SELECT parent.rolname AS inherited_role
FROM pg_auth_members members
JOIN pg_roles member ON member.oid = members.member
JOIN pg_roles parent ON parent.oid = members.roleid
WHERE member.rolname = 'totish_gpt_reader';
```

Both effective privilege values must be `false`, and the role must have no
unexpected memberships, before enabling the GPT connection string. If either is
true, stop the rollout and assess existing application roles; do not apply a
global `PUBLIC` revoke blindly.

Copy the example, replace the two placeholders, review it, and run it only after
explicit production approval:

```sh
cp docs/totish_gpt_readonly_role.sql.example /tmp/totish_gpt_reader.sql
${EDITOR:-vi} /tmp/totish_gpt_reader.sql
psql "$DATABASE_URL" -f /tmp/totish_gpt_reader.sql
```

```sh
openssl rand -hex 32
```

Set only these values in the production `.env`; do not commit them:

```env
TOTISH_GPT_API_KEY=<openssl-random-value>
TOTISH_GPT_DATABASE_URL=postgresql://totish_gpt_reader:...@.../...
```

From the production repository root, apply the new environment with the existing
Compose deployment (this does not modify Compose configuration):

```sh
docker compose up -d
docker compose ps
```

Then verify without exposing the token in shell history where possible:

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
