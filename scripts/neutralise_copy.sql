-- Cut a restored copy of the database off from every outside service.
--
-- Run it on the COPY, after restoring a dump into it and BEFORE its scrob container is
-- started (never on production). Scrob starts background jobs at startup that sync with
-- Jellyfin, Plex, Emby, Trakt, Simkl and others on their own, and refresh Simkl and
-- Trakt tokens; against a restored copy those jobs would use your real credentials,
-- could push watch events to your real accounts, and could replace a token that
-- production still relies on.
--
--   docker exec -i <test-db-container> psql -U scrob -d scrob -v ON_ERROR_STOP=1 < scripts/neutralise_copy.sql
--
-- It works on the table layout the dump came from, old or new: a column that does not exist
-- yet (a dump from an older version has not been migrated) is skipped. Afterwards no
-- connection has a usable address or token and nothing is scheduled. TMDB and the other
-- metadata keys are kept, so recommendations and artwork still work.
BEGIN;

DO $do$
DECLARE
  item record;
  changed bigint;
BEGIN
  FOR item IN
    SELECT * FROM (VALUES
      -- Media servers (Jellyfin, Plex, Emby, Nuvio, Stremio): an address nothing listens on,
      -- no tokens, and no automatic pull or push.
      ('media_server_connections', 'url',                  $s$'http://127.0.0.1:9'$s$),
      ('media_server_connections', 'token',                $s$''$s$),
      ('media_server_connections', 'plex_auth_token',      'NULL'),
      ('media_server_connections', 'auto_sync_interval',   'NULL'),
      ('media_server_connections', 'auto_push_interval',   'NULL'),
      ('media_server_connections', 'push_watched',         'FALSE'),
      ('media_server_connections', 'push_collection',      'FALSE'),
      ('media_server_connections', 'push_playback',        'FALSE'),
      ('media_server_connections', 'push_ratings',         'FALSE'),
      -- Trackers and other services, per user: forget the tokens, turn off automatic sync.
      ('user_settings', 'trakt_access_token',        'NULL'),
      ('user_settings', 'trakt_refresh_token',       'NULL'),
      ('user_settings', 'trakt_auto_sync_interval',  'NULL'),
      ('user_settings', 'simkl_access_token',        'NULL'),
      ('user_settings', 'simkl_refresh_token',       'NULL'),
      ('user_settings', 'simkl_auto_sync_interval',  'NULL'),
      ('user_settings', 'wetrakr_access_token',      'NULL'),
      ('user_settings', 'wetrakr_refresh_token',     'NULL'),
      ('user_settings', 'wetrakr_auto_sync_interval','NULL'),
      ('user_settings', 'mdblist_auto_sync_interval','NULL'),
      ('user_settings', 'bingebase_webhook_url',     'NULL'),
      ('user_settings', 'bingebase_api_key',         'NULL'),
      ('user_settings', 'radarr_url',                'NULL'),
      ('user_settings', 'radarr_token',              'NULL'),
      ('user_settings', 'sonarr_url',                'NULL'),
      ('user_settings', 'sonarr_token',              'NULL'),
      -- Radarr and Sonarr set for the whole server.
      ('global_settings', 'radarr_url',   'NULL'),
      ('global_settings', 'radarr_token', 'NULL'),
      ('global_settings', 'sonarr_url',   'NULL'),
      ('global_settings', 'sonarr_token', 'NULL')
    ) AS v(tbl, col, val)
  LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = item.tbl AND column_name = item.col
    ) THEN
      EXECUTE format('UPDATE public.%I SET %I = %s', item.tbl, item.col, item.val);
      GET DIAGNOSTICS changed = ROW_COUNT;
      RAISE NOTICE '% . %  ->  % row(s)', item.tbl, item.col, changed;
    ELSE
      RAISE NOTICE '% . %  does not exist in this layout, skipped', item.tbl, item.col;
    END IF;
  END LOOP;
END
$do$;

COMMIT;
