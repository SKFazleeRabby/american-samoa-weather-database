# DigitalOcean deployment and secure AI database access

This guide deploys the existing PostgreSQL 17 + TimescaleDB + PostGIS, React/FastAPI
website, scheduled collector, and pgAdmin to an Ubuntu Droplet. Commands below run
in Bash on the indicated Linux server unless marked PowerShell. Replace all
uppercase placeholders before running. No SSH private key belongs in this project.

**Your deployment context:** the Droplet belongs to your professor, and you have
SSH access only. You do not need his DigitalOcean account credentials to run the
application. SSH access does not necessarily include sudo or Docker access;
check those first. DigitalOcean Cloud Firewall rules, Droplet resizing, and
account-level backups are tasks for your professor or someone with delegated
DigitalOcean access. Nothing in this guide requires sharing his account password
or API token.

`ADMIN` below means your existing SSH username, not a DigitalOcean account name.
Use the supplied Droplet IP and SSH key/port. Keep using that account for
deployment; the dedicated AI tunnel account is created separately.

## 1. Access design

```text
AI process -> AI server 127.0.0.1:15432
           -> encrypted SSH connection (TCP 22)
           -> Droplet 127.0.0.1:5432 -> PostgreSQL

Everyone's browser -> http://DROPLET_IP:80 -> Caddy -> website:8000
Administrator browser -> SSH tunnel -> Droplet 127.0.0.1:5050 (pgAdmin)
```

The website UI, read API, and data downloads are public, without a login. No
domain is required: compose.public.yaml adds Caddy on port 80 for IP-based HTTP
access. The browser connection uses HTTP in this mode. Keep the existing
loopback bindings for PostgreSQL, pgAdmin, and the direct backend port; Caddy
proxies only the website. SSH encrypts the database connection between servers
and verifies the Droplet host key; database passwords remain required. The SSH
and database credentials are independent.

The AI uses a separate `weather_agent` login with SELECT on `public.stations`,
`public.variables`, and `public.observations`. It gets no grants on users, uploads,
API keys, sequences, or future application tables. The login has a five-connection
limit, 15-second statement timeout, three-second lock timeout, and 30-second idle
transaction timeout. Read-only defaults and timeouts can be changed by a SQL client;
table privileges enforce the prohibition on writes. This is not an isolation
sandbox for arbitrary untrusted SQL: PostgreSQL grants some functions and temporary
table access to PUBLIC. Use parameterized, bounded queries, small connection pools,
and application query validation. Do not expose an administrator connection to an LLM.

## 2. Prepare the Droplet

### Check your existing SSH access and server first

From Windows PowerShell:

```powershell
ssh ADMIN@DROPLET_IP
```

On the Droplet, run these inspection commands before installing or starting
anything. `sudo -v` may ask for your Linux account password; it does not ask for
your professor's DigitalOcean password.

```bash
whoami
id
cat /etc/os-release
uname -m
free -h
df -h /
sudo -v
sudo -l
command -v docker
docker --version
docker compose version
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
sudo ss -ltnp
sudo ufw status verbose
ls -ld /opt/as_db
```

A missing Docker command, inactive/uninstalled UFW, or nonexistent /opt/as_db is
useful information, not a reason to reset the server. This setup targets a
compatible Ubuntu x86-64 server (Ubuntu 24.04 LTS is a suitable baseline).
Check available resources: 2 vCPU / 4 GiB RAM is a starting point for this stack,
but existing workloads also need capacity. Your professor handles resizing if
needed; do not reinstall the operating system to match tdockerhis guide.

| Result | Next step |
| --- | --- |
| Your account can run the required sudo commands | Continue with the server steps below. |
| No sudo, but authorized Docker access already works | You can run Compose without sudo in a dedicated writable directory; a server administrator handles host firewall, SSH account/configuration, and system services. |
| Neither sudo nor Docker access | Upload the project to your home directory and give the remaining installation/start commands to your professor or server administrator. SSH alone cannot perform these privileged steps. |
| Ports 80 or 443 already have listeners | Use the existing web server integration below; do not start the public overlay yet. |
| Ports 5432, 5050, or 8080 are already occupied | Choose unused host ports in this project's .env before starting it; see the port notes below. |

Do not stop existing websites, replace a host reverse proxy, restart Docker, or
run Docker prune commands as part of this deployment. An existing /opt/as_db
must be inspected before use; do not overwrite another project's files or change
their ownership. If necessary, agree on a separate directory and use it in all
commands below. Keep the chosen directory consistent for updates and backups.

### Cloud firewall: your professor's part

Ask your professor to check whether a DigitalOcean Cloud Firewall is attached
and, if so, adjust its rules for the agreed website port. SSH and sudo on the
Droplet do not grant control of that account-level firewall. Preserve existing
rules used by his other services and users; these are the **additional access
requirements for this project**, not a replacement firewall policy:

| Direction | Port | Source / destination |
| --- | --- | --- |
| Inbound TCP | 22 (or your SSH port) | Preserve existing SSH access; allow your IP and AI server public egress IP |
| Inbound TCP | 80 | Everyone: 0.0.0.0/0 and ::/0 |
| Inbound TCP | 443 | Everyone, when enabling HTTPS with a domain later |
| Outbound | Defaults | Allow outgoing traffic for DNS, apt, image downloads, and data providers |

Do not add public firewall access for this project's database, pgAdmin, or direct
backend ports (defaults 5432, 5050, and 8080). Preserve unrelated services' rules.
If your SSH port
differs, update every tunnel command and systemd ExecStart with `-p PORT`.
For private addresses, use the actual source reachable by SSH. Do not remove your
current administrator access rule until a second SSH session succeeds.

You can send your professor this short request:

> I will deploy the public weather data website on the existing Droplet using
> my SSH account. Please check that TCP 80 is reachable from the internet, and
> that SSH is allowed from my workstation and the AI server's egress IP. Please
> preserve the current rules for other services. The project's database and
> pgAdmin will stay private. Please also confirm the deployment directory and
> whether Docker/sudo access is available to my account. If port 80 is already
> used, we should agree on routing or another public port first.

This is a message template for you to send; this guide does not contact him.
If no cloud firewall is attached, still inspect the host firewall and public
reachability. Local success at 127.0.0.1 does not prove internet access.

### Install only what is missing

If Docker is already installed and usable, keep the existing installation.
Otherwise, you or the server administrator can install Engine and its Compose
plugin using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
Use a current Engine (at least 28): earlier versions have a documented localhost
port reachability caveat. Verify:

```bash
sudo docker version
sudo docker compose version
sudo systemctl is-active docker
```

If Docker was newly installed and is not running, start/enable it with
`sudo systemctl enable --now docker`. Do not restart an already running daemon.
For a **new, unused** deployment directory, create it once:

```bash
if [ ! -e /opt/as_db ]; then
    sudo install -d -o "$USER" -g "$(id -gn)" -m 700 /opt/as_db
else
    ls -ld /opt/as_db
    echo 'Existing directory: verify that it belongs to this deployment before continuing.'
fi
```

Without sudo, use a dedicated directory such as `mkdir -p ~/as_db_weather`;
change the SCP destination and all subsequent paths accordingly. Omit sudo from
Docker commands only if your account already has authorized Docker access.

This guide uses `sudo docker`; membership in the docker group effectively gives
host administrator access. Never add the AI tunnel account to that group.

Docker-published ports can bypass UFW rules. Loopback bindings plus the
DigitalOcean firewall are intentional; an UFW rule alone is insufficient.

If UFW is already enabled and this deployment owns port 80, a server administrator
can add `sudo ufw allow 80/tcp`. Do not enable/reset UFW or remove existing rules
as a shortcut. Your professor must separately allow the port in any attached
DigitalOcean Cloud Firewall.

### If the Droplet already hosts a website

The supplied public overlay publishes ports 80 and 443. Use it only when both
are free and allocated to this project. If an existing Nginx, Apache, Caddy, or
container proxy owns either port, start just this application's private backend:

```bash
cd /opt/as_db
sudo docker compose up --build -d
curl --fail http://127.0.0.1:8080/api/health
```

Then have the existing web server's maintainer route the agreed public address
to `http://127.0.0.1:8080`. That upstream applies to a proxy running directly on
the host; a containerized proxy needs an explicitly configured route/network to
reach the backend. The current frontend expects the root path and `/api`, so do
not place it under `/weather/` without adapting the application. A second site
cannot independently own the same IP/port/root path as an existing website;
agree on a separate hostname, public port, or address with the maintainer.
If choosing another public port, update the proxy's host port mapping and both
firewalls, and use `http://DROPLET_IP:PORT` for public access. Do not use
compose.public.yaml unchanged while those ports are occupied.

If only this project's loopback ports conflict, change POSTGRES_PORT,
PGADMIN_PORT, or WEB_PORT in .env. A changed POSTGRES_PORT also requires updating
the remote destination in the SSH key restrictions, SSH server PermitOpen, all
tunnel commands, and the systemd tunnel template; database containers still use
internal port 5432. Update the website/pgAdmin checks and tunnel destinations
when changing their host ports. Keep every host binding at 127.0.0.1.

## 3. Transfer this project

Continue after the deployment directory and Docker/port access are established.
The examples below assume /opt/as_db and the default unused ports; use your
agreed directory and ports if different. For a shared reverse proxy, use the
base Compose startup above instead of the public overlay startup in section 4.

From this Windows project directory in PowerShell (replace ADMIN and DROPLET_IP):

```powershell
$archive = Join-Path $env:TEMP 'as-db-deploy.tar.gz'
tar -czf $archive --exclude=node_modules --exclude=__pycache__ --exclude=dist --exclude=*.tsbuildinfo --exclude=*.png --exclude=.env --exclude=.env.local --exclude=.env.production Dockerfile Dockerfile.web compose.yaml compose.public.yaml pyproject.toml uv.lock README.md .env.example .dockerignore .gitattributes docker src scripts web stations.csv variables.csv data docs deploy
scp $archive ADMIN@DROPLET_IP:/opt/as_db/as-db-deploy.tar.gz
ssh ADMIN@DROPLET_IP
```

On the Droplet:

```bash
cd /opt/as_db
tar -xzf as-db-deploy.tar.gz
rm as-db-deploy.tar.gz
umask 077
cp -n .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Generate a different random value for EACH of `POSTGRES_PASSWORD`,
`APP_DB_PASSWORD`, `AGENT_DB_PASSWORD`, and `PGADMIN_DEFAULT_PASSWORD` and enter
them using `nano .env`. Set `PGADMIN_DEFAULT_EMAIL`, `POSTGRES_DB=weather`,
`AGENT_DB_USER=weather_agent`, `PUBLIC_SITE_ADDRESS=:80`, and any collection tokens. Leave `POSTGRES_HOST`
at 127.0.0.1; Compose uses `db` internally. Default database port is 5432.
Never copy the workstation .env or share the administrator password with the AI.

## 4. Start and verify

```bash
cd /opt/as_db
sudo docker compose -f compose.yaml -f compose.public.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.public.yaml up --build -d
sudo docker compose -f compose.yaml -f compose.public.yaml ps -a
sudo docker compose -f compose.yaml -f compose.public.yaml logs --tail=80 db web-setup web collector caddy
curl --fail http://127.0.0.1:8080/api/health
sudo docker compose --profile agent run --rm agent-setup
```

First initialization imports the historical CSVs bundled in data/. Allow it to
finish; imports can take substantially longer than the normal healthcheck start
period. If dependencies initially report unhealthy, inspect the db logs, wait for
initialization to complete, then rerun the same public Compose up command. Do not delete
the volume to resolve a startup delay. `web-setup` exiting with code 0 is expected.
AI provisioning must exit successfully before using its credentials.

The collector runs at 03:00 in COLLECTION_TIMEZONE (default Pacific/Pago_Pago).
Inspect its logs/health after startup; upstream tokens are needed for relevant
providers. This schedule is independent of your server's clock timezone.

If agent setup rejects an existing role, choose a new dedicated AGENT_DB_USER.
If it reports inherited write/CREATE access, review PUBLIC grants before
continuing. Upgraded databases may need `REVOKE CREATE ON SCHEMA public FROM PUBLIC`
after checking whether any existing application relies on that permission.
Setup is transactional: a failure rolls back its changes.

### Existing workstation observations

Fresh deployment imports the bundled CSV archive; it does **not** copy your current
Docker volume or observations collected since those files were made. If those
records matter, pause source collection and migrate a complete database backup
instead of treating the fresh CSV import as the latest database.

Before migration, obtain source and target extension versions:

```bash
sudo docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname, extversion FROM pg_extension ORDER BY extname"'
```

Use matching PostgreSQL major and extension versions. TimescaleDB restores need
`timescaledb_pre_restore()` before `pg_restore` and `timescaledb_post_restore()`
afterward. Restore into a separate empty database with the extensions installed,
then verify counts/date ranges and switch POSTGRES_DB; do not overlay a dump onto
the already seeded archive. Use `pg_restore --no-owner --no-privileges` when
restoring into this deployment with newly provisioned roles, then rerun web-setup
and agent-setup to restore their explicit access grants. See the
[Timescale backup/restore procedure](https://www.tigerdata.com/docs/deploy/self-hosted/backup-and-restore/logical-backup).
The backup instructions below create a suitable archive; on Windows use
`docker compose cp` to transfer the binary archive rather than piping it through
PowerShell redirection. Keep the old database until the migration is verified.

## 5. Open the public website and private pgAdmin

Open **http://DROPLET_IP** from any browser. Everyone can browse observations and
download data without logging in, installing software, or opening an SSH tunnel.
The public site also includes the existing read-only API and /docs API reference.

Check on the Droplet:

These Caddy commands apply to the supplied public overlay. If you use the
professor's existing reverse proxy, use its agreed public URL and have its
maintainer check that proxy's logs instead.

```bash
curl --fail http://127.0.0.1/api/health
sudo docker compose -f compose.yaml -f compose.public.yaml logs --tail=80 caddy
```

Then check `http://DROPLET_IP/api/health` from a separate computer. If it is
unreachable, check the cloud firewall, any host firewall (allow TCP 80), Caddy
logs, and whether another server already occupies port 80. Confirm
`http://DROPLET_IP:5050` and direct TCP 5432/8080 remain unreachable externally.
Browsers never receive a database password; the backend uses its SELECT-only
website login. Downloads are available to everyone and consume Droplet bandwidth.

For **administrator-only pgAdmin access**, use SSH:

From your workstation in PowerShell (keep the command running):

```powershell
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:15050:127.0.0.1:5050 ADMIN@DROPLET_IP
```

Open http://127.0.0.1:15050 for pgAdmin. The pgAdmin browser password differs
from the database password. The public proxy has no pgAdmin route.

### Add a domain and HTTPS later

Point a domain's DNS A record at the Droplet's public IPv4 address. Add an AAAA
record only if that IPv6 address also reaches the Droplet. Set, for example,
`PUBLIC_SITE_ADDRESS=weather.example.com` in .env (hostname only, no scheme/path),
allow inbound TCP 443 from everyone as well as TCP 80, and run:

```bash
sudo docker compose -f compose.yaml -f compose.public.yaml up -d caddy
curl --fail https://weather.example.com/api/health
```

Caddy obtains and renews the domain certificate automatically and redirects HTTP
to HTTPS. Preserve the caddy_data volume across updates. Public browsing and
downloads remain available without authentication. IP-only mode in this supplied
configuration intentionally uses HTTP; merely replacing :80 with a bare IP does
not configure a publicly trusted IP certificate.

## 6. Set up the AI server SSH key

On the **AI server** (Ubuntu/systemd example):

```bash
sudo adduser --system --group --home /var/lib/weather-ai weather-ai
sudo install -d -o weather-ai -g weather-ai -m 700 /var/lib/weather-ai/.ssh
sudo -u weather-ai ssh-keygen -t ed25519 -N '' -f /var/lib/weather-ai/.ssh/id_ed25519
sudo cat /var/lib/weather-ai/.ssh/id_ed25519.pub
```

Only copy the public key to the Droplet. The private key stays on the AI server
with mode 600. This key has no passphrase so systemd can reconnect unattended;
its server-side account is restricted below. Keep the key out of the AI prompt,
source control, and logs.

On the **database Droplet**, in your existing administrator SSH session:

These account-creation and SSH configuration commands require sudo. If your
SSH account lacks those permissions, have your professor or the server
administrator perform this part. Keep using your own account for deployment;
do not put your personal deployment private key on the AI server.

```bash
sudo adduser --disabled-password --gecos '' weather-tunnel
sudo install -d -o weather-tunnel -g weather-tunnel -m 700 /home/weather-tunnel/.ssh
sudo nano /home/weather-tunnel/.ssh/authorized_keys
```

Paste this as ONE line, replacing AI_SERVER_PUBLIC_IP and the public key:

```text
from="AI_SERVER_PUBLIC_IP",restrict,port-forwarding,permitopen="127.0.0.1:5432" ssh-ed25519 AAAA_REPLACE_WITH_AI_PUBLIC_KEY weather-ai
```

The `from` IP must match the AI server's public egress IP (or actual source when
using a private SSH route). Then:

```bash
sudo chown weather-tunnel:weather-tunnel /home/weather-tunnel/.ssh/authorized_keys
sudo chmod 600 /home/weather-tunnel/.ssh/authorized_keys
sudo install -m 644 deploy/ssh/weather-tunnel.conf.example /etc/ssh/sshd_config.d/60-weather-tunnel.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T -C user=weather-tunnel,host=ai,addr=AI_SERVER_PUBLIC_IP | grep -E 'allowtcpforwarding|permitopen|maxsessions|passwordauthentication'
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Keep your administrator session open during validation. Confirm the effective
configuration shows local TCP forwarding only, PermitOpen 127.0.0.1:5432,
MaxSessions 0, and password authentication disabled. Global AllowUsers/AllowGroups
or Match rules on an existing server may also need to admit weather-tunnel.
MaxSessions 0 blocks shell/subsystem sessions while permitting SSH forwarding.

On the **AI server**, start a manual tunnel:

```bash
sudo -u weather-ai ssh -NT -i /var/lib/weather-ai/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=ask -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:15432:127.0.0.1:5432 weather-tunnel@DROPLET_IP
```

Compare the displayed Ed25519 host fingerprint with the one obtained on the
Droplet before accepting it. This creates known_hosts for the service. Keep the
tunnel open for the next checks, then press Ctrl+C before starting systemd.

## 7. Connect your AI application

Install psycopg in the AI application's environment (`pip install 'psycopg[binary]'`).
Store AGENT_DB_PASSWORD in that server's secret manager or restricted environment
file. Set only the dedicated reader password; do not copy the project .env.

```python
import os
import psycopg

with psycopg.connect(
    host="127.0.0.1", port=15432, dbname="weather", user="weather_agent",
    password=os.environ["AGENT_DB_PASSWORD"],
    connect_timeout=10, application_name="weather-ai",
    # SSH provides transport encryption; this connection terminates on local loopback.
    sslmode="disable",
) as conn:
    print(conn.execute("SELECT current_user, current_setting('transaction_read_only')").fetchone())
    print(conn.execute("""SELECT has_table_privilege(current_user,
        'public.observations','SELECT'), has_table_privilege(current_user,
        'public.observations','INSERT'), has_table_privilege(current_user,
        'public.api_keys','SELECT')""").fetchone())  # expected: (True, False, False)
    rows = conn.execute("""SELECT observed_at, station_id, variable_id, value
        FROM public.observations WHERE observed_at >= %s::timestamptz
        ORDER BY observed_at DESC LIMIT %s""", ("2026-09-01T00:00:00Z", 100)).fetchall()
```

Use your actual database/role names if changed. SQLAlchemy equivalent:
`postgresql+psycopg` with host 127.0.0.1 and port 15432; build credentials using
`sqlalchemy.engine.URL.create` to handle password escaping. Keep the connection
pool small (for example pool_size=2, max_overflow=0, pool_pre_ping=True).

If the AI application runs in Docker, its 127.0.0.1 is its own container. On a
Linux AI host you can run the application with host networking to reach this
loopback tunnel, accepting that host-network access boundary, or design a shared
network namespace for a tunnel sidecar. Do not solve this by publishing 15432
on every interface. Treat local processes on the AI host as within the same
trust boundary; database credentials are still required.

## 8. Keep the tunnel running after reboot

Copy deploy/systemd/weather-db-tunnel.service.example from this project to the
AI server. Replace DROPLET_IP in the file, then on the **AI server**:

```bash
sudo install -m 644 weather-db-tunnel.service.example /etc/systemd/system/weather-db-tunnel.service
sudo nano /etc/systemd/system/weather-db-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now weather-db-tunnel
sudo systemctl status weather-db-tunnel
sudo journalctl -u weather-db-tunnel -n 50 --no-pager
```

The service validates known_hosts, binds only local port 15432, detects lost SSH
connections, and retries after failures. Application connections interrupted by
a tunnel outage must reconnect. Rerun the Python check after starting the service.

## 9. Backups, updates, and rotation

On the Droplet, make a consistent logical archive (no passwords in commands):

```bash
cd /opt/as_db
umask 077
mkdir -p backups
backup_name="weather-$(date -u +%Y%m%dT%H%M%SZ).dump"
sudo docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/weather-backup.dump'
sudo docker compose cp db:/tmp/weather-backup.dump "backups/$backup_name"
sudo chown "$USER:$(id -gn)" "backups/$backup_name"
chmod 600 "backups/$backup_name"
sudo docker compose exec -T db rm /tmp/weather-backup.dump
```

Copy backups off the Droplet to restricted, encrypted storage. Schedule daily
backups with your backup service or scheduler, and perform a restore drill into
an isolated matching PostgreSQL/TimescaleDB instance. A successful dump alone
does not prove restorability. pg_dump excludes cluster roles; provision website
and AI roles again after restoring. Preserve .env securely and record extension
versions. Droplet snapshots complement database-aware backups.

Your professor manages DigitalOcean snapshots, account-level backup settings,
and monitoring through his account. Logical dumps and off-server copies can be
managed through your SSH access with the database/Docker permissions above.
Agree on an off-server destination and who checks backup/restore success.

Before updates, back up; keep the Compose project in /opt/as_db so its named
volume identity remains consistent. Rebuild with
`sudo docker compose -f compose.yaml -f compose.public.yaml up --build -d`.
If using the existing server reverse proxy, keep using base
`sudo docker compose up --build -d` instead; do not introduce the public overlay
during an update. Run commands from your agreed project directory and avoid
changing an existing Compose project name, which would select different volumes.
Do not run `docker compose down -v` or delete postgres_data: that removes the archive.
Do not change the PostgreSQL major version or TimescaleDB extension version
without a planned migration. The Dockerfile installs extension packages from an
apt repository, so record versions before rebuilds and follow extension upgrade
instructions if package versions change.

To rotate the AI database password, edit AGENT_DB_PASSWORD in .env, rerun
`sudo docker compose --profile agent run --rm agent-setup`, update the AI server
secret, and reconnect its pool. Password rotation does not disconnect existing
sessions. To revoke access immediately, remove its SSH authorized key, stop its
tunnel service, and use an administrator database shell to run `ALTER ROLE
weather_agent NOLOGIN` and `SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE usename='weather_agent' AND pid<>pg_backend_pid()` (adjust role if renamed).
Stop active weather-tunnel SSH connections on the Droplet as well if the key was
compromised. Changing POSTGRES_PASSWORD in .env does not change an existing
database administrator password; use PostgreSQL password management for that.

## References

- [DigitalOcean firewall rules](https://docs.digitalocean.com/products/networking/firewalls/how-to/configure-rules/)
- [Docker published ports and localhost caveat](https://docs.docker.com/engine/network/port-publishing/)
- [Docker and UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
- [PostgreSQL SSH tunnels](https://www.postgresql.org/docs/17/ssh-tunnels.html)
- [OpenSSH forwarding restrictions](https://man.openbsd.org/sshd_config)
- [PostgreSQL default privileges](https://www.postgresql.org/docs/17/ddl-priv.html)
- [Caddy HTTP/HTTPS configuration](https://caddyserver.com/docs/automatic-https)

## Local validation

Run `uv run python -m pytest tests -q` and `docker compose config --quiet`.
Also run `docker compose -f compose.yaml -f compose.public.yaml config --quiet`
for the public deployment. With Docker running, validate the proxy config with
`docker compose -f compose.yaml -f compose.public.yaml run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`.
Privilege integration tests require AS_DB_SECURITY_TEST_DSN pointing to a fresh,
empty, disposable PostgreSQL 17 database with a superuser connection. They create
test tables/roles transactionally and roll them back; never use a production DSN.
Run them with `uv run python -m pytest tests/test_agent_setup.py -q`.
