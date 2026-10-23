# Deploy Revenue Leakage Agent to Vercel

This deployment retains Streamlit and uses Vercel container Functions through
`Dockerfile.vercel`. Groq is primary; OpenRouter is the backup. The hosted
entrypoint forces Ollama off. Local JSON/SQLite storage remains available when
running the app locally; hosted records and chat checkpoints use Postgres.

## Prepare the database

Create a dedicated Postgres database, for example Neon through the Vercel
Marketplace. Use separate databases for production and previews. The app is a
shared investigation workspace, with the same shared records and conversation
picker as the local prototype; it does not provide tenant isolation.

Set the connection strings in your shell or an uncommitted environment file:

```dotenv
DATABASE_URL=<provider-pooled-postgres-url-with-TLS>
DATABASE_URL_UNPOOLED=<provider-direct-postgres-url-with-TLS>
REVENUE_WORKSPACE=revenue-leakage
```

The Vercel container automatically runs the idempotent setup on startup using
the integration's `DATABASE_URL_UNPOOLED`, with migrations serialized by a
database advisory lock. This also works when a developer's local network cannot
reach Postgres. To initialize manually, install the hosted requirements and run:

```sh
python -m pip install -r requirements.hosted.txt
python -m leakage.hosted setup
```

The setup command reads environment variables from the calling shell. It does
not load `.env` automatically. It serializes schema migrations on the direct
database connection and seeds the original supplied source records only if the
workspace does not already exist. Re-running it preserves existing billing
records, approvals, receipts, audit events and conversation checkpoints.
Local sandbox contents and past local conversations are not uploaded.

## Deploy the application

1. Sign in to Vercel and create/import a project. Set its root directory to this
   folder, `outputs/revenue-leakage-agent`, if importing the whole workspace.
   If publishing this folder as its own repository, use the repository root.
2. Add `DATABASE_URL`, `GROQ_API_KEY` and `OPENROUTER_API_KEY` as server-side
   project secrets. Also supply `DATABASE_URL_UNPOOLED` for startup migrations.
   Both database URLs must point to the same dedicated database. Never put
   these values in source control or public URLs.
3. Set `PORT=80` and the non-secret settings in `.env.vercel.example`. Do not
   upload a real `.env` file. The container copies source files explicitly;
   `.dockerignore` and `.vercelignore` also exclude secrets and local state.
4. Deploy using Vercel's container detection for `Dockerfile.vercel`, either
   through the connected repository or `vercel deploy` from this directory.
   After verifying the preview against its own database, publish production.

The container serves on `0.0.0.0:$PORT` and keeps Streamlit's CORS and XSRF
protections enabled. Check `/_stcore/health` after deployment. An unhealthy
database must also be checked by opening the app and running an investigation;
the Streamlit process health endpoint alone does not validate database access.

## Verify persistence and providers

Investigate C-1007-A1. Its agreement summary should include C-1007, show USD
45,000 net billed from linked invoices/credits, and separately suggest the USD
15,000 orphan I-9202 for review. Then reload the chat URL and confirm the saved
conversation resumes. After a redeploy, confirm the same records and chat still
exist. Check the execution trace for the provider used; OpenRouter is attempted
after the configured Groq fallbacks when eligible provider errors occur.

Hosted mutations use a short Postgres transaction with `SELECT ... FOR UPDATE`
on one workspace snapshot row. This preserves atomic approval, receipt and
audit commits across replicas. Checkpoints use LangGraph's Postgres saver and
the same bounded connection pool. This prototype serializes mutations within
one workspace; it is not designed as a high-volume accounting database.

Never use the local `leakage.cli reset` command to reset a hosted database.
Use a new dedicated database/workspace for a fresh deployment. Provider
database backup/restore applies to both the snapshot and checkpoint tables.

## Local verification

```sh
python -m pytest tests/test_hosted.py -q
# Optional: actual database tests require a dedicated disposable test database.
TEST_POSTGRES_URL=<dedicated-test-db-url> python -m pytest tests/test_postgres.py -q
```

References: [Vercel container deployments](https://vercel.com/kb/guide/docker),
[Postgres through Vercel Marketplace](https://vercel.com/docs/marketplace-storage),
[LangGraph Postgres persistence](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres).
