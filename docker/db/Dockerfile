# Postgres 16 + PostGIS 3.5 (base image) + pgvector (from the PGDG apt repo the
# official postgres image already configures). pg_trgm ships with Postgres contrib.
FROM postgis/postgis:16-3.5

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
