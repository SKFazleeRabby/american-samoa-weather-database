# Weather database

Docker setup for PostgreSQL 17 with TimescaleDB, PostGIS, `pg_stat_statements`,
and a separate pgAdmin web interface. TimescaleDB supports time-series
hypertables and aggregation; PostGIS supports geographic queries;
`pg_stat_statements` helps identify expensive queries.

For **DigitalOcean deployment and secure access from an AI server**, follow
[the deployment guide](docs/DEPLOY_DIGITALOCEAN.md). It includes
public browsing/downloads, a dedicated AI database reader, restricted SSH
tunneling, persistent connections, and backups.
For a professor-owned Droplet with SSH-only access, start with the guide's
permission and existing-service checks. Your professor handles any DigitalOcean
Cloud Firewall changes; the server steps use your own SSH account.

## Run the complete website with Docker

The working website preserves the approved map and interface and uses React,
TypeScript, Vite, TanStack Query, Leaflet, FastAPI, and psycopg connection pools.
It reads your actual PostgreSQL observations; no demo stations or generated
measurements are included in the application.

On this workstation the database credentials and dedicated website account are
already configured. Start Docker Desktop, then run from this directory:

```powershell
docker compose up --build -d
docker compose ps
```

Open **http://localhost:8080** for the website and **http://localhost:8080/docs**
for the API documentation. PostgreSQL remains at localhost:5432 and pgAdmin at
localhost:5050. `WEB_PORT` in `.env` changes the website port.

For a fresh checkout, copy `.env.example` to `.env` once and set
`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `PGADMIN_DEFAULT_PASSWORD`, and
`PGADMIN_DEFAULT_EMAIL` before starting. Use a different strong password for
each account. No local Node.js or Python installation is needed for Docker.
First startup imports the historical CSVs and may take longer; an existing
database volume is reused without reimporting or deleting observations.

Compose starts the database, runs the one-time `web-setup` container, then starts
the website after setup succeeds. `web-setup` provisions `weather_reader` (or
`APP_DB_USER`) with SELECT access to only `stations`, `variables`, and
`observations`. Administrator credentials are supplied only to setup and the
database; the website receives the separate read-only credentials. Passwords
stay in ignored `.env`/container environment, outside frontend assets and images.
An exited setup container with status 0 is normal. Restarting setup is safe;
after changing `APP_DB_PASSWORD`, run `docker compose up -d --force-recreate web-setup web`.

```powershell
docker compose logs --tail 80 web web-setup
docker compose stop web
```

For local use the website is bound to localhost. For public deployment using the
Droplet IP, keep `PUBLIC_SITE_ADDRESS=:80` in `.env` and open TCP 80 to everyone
in its firewall. Start the included public web proxy with:

```bash
docker compose -f compose.yaml -f compose.public.yaml up --build -d
```

Anyone can browse and download data at `http://DROPLET_IP` without logging in.
PostgreSQL and pgAdmin remain private; the separate AI server uses its SSH tunnel.
When you obtain a domain, the same proxy can provide automatic HTTPS. See the
deployment guide for public access, verification, and administrator access.

### Browsing and downloads

- The starting date range is the latest seven available archive days, rather
  than the current calendar date. Station/variable metadata comes from PostgreSQL.
- Station type/status, multiple stations, multiple variables, inclusive local
  dates, reporting interval, and date sorting are supported. All six registered
  variables are available even when some have no readings in the chosen range.
- Table rows preserve station, local date, exact UTC timestamp, and reporting
  interval. Different reporting intervals are never averaged together.
- Server-side keyset pagination fetches 10/50/100 rows without scanning a growing
  OFFSET. TanStack Query caches results, cancels obsolete browser requests, and
  prefetches the next page. The previous page remains visible during page changes.
- UTC timestamp bounds allow TimescaleDB chunk pruning alongside exact local
  date filtering. Metadata is cached five minutes; pages and counts for 30 seconds.
  Date ranges are not truncated, and counts load independently of the first page.
- Data downloads are streaming ZIP archives containing exactly one CSV per selected
  variable. Each CSV has only `Date` plus station-name columns, with one row per
  requested calendar day. Missing readings stay blank and zero is retained.
  The download includes every filtered station/date, across all table pages.
  Daily exports reject conflicting duplicate values instead of averaging them.
  Choose the Daily reporting interval for this date-by-station format.
- The Stations window offers a separate CSV with exactly `Station Name`,
  `Longitude`, `Latitude`, `Elev`, and `Network` columns for the station catalog.
- Precipitation, Temperature, and Relative Humidity are selected by default.
  Clicking empty map space clears only the station selection, preserving the
  other filters. Clicking a station marker continues to select that station.
- Interactive queries have a 15-second statement timeout and exports a 120-second
  timeout. The UI shows loading, empty, validation, connection-error, and retry states.

### Local development and tests

For backend development, after setting up the read-only account:

```powershell
uv sync
uv run uvicorn as_db.web.api:app --reload --host 127.0.0.1 --port 8000
```

In `web/`, use Node.js 24 and pnpm 11.25.0:

```powershell
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm test
```

Vite serves http://localhost:5173 and proxies `/api` to FastAPI on port 8000.
For a single local server, build `web/` first and open http://localhost:8000.

```powershell
uv run python -m pytest tests -q
# Opt in to SELECT-only web tests against the configured real archive:
$env:AS_DB_WEB_TEST='1'
uv run python -m pytest tests/test_web.py -q
```

The original import integration tests remain opt-in using `AS_DB_TEST_DSN`
against a disposable database. They are separate from the website's read-only
archive checks. Geographic data attribution is in `web/public/MAP-DATA.md`.

## Start the database

Install and start Docker Desktop with Linux containers enabled. In PowerShell,
from this directory:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set strong, separate `POSTGRES_PASSWORD` and
`PGADMIN_DEFAULT_PASSWORD` and `APP_DB_PASSWORD` values, plus your
`PGADMIN_DEFAULT_EMAIL`, then run:

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs db
```

Connection settings:

| Setting | Default |
| --- | --- |
| Host from Windows | `127.0.0.1` |
| Host from another service in this Compose project | `db` |
| Port from Windows | `5432`, configurable in `.env` |
| Port inside the Compose network | `5432` |
| Database | `POSTGRES_DB` from `.env` (example: `weather`) |
| Bootstrap administrator | `postgres` |

The host port is bound to localhost. The website connects through the FastAPI
backend using its restricted database login. `POSTGRES_USER` is a database
superuser created at first initialization. Website users and API keys are
separate application identities, not PostgreSQL logins.

## pgAdmin

Open <http://localhost:5050> and log in with `PGADMIN_DEFAULT_EMAIL` and
`PGADMIN_DEFAULT_PASSWORD`. Expand **Weather > Weather Database** and enter
`POSTGRES_PASSWORD` when prompted to connect to PostgreSQL.

Set `POSTGRES_DB` in `.env` to choose the database name. Compose requires a
nonempty value and passes it to both services. pgAdmin's server registration
is generated at startup from `POSTGRES_DB` and `POSTGRES_USER`, using host `db`.
Server definitions are managed by this configuration and replaced at each
pgAdmin startup, so additional server registrations made in the interface will
not persist across restarts. Browser credentials persist in `pgadmin_data`;
changing `.env` does not reset an existing pgAdmin account's password.

## Existing database volumes

If the earlier PostgreSQL/PostGIS container has already initialized the volume,
take a backup before rebuilding. This setup retains PostgreSQL 17 and the same
volume path. After rebuilding, enable TimescaleDB in the existing database:

```powershell
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"'
```

This command uses the container's configured user and database. Initialization
scripts automatically enable all three extensions for a new database.

## Verify extensions

Open a database shell using the user and database configured in `.env`:

```powershell
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Then run:

```sql
SELECT version();
SELECT PostGIS_Full_Version();
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SHOW shared_preload_libraries;
SELECT count(*) FROM pg_stat_statements;
```

Use `\q` to exit.

## Tables and relationships

On first startup with an empty data volume, PostgreSQL enables the extensions
and then runs `scripts/create_tables.py`. It creates `stations`, `variables`,
`sources`, `observations`, `uploads`, `users`, and `api_keys`, including foreign
keys, uniqueness constraints, validation checks, and query indexes.

`observations` is automatically converted to a TimescaleDB hypertable on
`observed_at`, with 365-day chunks suited to the current daily archive. Revisit
the interval as high-frequency data grows. Existing hypertables retain their
chunk interval; PostgreSQL is configured with increased lock capacity to support
large historical transactions. Its primary key is `(station_id, variable_id, interval_seconds,
observed_at)`. `sources` remains a separate station/variable/provider lookup,
as agreed. No measurement-series or revision table is created.

For an existing database, rebuild the image and run the script manually:

```powershell
docker compose up --build -d
docker compose exec db python3 /opt/weather/create_tables.py
```

The script reads `POSTGRES_DB` and `POSTGRES_USER` from the container environment
provided by Compose from `.env`. It uses the installed `psql` client and runs
all schema statements in one transaction. Extensions must already be enabled
(see the existing-volume instructions above).

It can be rerun against its own unchanged schema. It does not alter existing
table definitions or migrate a populated ordinary observations table; use a
planned migration for those cases. It does not seed users, API keys, stations,
or measurements. Password and API-key hashing belong in the application.

To inspect the SQL without a connection, run locally:

```powershell
.\.venv\Scripts\python.exe scripts/create_tables.py --print-sql
```

## Automatic historical import

`docker compose up --build -d` now initializes a new database in this order:
extensions, tables, stations, variables, and all six historical measurement
CSVs. The image includes the CSV bundle and a small Python environment for
calling the standard observation CSV importer described below.

The import reads the database name from `POSTGRES_DB`, supplied by `.env`.
Data is inserted on the first container startup with an empty database volume;
`docker build` alone builds the image without starting PostgreSQL.

`docker/history-imports.json` defines the files, variable codes, reporting
intervals, and station timezone. The bundled files are treated as daily
products (`86400` seconds), with dates at midnight in `Pacific/Pago_Pago`.
Update this manifest when adding files or changing those reporting conventions.
It is explicit because aggregation cannot safely be inferred from filenames or
provider variable codes.

Station/variable insertion and all historical files are one transaction. If a
file fails, no seed data from that attempt is committed. Entirely empty station
columns with no metadata mapping are ignored and logged; an unknown station
with populated readings fails the import. No populated readings are silently
discarded. Restarting with an existing volume does not reload the bundle.

For an existing volume, or to recover from an interrupted first initialization,
rebuild the image and run the schema/import scripts without deleting data:

```powershell
docker compose up --build -d
docker compose exec db python3 /opt/weather/create_tables.py
docker compose exec db /opt/weather/venv/bin/python /opt/weather/seed_history.py
```

If first initialization stopped before extension creation completed, first apply
the extension SQL using `psql` in the configured database. Import reruns skip
existing observations and update station/variable metadata from the bundled
files. No application users, API keys, upload records, or source endpoints are
created by the seed process.

To see import progress:

```powershell
docker compose logs -f db
```

## Import observation CSVs

Use one importer for every observation variable. Install dependencies with
`uv sync`, then supply a CSV path and the registered variable name:

```powershell
uv run python -m as_db.import_observations data/as_temperature_1999_12_2026_09.csv "Air Temperature"
uv run python -m as_db.import_observations data/as_rainfall_1966_04_2026_09.csv "Precipitation"
uv run python -m as_db.import_observations data/as_soil_moisture_2024_02_2026_08.csv "Soil Moisture"
```

Every CSV uses the same format: `Date`, followed by station names or codes, with
measurement values in the rows:

```csv
Date,Aasu Uh,Afono Uh
2026-01-01,26.5,27.1
2026-01-02,0,
```

The variable argument matches a name or code already in `variables`, ignoring
case and outer whitespace. Legacy Mesonet codes such as `Tair_1_Avg` also work.
Ambiguous names are rejected; use a unique code instead. Stations and variables
must already be registered. This importer writes only `observations`; it does
not create or modify metadata, sources, or upload records. It replaces the old
`as_db.insert_observations` module. Historical bootstrap uses this same importer
after its separate metadata setup.

Reporting intervals default to daily (`86400` seconds). Use `--interval-seconds 300`
for five-minute measurements or `--interval-seconds 0` for instantaneous
readings. Dates and naive timestamps are interpreted in each station's registered
timezone and stored as UTC with their local date. Timestamps represent interval
starts. Units are not converted; values must match the variable's registered unit.

Blank/NA cells are skipped and zero is retained. Empty unknown station columns
are ignored and reported. Populated unknown columns fail unless explicitly
omitted with `--skip-unknown-stations`. Invalid numbers, malformed CSV rows,
duplicate columns, and duplicate observations within a file fail the import.
Each file uses one transaction, so failures roll back all its readings.

Reruns skip existing observations by default. Pass `--on-conflict update` to
replace existing values, quality flags, local dates, and upload IDs. CLI imports
use quality flag `unreviewed` and no upload ID. A committed import prints counts
of written/existing readings and missing cells, plus ignored station columns.

Connections use `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_HOST`, and `POSTGRES_PORT` from `.env` in the current directory or its
parents; environment variables take precedence. Use `--env-file path/to/.env`
to select another file. The default host is `127.0.0.1` and port is `5432`.

The same importer is callable from Python:

```python
from as_db.import_observations import import_observations

result = import_observations('data/as_temperature_1999_12_2026_09.csv', 'Air Temperature')
print(result)
```

Python callers can also supply `connection`, `env_file`, `interval_seconds`,
`on_conflict`, `skip_unknown_stations`, `quality_flag`, and `upload_id`.
`ImportResult` contains `processed`, `written`, `skipped`, `missing`, and
`ignored_stations`. Supplying a connection uses a transaction or savepoint.

The separate `insert_stations` and `insert_variables` DataFrame helpers remain
available for metadata setup and preserve IDs when updating rows by code.

Run importer tests with `uv run python -m pytest tests/test_observation_csv.py
tests/test_dataframe_imports.py -q`. Database integration tests require
`AS_DB_TEST_DSN` pointing to a disposable database with the schema initialized.

## Persistence and initialization

The `postgres_data` named volume retains the database when containers are
recreated. `docker compose down` stops and removes the containers while keeping
the volume. Do not use `docker compose down -v` unless you intend to delete the
database volume. A persistent volume is not a backup; use `pg_dump` or an
appropriate backup service for recovery.

Initialization SQL runs only on a new, empty data directory. Rebuilding the image
does not apply new SQL to an existing database; apply subsequent schema changes
as migrations. Changing `.env` does not change an existing database password,
user, or database name. Change those through SQL after initialization.

This setup creates the server, extensions, seven weather/application tables,
and imports the bundled historical CSVs on first initialization. Do not change the
PostgreSQL major version on an existing volume without a planned database
upgrade or dump/restore. PostgreSQL image and TimescaleDB/PostGIS packages receive
updates; pin tested image digests and package versions for a reproducible
production release. After upgrading TimescaleDB packages on an existing database,
follow its upgrade procedure, including the required extension update.


## Automatic data collection

On a new database volume, Docker imports **all six CSVs in data/** with the
standard observation importer after creating tables and registering metadata.
The collector waits for database initialization and website account setup,
then runs startup backfill. Existing volumes retain their data.

For each variable, startup reads the maximum date in its historical CSV and
collects only later dates through yesterday in the station's timezone. It checks
existing daily observations before requesting data, so restarts resume missing
post-history dates without replacing archive values. Missing cells within the
historical CSV period are not fetched from providers. Different CSV end dates
are respected independently, including soil moisture's earlier cutoff.

### Provider routing

| Stations | First source | Fallback |
| --- | --- | --- |
| Five UH Mesonet stations | HCDP | Synoptic for missing daily summaries |
| Toa Ridge Wrcc | WRCC sam1, using the R-derived parser | None |
| Siufaga Wrcc | WRCC sam2, using the R-derived parser | None |
| Airport5101 | Synoptic NSTU daily products | None |

Mesonet variables are precipitation, air temperature, relative humidity, solar
radiation, and soil moisture. HCDP five-minute readings are aggregated into daily
rainfall totals and daily means using the original 95% completeness requirement.
Synoptic fills missing variable/dates without replacing successful HCDP values.
If Synoptic daily services are unavailable or incomplete for the UH sites, one
batched time-series request supplies reports for local-day aggregation: means
require 95% coverage and rainfall requires all 288 five-minute intervals.
Only the primary sensor (`*_set_1`, HCDP `*_1_*`) is mapped to each historical
variable; additional soil depths are not combined. Units must match the catalog.

WRCC downloads retain the previous metric daily rainfall, mean temperature, and
mean humidity parsing. Requests remain within calendar years and the requested
post-CSV date range. Airport collection uses station-local daily precipitation,
temperature, and humidity products; irregular airport reports are not converted
into assumed complete calendar-day summaries.

Stream-stage CSVs are imported at initialization. All stream stations are
currently inactive with no configured live source, so stream stage remains
historical-only. Stations with no configured provider, including Gml Smo, also
remain historical-only. The collector does not discover or register extra
stations or variables.

### Batches and daily schedule

HCDP and Synoptic use requests of up to **31 days**, with multiple variables
per request when their gaps align. HCDP paginates responses in up to 100,000
records per page. WRCC uses windows up to 366 days, split at year boundaries.
Synoptic's report fallback downloads a whole chunk, then computes separate
daily summaries locally; it does not issue one request per day during backfill.

Every downloaded window goes through the same observation importer and commits
atomically. Earlier successful windows survive a later failure. Duplicate,
invalid, unrequested, or non-finite readings fail the window. Missing readings
are not replaced with zero. Unavailable/insufficient data and provider errors
are reported in collector logs and its health status.

The Docker collector uses an APScheduler cron trigger at **03:00 every day**
in `COLLECTION_TIMEZONE` (default `Pacific/Pago_Pago`, American Samoa).
The scheduled job calls the same runner in `daily` mode to fetch **yesterday**
only. Container startup runs `backfill` mode to recover dates missed while the
worker was stopped. An advisory database lock prevents concurrent imports.

Set `HCDP_API` and `SYNOPTIC_TOKEN` in ignored `.env`. The collector receives
these credentials; the website keeps its separate read-only login. Legacy
`COLLECTION_PROVIDERS`, `SYNOPTIC_STATION_IDS`, `SYNOPTIC_DISCOVER`, and
`SYNOPTIC_BBOX` settings are no longer used. The old network discovery collector,
deployment switch helper, and three provider wrapper scripts have been removed.

```powershell
docker compose up --build -d
docker compose logs --tail 80 collector
docker compose run --rm collector python -m as_db.collection.runner --mode backfill
docker compose run --rm collector python -m as_db.collection.runner --mode daily
docker compose run --rm collector python -m as_db.collection.runner --provider wrcc --mode backfill
```

Outside Docker, use `uv run python -m as_db.collection.runner` with the same
arguments. Optional `--start YYYY-MM-DD --end YYYY-MM-DD` bounds are clamped to
the variable's post-CSV period and yesterday. Keep mappings for every data CSV
in `docker/history-imports.json`; an unmapped CSV fails setup explicitly.
The collector image includes the same CSVs and manifest as database bootstrap.

To use system cron instead of the Docker scheduler, invoke the same daily
runner once at 03:00 using the host's configured timezone:

```cron
0 3 * * * cd /path/to/as_db && /path/to/as_db/.venv/bin/python -m as_db.collection.runner --mode daily
```

Configure that host's schedule timezone as `Pacific/Pago_Pago` if matching the
Docker default. Choose one scheduler. After changing tokens, recreate the worker
with `docker compose up -d --force-recreate collector`.

Provider references: [HCDP API specification](https://github.com/HCDP/hcdp_api_docs/blob/master/hcdp_api.yaml),
[Synoptic daily statistics](https://docs.synopticdata.com/services/statistics),
and [Synoptic time series](https://docs.synopticdata.com/services/time-series).
The NOAA rolling-report diagnostic remains separately callable as
`python -m as_db.collection.noaa --date YYYY-MM-DD`.


### Analysis page

Open `/analysis` or use **Analysis** in the website navigation. Precipitation is
selected by default; all catalog variables and station statuses are available.
Apache ECharts renders a station record timeline (green present dates, gray
missing dates within each first/last record) with a year-range zoom slider,
and a station-by-year percentage heatmap. The same values are provided in an
expandable HTML table for keyboard and screen-reader access.

Availability is based on distinct local dates with non-null daily readings;
zero and quality-flagged values count as present. Annual percentages use the
whole 365/366-day year, including partial first/last record years. The current
year is measured through yesterday in Pacific/Pago_Pago. Years outside a
station-variable record are N/A. `/api/analysis/availability?variable_id=1`
returns compressed contiguous ranges and annual counts, cached for five minutes.
The existing SELECT-only website login is used; no database write permission
is needed for analysis.
