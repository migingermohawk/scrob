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
-- Afterwards no connection has a usable address or token and nothing is scheduled. TMDB and
-- the other metadata keys are kept, so recommendations and artwork still work.
BEGIN;

-- Media servers (Jellyfin, Plex, Emby, Nuvio, Stremio): an address nothing listens on, no
-- tokens, and no automatic pull or push.
UPDATE media_server_connections
SET url = 'http://127.0.0.1:9',
    token = '',
    plex_auth_token = NULL,
    auto_sync_interval = NULL,
    auto_push_interval = NULL,
    push_watched = FALSE,
    push_collection = FALSE,
    push_playback = FALSE,
    push_ratings = FALSE;

-- Trackers and other services, per user: forget the tokens and turn off automatic sync.
UPDATE user_settings
SET trakt_access_token = NULL, trakt_refresh_token = NULL, trakt_auto_sync_interval = NULL,
    simkl_access_token = NULL, simkl_refresh_token = NULL, simkl_auto_sync_interval = NULL,
    wetrakr_access_token = NULL, wetrakr_refresh_token = NULL, wetrakr_auto_sync_interval = NULL,
    mdblist_auto_sync_interval = NULL,
    bingebase_webhook_url = NULL, bingebase_api_key = NULL,
    radarr_url = NULL, radarr_token = NULL,
    sonarr_url = NULL, sonarr_token = NULL;

-- Radarr and Sonarr set for the whole server.
UPDATE global_settings
SET radarr_url = NULL, radarr_token = NULL,
    sonarr_url = NULL, sonarr_token = NULL;

COMMIT;
