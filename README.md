# American Samoa Weather Database

Browse, download, and explore daily weather and historical stream-stage data
from American Samoa.

## Get started

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
start it with Linux containers enabled. Run these commands from the project folder.

**First-time setup:** copy the example settings file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`,
`PGADMIN_DEFAULT_PASSWORD`, and `PGADMIN_DEFAULT_EMAIL`. Use different passwords.
For automatic collection, also set `HCDP_API` and `SYNOPTIC_TOKEN`.

Start the application:

```powershell
docker compose up --build -d
```

First startup imports all six historical CSVs in `data/`, then downloads
available daily summaries after each variable's final CSV date through yesterday.
This may take a few minutes. Existing database data is kept on later starts.

## Use the website

Open [localhost:8080](http://localhost:8080).

1. Select stations on the map or in the filters.
2. Choose variables, a date range, and the Daily reporting interval.
3. Browse the readings or download a ZIP containing one CSV per variable.
4. Open **Analysis** to explore data coverage by station and year.
5. Open **Stations** to view and download station information.

Downloaded CSVs have a `Date` column and one column per station.
Missing readings are blank; zero is a valid measurement.

## Automatic updates

The collector runs every day at **3:00 AM American Samoa time** to collect
yesterday's daily summaries. Change `COLLECTION_TIMEZONE` in `.env` to adjust
the schedule's timezone.

- **UH Mesonet:** HCDP first, with Synoptic as a fallback.
- **Siufaga and Toa Ridge:** WRCC.
- **Airport5101:** Synoptic.
- **Stream stage:** historical data only.

Downloads use batches, and existing readings are preserved.
Restarting the collector also checks for missing dates after the historical CSVs.

To check progress:

```powershell
docker compose logs --tail 80 collector
```

## Import your own CSV

Use the same layout for every variable:

```csv
Date,Aasu Uh,Afono Uh
2026-01-01,26.5,27.1
2026-01-02,0,
```

Stations and the variable must already be registered. Values must use the
variable's registered units. With the application running, copy your file
into the collector and import it using the variable name or code:

```powershell
docker compose cp observations.csv collector:/tmp/observations.csv
docker compose exec collector python -m as_db.import_observations /tmp/observations.csv "Air Temperature"
```

Imports default to daily data, skip blank cells, and preserve existing readings.
Add `--on-conflict update` only when you intend to replace existing values.

## Everyday commands

```powershell
docker compose ps                     # Check service status
docker compose logs --tail 80 web     # View website logs
docker compose stop                  # Stop the application
docker compose up -d                 # Start it again
```

Database data persists when containers stop. Avoid `docker compose down -v`:
it deletes the database volume.

For database administration, open [pgAdmin](http://localhost:5050) and sign in
with the pgAdmin credentials from `.env`.

For server hosting, see the [deployment guide](docs/DEPLOY_DIGITALOCEAN.md).
For existing installations, see the [collection update notes](docs/SYNOPTIC_UPDATE.md).
