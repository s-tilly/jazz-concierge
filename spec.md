# Technical & Functional Specification: Jazz Concierge Agent (`MilesAgent`)

## 1. Context, Spec-Driven Approach & Repository Standards

This document is the complete, self-contained **Technical and Functional Specification** for **`jazz-concierge`** (`MilesAgent`), an autonomous multi-agent jazz discovery and curation system written in Python.

Designed for **Spec-Driven Development / Vibecoding** with **Google Antigravity** and **Gemini**, this specification contains every architectural contract, tool schema, data model, infrastructure definition, and testing rule required to generate or regenerate the entire project reproducibly from scratch.

### Repository & Security Hygiene Rules
* **Tracked vs. Ignored Files:**
  * `spec.md`, `README.md` (English), and `README-fr.md` (French translation) are version-controlled at the root of the public GitHub repository.
  * `.env` (real credentials and cloud project IDs), local working documents (`*.pdf`), and runtime SQLite databases (`*.db`) are strictly excluded via `.gitignore`.
* **Configuration Neutrality:** Versioned files (`.env.example`, `infra/`, `README.md`, `README-fr.md`, tests) must use generic placeholders only (e.g., `your-gcp-project-id`) and never contain hardcoded API keys, real cloud project identifiers, internal URLs, or personal/corporate email addresses (commits use a public GitHub `noreply` identity).
* **Root-Level Repository Layout:** All project files live directly at the root of the Git repository (not nested inside a subfolder).

---

## 2. Project Summary & Core Parameters

| Parameter | Selected Design |
|---|---|
| **Project Name** | `jazz-concierge` |
| **Category** | Concierge Agents |
| **Agent Framework** | **Google ADK-Python** (`google-adk`) + `google-genai` SDK |
| **Public Gemini 4 Models** | **`gemini-4-hydrogen`**, **`gemini-4-neon`**, **`gemini-4-carbon`** via Vertex AI (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`), fully overridable via environment variables |
| **Data Layer** | **Hybrid Strategy Pattern**: live Spotify Web API client (catalog enrichment + public playlist audit via *Client Credentials*) when credentials are present in `.env`; automatic, seamless fallback to static JSON catalogs (`data/jazz_vault.json` and `data/sample_playlist.json`) otherwise |
| **Secret Management** | Local `.env` (gitignored) + **Google Cloud Secret Manager** integration (`src/config/secrets.py`), zero hardcoded secrets |
| **Memory & Context** | Asynchronous SQLite database (`src/memory/user_profile.db`), persistent session state, and ADK history compaction (`EventsCompactionConfig` / sliding window) |
| **Guardrails & HITL** | ADK Policy/Guardrail plugin & callbacks + explicit **Human-in-the-Loop** confirmation hooks for destructive/high-impact actions |
| **Observability** | Structured JSON logging, explicit *Intent vs. Outcome* capture, **OpenTelemetry** distributed tracing, and **active PII redaction** |
| **User Interfaces** | Interactive **Streamlit** Web UI (`src/app.py`) + interactive Terminal **CLI** (`src/cli.py`) + native `adk web` / `agents-cli` compatibility |
| **Infra, Eval & CI/CD** | **Terraform** IaC (`infra/`), golden-dataset evaluation harness (`tests/eval/`), offline `pytest` suite, `Dockerfile`, GitHub Actions (`.github/workflows/ci.yml`), and `agents-cli` workflows |

---

## 3. AgentOps Engineering Requirements Matrix

The system implements 19 production-grade AgentOps engineering standards across 5 core pillars:

### Pillar 1: Tool & Interface Design
| Requirement | Implementation in `jazz-concierge` |
|---|---|
| **Comprehensive Tool Docstrings** | Every ADK tool function includes a thorough Google-style docstring describing its domain purpose, typed parameters, return payload structure, and usage guidance for the LLM. |
| **Descriptive Naming** | Action-oriented, domain-specific tool names: `analyze_playlist`, `search_artist_discography`, `trace_artist_lineage`, `find_subgenre_bridges`, `sync_user_taste_profile`, `reset_user_taste_profile`, `export_curated_playlist`. |
| **Explicit JSON Schemas** | Strict input and output validation using Pydantic v2 models (`ConfigDict(extra="forbid")`) producing explicit JSON Schemas that constrain tool arguments and outputs. |
| **Guided Error Handling** | Tools never crash with raw tracebacks to the LLM: when an artist is missing, a filter is invalid, or an external API restricts access, tools return a structured `GuidedErrorResponse` (`status="error"`, `error_code`, `recovery_instructions`) guiding the LLM toward valid alternatives (e.g., fuzzy artist matches or valid enum values). |

### Pillar 2: Context & Memory
| Requirement | Implementation in `jazz-concierge` |
|---|---|
| **Robust System Instructions** | Each of the 3 agents is governed by an explicit **Constitution** in its system prompt (`Persona`, `Domain Knowledge`, `Behavioral Constraints`, `Output Protocol`). |
| **History Compaction** | Context bloat management via ADK `EventsCompactionConfig` combined with token/turn sliding-window summarization to keep multi-turn listening consultations lean. |
| **Persistent Session State** | Multi-session SQLite persistence (`src/memory/sqlite_memory.py` synchronized with ADK session `state`) retaining user expertise level, harmonic preferences, sonic fingerprint, and listening history across turns and restarts. |
| **Async Memory Operations** | Profile writes, playlist audit persistence, and history consolidation execute asynchronously (`async def` / `aiosqlite` / non-blocking `asyncio` tasks) so UI rendering and agent streaming never block. |

### Pillar 3: Orchestration & Logic
| Requirement | Implementation in `jazz-concierge` |
|---|---|
| **Multi-Agent Patterns** | Cooperative ADK multi-agent architecture combining a coordinator and specialized pipeline (`JazzProfilerAgent` → `JazzMusicologistAgent` → `JazzCuratorAgent`) rather than a monolithic prompt. |
| **Strategic Model Routing** | Cognitive-tier routing across the **Gemini 4** family: `gemini-4-hydrogen` (fast conversational profiling & tool dispatch), `gemini-4-neon` (musicological lineage & structured catalog reasoning), `gemini-4-carbon` (deep editorial curation, anti-duplicate verification & listening notes). |
| **Guardrails & Policy Plugins** | Custom ADK `BasePlugin` and lifecycle callbacks (`before_model_callback`, `after_model_callback`, `before_tool_callback`) enforcing prompt-injection / off-topic guardrails and self-checking against duplicate album recommendations. |
| **Human-in-the-Loop Hooks** | Explicit confirmation gate (`FunctionTool(..., require_confirmation=True)` + UI/CLI confirmation check) pausing execution for human approval before high-impact operations such as wiping persistent memory (`reset_user_taste_profile`). |

### Pillar 4: Observability & Tracing
| Requirement | Implementation in `jazz-concierge` |
|---|---|
| **Structured JSON Logging** | 100% structured JSON logs (`python-json-logger`) capturing ISO timestamp, `trace_id`, `span_id`, `agent_name`, `model_id`, latency, and domain metadata (zero ad-hoc `print()` debugging). |
| **Intent vs. Outcome Capture** | Agent and tool callbacks explicitly log the **intended action** prior to execution (`event="tool_intent"`, planned tool & sanitized arguments) and the **actual outcome** afterward (`event="tool_outcome"`, status, summary & duration in ms). |
| **Distributed Tracing** | **OpenTelemetry** (`opentelemetry-sdk`) instrumentation linking parent and child spans end-to-end from user query through sub-agents and tools to final answer, with optional Google Cloud Trace export. |
| **PII Redaction** | Active scrubbing pipeline (`src/telemetry/pii_redactor.py`) redacting emails, phone numbers, API tokens, Spotify share tracking tokens (`?si=...`), IP addresses, and personal identifiers before writing to JSON logs or SQLite storage (with optional Google Cloud DLP hook). |

### Pillar 5: Infrastructure & CI/CD
| Requirement | Implementation in `jazz-concierge` |
|---|---|
| **Automated Evaluation Suites** | Automated evaluation harness (`tests/eval/`) backed by a **golden dataset** (`golden_dataset.json` + `eval_config.yaml`) measuring trajectory and schema regressions statically/offline alongside `pytest` unit tests. |
| **Infrastructure as Code** | Complete **Terraform** configuration (`infra/main.tf`, `variables.tf`, `outputs.tf`) provisioning Cloud Run (IAM-authenticated private mode by default or public mode via variable), Secret Manager, and Vertex AI APIs, paired with a `Dockerfile` and documented `agents-cli` workflows. |
| **Secure Secret Management** | Zero hardcoded credentials; centralized secret loader (`src/config/secrets.py`) supporting **Google Cloud Secret Manager** in cloud deployments with seamless fallback to local `.env` in development and offline defaults in CI. |

---

## 4. Environment Configuration (`.env`), GCP Auth, Cloud Run & Gemini 4 Models

Configuration is loaded centrally via `src/config/settings.py` and `src/config/secrets.py` (Secret Manager + `python-dotenv`).

### `.env.example` Template (and Local `.env` Structure)

```dotenv
# --- Google Cloud / Vertex AI Configuration (Gemini Calls) ---
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global

# Optional: Alternative API key auth if not using Vertex AI
GOOGLE_API_KEY=

# Optional: Fetch secrets from Google Cloud Secret Manager in production
USE_SECRET_MANAGER=FALSE

# --- Public Gemini 4 Models (Overridable) ---
# If GEMINI_MODEL is set, it acts as an optional global override;
# otherwise each agent routes to its dedicated tier below:
GEMINI_MODEL=
PROFILER_MODEL=gemini-4-hydrogen
MUSICOLOGIST_MODEL=gemini-4-neon
CURATOR_MODEL=gemini-4-carbon

# --- Spotify Web API Configuration (Optional) ---
# When SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET (or SPOTIFY_API_KEY) are set,
# SpotifyDataProvider is enabled. When empty or omitted, the app automatically
# falls back to data/jazz_vault.json and data/sample_playlist.json.
SPOTIFY_API_KEY=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8501/callback

# --- Persistence & Observability ---
SQLITE_DB_PATH=src/memory/user_profile.db
LOG_LEVEL=INFO
OTEL_EXPORTER_GCP_TRACE=FALSE
```

### GCP Authentication (ADC) & Cloud Run Exposure Modes
1. **Local Authentication for Vertex AI / Gemini Calls:**
   * Uses **Application Default Credentials (ADC)** via `gcloud` (no service account JSON key files stored on disk):
     ```bash
     gcloud auth login
     gcloud config set project <your-gcp-project-id>
     gcloud auth application-default login
     gcloud auth application-default set-quota-project <your-gcp-project-id>
     ```
2. **Flexible Cloud Run Deployment (Private IAM + HTTPS Proxy vs. Public Endpoint):**
   * Terraform (`infra/variables.tf`) exposes `allow_public_access` (`false` by default for enterprise organizations enforcing *Domain Restricted Sharing* where `allUsers` is disallowed; `true` for open internet deployments).
   * In private IAM mode (`allow_public_access = false`), access is performed cleanly via **authenticated HTTPS forwarding / tunneling**:
     * Local developer / demo tunnel: `gcloud run services proxy jazz-concierge --project=<your-gcp-project-id> --region=us-central1 --port=8080` (automatically injects IAM OIDC credentials over HTTPS and binds `http://127.0.0.1:8080`),
     * Or behind an HTTPS reverse-proxy / Load Balancer with Identity-Aware Proxy (IAP) or `Authorization: Bearer $(gcloud auth print-identity-token)`.

### Strategic Model Routing & Override Resolution
1. **Per-agent default routing:**
   * `JazzProfilerAgent` → `os.getenv("PROFILER_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-4-hydrogen"`
   * `JazzMusicologistAgent` → `os.getenv("MUSICOLOGIST_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-4-neon"`
   * `JazzCuratorAgent` → `os.getenv("CURATOR_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-4-carbon"`
2. Official model reference: `https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/google-models`.

---

## 5. Aesthetic Profiling & Listening Spectrum

Rather than rigid questionnaires, the agent conducts **contextual conversational profiling** combined with optional playlist auditing across 4 dimensions:

### A. Jazz Knowledge Level (4-Tier Scale)
* **Beginner (`beginner`):** Knows nothing about jazz at all (zero artist names, zero standards, zero subgenre familiarity). The agent avoids unexplained technical jargon, anchors recommendations in the listener's general everyday music tastes (pop, rock, classical, soul, film scores, hip-hop, electronic) or mood (relaxing, focus, warm evening, energetic), and selects ultra-accessible, melodic entry points.
* **Novice (`novice`):** Recognizes a few iconic names or famous standards (e.g., Miles Davis - *So What*, Dave Brubeck - *Take Five*, Nina Simone, Chet Baker) without deep historical context. The agent emphasizes approachable storytelling and essential cornerstone albums.
* **Intermediate (`intermediate`):** Regular jazz listener comfortable with major eras (Swing, Bebop, Cool, Hard Bop). The agent targets landmark label sessions (Blue Note, Prestige, Riverside, Impulse!), celebrated sidemen, and deeper catalog gems.
* **Connoisseur (`connoisseur`):** Seeks rare pressings, spiritual jazz, post-bop deep cuts, or American, European, and Japanese avant-garde sessions. The agent adopts expert musicological depth and traces intricate historical lineages.

### B. Harmonic Complexity & Historical Era
* **Roots & Consonance (`roots_dixieland` / `standard_swing`):** Traditional Swing, Blues, Dixieland, Early Jazz, classic tonal Hard Bop (Louis Armstrong, Sidney Bechet, Duke Ellington, Count Basie, Ella Fitzgerald).
* **Modernity & Dissonance (`modal_modern` / `avant_garde`):** Modal Jazz, Post-Bop, Free Jazz, Contemporary Avant-Garde bridges (Miles Davis Second Quintet, John Coltrane, Ornette Coleman, Cecil Taylor, Sun Ra, Anthony Braxton, aesthetic bridges to Pierre Boulez or Olivier Messiaen).

### C. Sonic Texture & Ensemble Format
* **Solo Instruments (`solo`):** Unaccompanied piano (Keith Jarrett, Thelonious Monk, Art Tatum), solo guitar (Joe Pass), solo saxophone.
* **Small Ensembles (`small_ensemble`):** Trios, Quartets, Quintets (Bill Evans Trio, Ahmad Jamal, Miles Davis Quintets, John Coltrane Quartet).
* **Large Ensembles (`big_band`):** Big Bands & Third Stream orchestras (Count Basie, Duke Ellington, Gil Evans, Thad Jones/Mel Lewis, Maria Schneider).

### D. Public Spotify Playlist Audit (Sonic Fingerprint)
In addition to or instead of self-assessment, the user can provide the **URL or URI of a public Spotify playlist** (via Streamlit's *"Analyze my playlist"* widget or directly inside chat/CLI):
* Using standard server-to-server *Client Credentials* (no user OAuth login required), the system extracts tracks, artists, dominant genres, and tempo/energy characteristics.
* It computes a structured **Sonic Fingerprint**:
  * **Automatic jazz tier calibration:** e.g., 0% jazz tracks → `beginner`; mainstream vocal/cool standards → `novice`; classic Bebop/Hard Bop catalog → `intermediate`; Free/Spiritual/Avant-Garde → `connoisseur`.
  * **Non-jazz to jazz stylistic bridges:** e.g., Progressive/Psychedelic Rock → Jazz-Rock Fusion or modern European piano trios (Mahavishnu Orchestra, Esbjörn Svensson Trio); Soul/R&B → Soul Jazz & melodic Hard Bop (Grant Green, Nina Simone, Roy Hargrove); Funk → Jazz-Funk (Herbie Hancock *Head Hunters*, Donald Byrd); Impressionist Classical → Lyrical Piano Trio & Third Stream (Bill Evans, Keith Jarrett, Gil Evans).

---

## 6. Software Architecture & Multi-Agent Orchestration

### Orchestration Diagram

```
              ┌──────────────────────────────────────────────┐
              │       User Interface (Streamlit / CLI)       │
              │    + Optional "Analyze my playlist" Widget   │
              └──────────────────────┬───────────────────────┘
                                     │ Query / Spotify URL + PII & Policy Guardrails
                                     ▼
              ┌──────────────────────────────────────────────┐
              │             JazzProfilerAgent                │
              │  - Model: gemini-4-hydrogen                  │
              │  - Role: Welcome constitution, playlist      │
              │    audit (`analyze_playlist`), tier/taste    │
              │    extraction & HITL gate on profile reset   │
              └──────────────┬──────────────────▲────────────┘
                             │                  │
           Async SQLite Sync │                  │ Profile, Sonic Fingerprint & History
                             ▼                  │
              ┌─────────────────────────────────┴────────────┐
              │     Async Persistence & ADK Session State    │
              │     src/memory/user_profile.db (PII-clean)   │
              └─────────────────────────────────┬────────────┘
                             │ Structured State │
                             ▼                  ▼
              ┌──────────────────────────────────────────────┐
              │           JazzMusicologistAgent              │
              │  - Model: gemini-4-neon                      │
              │  - Role: Jazz genealogy navigation, genre    │
              │    bridges & discography candidate search    │
              └──────────────────────┬───────────────────────┘
                                     │ Qualified Candidates
                                     ▼
              ┌──────────────────────────────────────────────┐
              │              JazzCuratorAgent                │
              │  - Model: gemini-4-carbon                    │
              │  - Role: Anti-duplicate history check,       │
              │    tailored listening notes & playlist export│
              └──────────────────────┬───────────────────────┘
                                     │
                                     ▼
              ┌──────────────────────────────────────────────┐
              │    Enriched Final Response + JSON/MD Export  │
              └──────────────────────────────────────────────┘
```

### Detailed Role of the 3 ADK Agents
1. **`JazzProfilerAgent` (`gemini-4-hydrogen`):**
   * Welcomes the listener, detects initiation tier (`beginner`, `novice`, `intermediate`, `connoisseur`), ensemble preferences, and harmonic appetite.
   * Detects any public Spotify playlist URL or URI in user messages, invokes `analyze_playlist`, and persists both the Sonic Fingerprint and taste profile asynchronously into SQLite (`sync_user_taste_profile`) and `tool_context.state`.
   * Triggers a *Human-in-the-Loop* confirmation stop via `reset_user_taste_profile` whenever a user asks to wipe their stored profile/history.
   * Hands off a structured recommendation brief to the musicology and curation pipeline.
2. **`JazzMusicologistAgent` (`gemini-4-neon`):**
   * Queries the active `DataProvider` (Spotify or offline JSON) via `search_artist_discography`, `trace_artist_lineage`, and `find_subgenre_bridges`.
   * Selects 3 to 5 candidate albums with rigorous musicological and stylistic rationale.
3. **`JazzCuratorAgent` (`gemini-4-carbon`):**
   * Verifies candidates against `listening_history` to prevent repetitive recommendations.
   * Crafts engaging, level-appropriate listening notes (`beginner` through `connoisseur`) and formats shareable exports via `export_curated_playlist`.

---

## 7. ADK Tools Specification (`FunctionTool` & Pydantic Schemas)

Every tool features exhaustive docstrings, strict Pydantic input/output validation, `GuidedErrorResponse` recovery hints, OpenTelemetry spans, and structured `intent` → `outcome` JSON logs:

1. **`analyze_playlist(playlist_url_or_id: str, user_id: Optional[str] = None, tool_context: Optional[ToolContext] = None)` *(async)***
   * **Pydantic Input Validation (`PlaylistAuditInput`):** accepts a Spotify public playlist URL (`https://open.spotify.com/playlist/<id>...`), URI (`spotify:playlist:<id>`), 22-char Base62 ID, or `"sample"` demo alias; automatically strips `?si=...` tracking parameters.
   * **Output (`PlaylistAnalysisReport`):** playlist title, analyzed track count, top artists, dominant genres, estimated BPM & energy/tempo profile, inferred jazz knowledge level (`beginner` to `connoisseur`), suggested harmonic/formation preferences, Sonic Fingerprint summary, and `BridgeRecommendation` list.
   * **Dual State Persistence:** asynchronously saves the Sonic Fingerprint to SQLite (`playlist_audits` + `users.sonic_fingerprint_json`) and injects `tool_context.state["sonic_fingerprint"]` for immediate downstream agent access.
   * **Guided Error Handling:** if the URL is malformed or points to a private / Spotify-owned algorithmic playlist restricted by the Spotify API (`404`), returns actionable `recovery_instructions` advising a user-created public playlist or `"sample"`.
2. **`search_artist_discography(artist: str, group_format: Optional[str] = None, era: Optional[str] = None, accessibility_level: Optional[str] = None, limit: int = 5)`**
   * Searches landmark albums and key tracks for an artist with optional filtering by ensemble format (`solo`, `small_ensemble`, `big_band`), decade/era, or accessibility tier.
   * When an artist is not found, returns fuzzy/stylistically close artist suggestions inside `recovery_instructions`.
3. **`trace_artist_lineage(artist: str, relationship: Literal["influences", "collaborators", "disciples", "all"] = "all")`**
   * Explores a musician's genealogical tree (mentors, collaborators, heirs) with historical justifications.
4. **`find_subgenre_bridges(anchor_genre_or_composer: str, target_vibe: str = "any")`**
   * Builds stylistic bridges from non-jazz or historical anchors (e.g., `"Progressive Rock"`, `"Funk"`, `"Claude Debussy"`, `"Pierre Boulez"`, `"Soul"`, `"Dixieland"`) to gateway jazz albums.
5. **`sync_user_taste_profile(user_id: str, knowledge_level: Optional[Literal["beginner", "novice", "intermediate", "connoisseur"]] = None, harmonic_preference: Optional[str] = None, formation_preference: Optional[str] = None, liked_artists_add: Optional[List[str]] = None, disliked_artists_add: Optional[List[str]] = None, tool_context: Optional[ToolContext] = None)` *(async)***
   * Atomically and asynchronously updates the PII-scrubbed user profile in SQLite and synchronizes `tool_context.state["taste_profile"]`.
6. **`reset_user_taste_profile(user_id: str, reason: str, confirmed: bool = False, tool_context: Optional[ToolContext] = None)` *(async + Human-in-the-Loop)***
   * High-impact destructive tool wrapped with `FunctionTool(..., require_confirmation=True)` and explicit confirmation verification before purging SQLite preferences and listening history.
7. **`export_curated_playlist(user_id: str, session_id: str, export_format: Literal["markdown", "json"] = "markdown")`**
   * Generates a formatted Markdown listening guide or import-ready JSON playlist manifest from session recommendations.

---

## 8. Data Abstraction Layer (`DataProvider` Strategy) & Static Catalog Contracts

```python
class DataProvider(ABC):
    @abstractmethod
    def search_albums(
        self,
        artist: str,
        group_format: Optional[str] = None,
        era: Optional[str] = None,
        accessibility_level: Optional[str] = None,
        limit: int = 5,
    ) -> List[AlbumRecord]: ...

    @abstractmethod
    def get_lineage(self, artist: str, relationship: str = "all") -> LineageResult: ...

    @abstractmethod
    def get_bridges(self, anchor: str, vibe: str = "any") -> List[BridgeRecommendation]: ...

    @abstractmethod
    def analyze_playlist(self, playlist_url_or_id: str) -> PlaylistAnalysisReport: ...
```

### Provider Implementations
1. **`SpotifyDataProvider` (`src/data_providers/spotify_provider.py`):**
   * Automatically selected whenever `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` (or `SPOTIFY_API_KEY`) are present in `.env` / Secret Manager, using standard server-side **Client Credentials** (`SpotifyClientCredentials`, zero user OAuth popup).
   * **Playlist Audit (`analyze_playlist`):**
     * Fetches tracks via `sp.playlist_items(playlist_id)` and batch-fetches artist genres via `sp.artists(artist_ids)`.
     * Attempts exact BPM extraction via `sp.audio_features(track_ids)` wrapped in a resilient `try/except`: if Spotify returns `403 Forbidden` (expected for post-Nov 2024 apps in *Development Mode*), transparently computes tempo/energy estimates via a genre-and-duration heuristic without failing.
     * Maps dominant genres to `BridgeRecommendation` entries from the musicological vault.
   * Enriches album searches with live Spotify metadata (listen URLs, cover art, release dates, popularity) and gracefully falls back to `JsonDataProvider` on network outages.
2. **`JsonDataProvider` (`src/data_providers/json_provider.py` + `data/jazz_vault.json` + `data/sample_playlist.json`):**
   * Automatically active whenever Spotify credentials are absent or empty, guaranteeing 100% offline execution for local testing, `pytest`, GitHub Actions CI, and automated evaluation.

### Static Catalog Contracts & Maintenance Script

#### A. `data/jazz_vault.json` Schema & Construction Rules
* **Minimum Coverage Quota:** at least **80 distinct jazz artists** balanced across:
  * **Historical Currents:** Early Jazz / Dixieland, Swing, Bebop, Cool Jazz, Hard Bop, Soul Jazz, Modal Jazz, Post-Bop, Jazz-Rock / Fusion, Third Stream, Free Jazz / Avant-Garde, Spiritual Jazz, European & Contemporary Jazz.
  * **All 4 Knowledge Tiers (`beginner`, `novice`, `intermediate`, `connoisseur`)** and **all 3 Ensemble Formats (`solo`, `small_ensemble`, `big_band`)**.
* **Target JSON Structure:**
  ```json
  {
    "schema_version": "1.0",
    "artists": [
      {
        "id": "miles-davis",
        "name": "Miles Davis",
        "aliases": ["Miles Dewey Davis III"],
        "era": "1950s-1980s",
        "styles": ["Cool Jazz", "Hard Bop", "Modal Jazz", "Jazz Fusion"],
        "instruments": ["trumpet"],
        "harmonic_profile": "modal_modern",
        "recommended_levels": ["beginner", "novice", "intermediate", "connoisseur"],
        "bio_summary": "Pioneering trumpeter and bandleader who shaped cool jazz, modal jazz, post-bop, and electric fusion.",
        "spotify_artist_url": null,
        "albums": [
          {
            "title": "Kind of Blue",
            "year": 1959,
            "decade": "1950s",
            "label": "Columbia",
            "group_format": "small_ensemble",
            "key_track": "So What",
            "personnel": ["Miles Davis", "John Coltrane", "Bill Evans", "Cannonball Adderley", "Paul Chambers", "Jimmy Cobb"],
            "accessibility_level": "beginner",
            "why_listen": "Universal gateway album: spacious melodies, nocturnal warmth, and effortless modal interplay.",
            "spotify_album_url": null,
            "cover_image_url": null
          }
        ],
        "lineage": {
          "influences": [
            {"artist": "Charlie Parker", "justification": "Direct bebop mentor in Parker's 1945-1948 quintet."}
          ],
          "collaborators": [
            {"artist": "Gil Evans", "justification": "Orchestral partner on Birth of the Cool, Miles Ahead, and Sketches of Spain."},
            {"artist": "Bill Evans", "justification": "Harmonic architect of Kind of Blue."}
          ],
          "disciples": [
            {"artist": "Herbie Hancock", "justification": "Second Great Quintet pianist who expanded modal harmony and jazz-funk."},
            {"artist": "Wayne Shorter", "justification": "Principal composer of the Second Great Quintet and co-founder of Weather Report."}
          ]
        }
      }
    ],
    "bridges": [
      {
        "anchor_tags": ["debussy", "ravel", "classical", "classique", "impressionism", "piano"],
        "vibes": ["meditative", "lyrical", "calm", "intimate"],
        "target_subgenre": "Lyrical Piano Trio & Third Stream",
        "recommended_artist": "Bill Evans",
        "recommended_album": "Waltz for Debby",
        "year": 1961,
        "group_format": "small_ensemble",
        "accessibility_level": "beginner",
        "bridge_rationale": "Bill Evans translates Debussy and Ravel's impressionist harmonic voicings into intimate acoustic jazz trio conversation."
      }
    ]
  }
  ```
* **Minimum `bridges[]` Quota:** at least **12 documented bridges** spanning *Rock*, *Progressive Rock*, *Soul / R&B*, *Funk*, *Classical / Impressionism (Debussy, Ravel, Satie)*, *Contemporary Classical (Pierre Boulez, Olivier Messiaen)*, *Pop / Chanson*, *Hip-Hop*, *Electronic / Ambient*, *Blues*, *Dixieland / Ragtime*, and *Film Scores*.

#### B. `data/sample_playlist.json` Schema & Construction Rules
* Contains **16 to 20 eclectic tracks** across **Rock, Soul, Funk, and Classical** so offline playlist auditing produces a realistic multi-genre Sonic Fingerprint:
  ```json
  {
    "playlist_id": "sample_eclectic_bridge_01",
    "playlist_name": "Eclectic Horizons (Rock, Soul, Funk & Classical)",
    "owner": "demo_listener",
    "description": "Offline eclectic reference playlist for Sonic Fingerprint auditing.",
    "tracks": [
      {
        "track_name": "Time",
        "artist": "Pink Floyd",
        "album": "The Dark Side of the Moon",
        "release_year": 1973,
        "duration_ms": 413000,
        "genres": ["progressive rock", "psychedelic rock", "classic rock"],
        "tempo_bpm": 120.0,
        "energy": 0.62
      }
    ]
  }
  ```

#### C. Catalog Validation & Optional Spotify Enrichment Script (`scripts/enrich_vault_from_spotify.py`)
* **`python scripts/enrich_vault_from_spotify.py --validate-only` (100% offline, CI-safe):**
  * Validates `data/jazz_vault.json` and `data/sample_playlist.json` against strict Pydantic schemas, enforces `>= 80` artists, checks full coverage across all 4 knowledge tiers and 3 ensemble formats, and verifies `bridges[]` integrity.
* **`python scripts/enrich_vault_from_spotify.py --enrich` (when Spotify keys are set in `.env`):**
  * Uses Spotify *Client Credentials* with rate-limit handling to idempotently populate optional `spotify_artist_url`, `spotify_album_url`, and `cover_image_url` fields inside `data/jazz_vault.json`.

---

## 9. Asynchronous Persistence & SQLite Schema (`src/memory/sqlite_memory.py`)

All reads and writes run asynchronously via `aiosqlite` and pass through `PIIRedactor` prior to storage:

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    knowledge_level TEXT DEFAULT 'beginner' CHECK(knowledge_level IN ('beginner', 'novice', 'intermediate', 'connoisseur')),
    harmonic_preference TEXT DEFAULT 'standard_swing',
    formation_preference TEXT DEFAULT 'small_ensemble',
    sonic_fingerprint_json TEXT DEFAULT '{}',
    compacted_summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playlist_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    playlist_ref TEXT, -- Playlist ID or URL scrubbed of ?si= tracking parameters
    playlist_name TEXT,
    dominant_genres_json TEXT,
    estimated_tempo_bpm REAL,
    fingerprint_report_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS artist_preferences (
    user_id TEXT,
    artist_name TEXT,
    preference_type TEXT CHECK(preference_type IN ('liked', 'disliked', 'known')),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, artist_name),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    album_title TEXT,
    artist_name TEXT,
    recommended_by_agent TEXT,
    user_feedback TEXT DEFAULT 'neutral',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);
```

---

## 10. Complete Repository Structure (Root)

```
jazz-concierge/
├── .github/
│   └── workflows/
│       └── ci.yml                       # GitHub Actions CI (Ruff + Catalog Validation + Pytest + Offline Eval)
├── data/
│   ├── jazz_vault.json                  # Autonomous catalog of 80+ jazz figures, discographies, lineages & bridges
│   └── sample_playlist.json             # Offline eclectic playlist (Rock, Soul, Funk, Classical) for audit fallback
├── infra/
│   ├── main.tf                          # Terraform IaC (Cloud Run, Secret Manager, Vertex AI APIs)
│   ├── variables.tf                     # Parameterized GCP variables (incl. allow_public_access)
│   └── outputs.tf                       # Infrastructure outputs
├── scripts/
│   └── enrich_vault_from_spotify.py     # Offline catalog validator (>=80 artists) & optional Spotify enricher
├── src/
│   ├── __init__.py
│   ├── agent.py                         # Canonical ADK entrypoint exposing `root_agent` and `app`
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py                  # .env loader, Gemini 4 strategic model routing & defaults
│   │   └── secrets.py                   # Google Cloud Secret Manager client + .env fallback
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── profiler.py                  # JazzProfilerAgent (gemini-4-hydrogen)
│   │   ├── musicologist.py              # JazzMusicologistAgent (gemini-4-neon)
│   │   ├── curator.py                   # JazzCuratorAgent (gemini-4-carbon)
│   │   └── guardrails.py                # ADK BasePlugin, Policy/Guardrail callbacks & history compaction
│   ├── data_providers/
│   │   ├── __init__.py
│   │   ├── base.py                      # Abstract DataProvider interface & Pydantic domain models
│   │   ├── json_provider.py             # Offline provider backed by jazz_vault.json & sample_playlist.json
│   │   └── spotify_provider.py          # Live Spotify Web API provider (Client Credentials + graceful fallback)
│   ├── memory/
│   │   ├── __init__.py
│   │   └── sqlite_memory.py             # Async SQLite manager (aiosqlite) + context compaction helpers
│   ├── telemetry/
│   │   ├── __init__.py
│   │   ├── logger.py                    # Structured JSON logging + Intent vs. Outcome capture
│   │   ├── pii_redactor.py              # Active PII & URL tracking parameter redaction
│   │   └── tracer.py                    # OpenTelemetry distributed tracing setup
│   ├── tools/
│   │   ├── __init__.py
│   │   └── jazz_tools.py                # Typed ADK FunctionTools, Guided Error Handling & HITL confirmation
│   ├── app.py                           # Interactive Streamlit Web UI (+ "Analyze my playlist" & Demo buttons)
│   └── cli.py                           # Interactive Terminal CLI
├── tests/
│   ├── __init__.py
│   ├── eval/
│   │   ├── eval_config.yaml             # Evaluation criteria & quality thresholds
│   │   ├── golden_dataset.json          # Reference scenarios (beginner, playlist audit, novice, connoisseur, bridges)
│   │   └── test_eval_harness.py         # Static/offline golden-dataset regression harness
│   ├── test_agents_and_guardrails.py
│   ├── test_data_providers.py           # Spotify mock tests + offline JSON catalog & playlist audit tests
│   ├── test_memory_and_pii.py           # Async SQLite persistence + PII & ?si= scrubbing tests
│   └── test_tools.py                    # Pydantic validation, Guided Errors, HITL & tool execution tests
├── .env.example                         # Safe configuration template (.env is gitignored)
├── .gitignore                           # Excludes .env, *.pdf, *.db, caches & Terraform state
├── Dockerfile                           # Production container image ready for local Docker & Cloud Run
├── pyproject.toml                       # Package metadata, dependencies & Ruff/Pytest config
├── requirements.txt                     # Pinned runtime & test dependencies
├── spec.md                              # Self-contained technical & functional specification (this file)
├── README.md                            # Complete primary documentation in English (links to README-fr.md & spec.md)
└── README-fr.md                         # Complete French translation of README.md (links back to README.md & spec.md)
```

---

## 11. Google ADK-Python Development & Code Quality Standards

1. **Native ADK Primitives Only (No LangChain / CrewAI Wrappers):**
   * Built strictly on `google.adk.agents` (`Agent` / `LlmAgent`, `SequentialAgent`), `google.adk.apps.App`, `google.adk.runners.Runner`, `google.adk.sessions`, and `google.genai.types`.
   * Expose canonical `root_agent` and `app = App(name="jazz_concierge", root_agent=root_agent, plugins=[...], events_compaction_config=...)` in `src/agent.py` (and re-export in `src/__init__.py`) for out-of-the-box compatibility with `adk run`, `adk web`, and `agents-cli`.
2. **ADK Tool Conventions (`FunctionTool` & `ToolContext`):**
   * Declare tools as strongly typed Python callables (`def` or `async def`) wrapped with `google.adk.tools.FunctionTool` where confirmation or metadata is needed (never use third-party `@tool` decorators).
   * Accept `tool_context: Optional[ToolContext] = None` when reading/updating shared session state (`tool_context.state["taste_profile"]`, `tool_context.state["sonic_fingerprint"]`).
   * Protect `reset_user_taste_profile` with `FunctionTool(func=reset_user_taste_profile, require_confirmation=True)` plus explicit confirmation validation.
   * Always return JSON-serializable `dict` payloads validated via Pydantic v2 `model_dump()`.
3. **Multi-Agent State Passing Rule:**
   * **ADK Constraint:** Never set `output_schema` on an `Agent` that also declares `tools` or `sub_agents` (in `google-adk`, `output_schema` disables tool calling and agent transfer).
   * Pass structured context cleanly across agents using `output_key` (e.g., `output_key="profiler_brief"`, `output_key="musicologist_candidates"`) and `{state_key}` placeholders in downstream agent instructions.
4. **Python Tooling & Deterministic Testing:**
   * **Python 3.11+**, compatible with both `uv` and `pip` (`pyproject.toml` + `requirements.txt`), zero `ruff check .` warnings.
   * **Strict Separation of Unit Tests vs. Behavioral Evaluation:**
     * `pytest` (`pytest-asyncio`) unit tests run 100% deterministically offline against tools, Pydantic contracts, mocked Spotify responses, JSON fallbacks, async SQLite operations, guardrails, and PII redaction (never asserting non-deterministic free-form LLM prose).
     * End-to-end behavioral quality is validated via `tests/eval/golden_dataset.json` + `eval_config.yaml` / `agents-cli eval`.

---

## 12. Bilingual Documentation Specification (`README.md` & `README-fr.md`)

The repository provides two synchronized, comprehensive documentation files at the root:
* **`README.md` (English — primary documentation)** featuring an immediate top language switch banner: `🇬🇧 English | [🇫🇷 Lire la documentation en français (README-fr.md)](./README-fr.md)`.
* **`README-fr.md` (French — complete translation)** featuring the reciprocal banner: `[🇬🇧 Read the documentation in English (README.md)](./README.md) | 🇫🇷 Français`.

### Required Sections in Both `README.md` and `README-fr.md`

1. **Header, Badges, Project Context & Spec-Driven Vibecoding Note**
   * Language switcher (`README.md` ↔ `README-fr.md`) and technical badges (Python, Google ADK, Gemini 4, OpenTelemetry, Terraform, CI, License).
   * Product vision of **Jazz Concierge (`MilesAgent`)**, noting neutrally that it was built as a capstone/demonstration project for an AI agent certification, and highlighting that the project was **vibecoded using Spec-Driven Development with Google Antigravity & Gemini** based on [`spec.md`](./spec.md) (with a clickable link to `spec.md` so anyone can inspect or regenerate the system from its specification).

2. **⚡ Fast Test / 2-Minute Quickstart (*"Test the App Immediately"*)**
   * Placed right near the top so any newcomer knows the exact copy-paste steps to test the application right away:
     1. **Clone & install** (`uv sync` or `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`).
     2. **Zero-Key Instant Verification (Offline Suite):** run `pytest` and `python scripts/enrich_vault_from_spotify.py --validate-only` with zero API keys to verify all tools, SQLite memory, playlist audit fallback, and golden dataset checks in seconds.
     3. **Minimal Gemini Setup:** copy `cp .env.example .env`, authenticate with GCP ADC (`gcloud auth application-default login` + set `GOOGLE_CLOUD_PROJECT` in `.env`, or set `GOOGLE_API_KEY`).
     4. **Launch & Try 3 Ready-Made Scenarios:** start Streamlit (`streamlit run src/app.py` at `http://127.0.0.1:8501`) or CLI (`python -m src.cli`), and test: (a) complete *Beginner* prompt ("I know nothing about jazz, I love Pink Floyd and Debussy"), (b) 1-click *"Test with Sample Playlist"* or pasting a public Spotify playlist URL, and (c) exporting a curated Markdown/JSON playlist.

3. **Complete Feature Guide & User-Configurable Options (with Exact Defaults)**
   * Clear explanation of all user-facing features (4 knowledge tiers `beginner`/`novice`/`intermediate`/`connoisseur`, harmonic preferences, ensemble formats, playlist auditing, anti-duplicate listening history, Human-in-the-Loop profile reset, Markdown/JSON export).
   * **Exhaustive Environment Variables & Configuration Table** documenting every variable, accepted values, and **exact default value / fallback behavior when the variable is unset or empty** (`GOOGLE_GENAI_USE_VERTEXAI` → `TRUE`, `GOOGLE_CLOUD_LOCATION` → `global`, `PROFILER_MODEL` → `gemini-4-hydrogen`, `MUSICOLOGIST_MODEL` → `gemini-4-neon`, `CURATOR_MODEL` → `gemini-4-carbon`, `GEMINI_MODEL` → empty = per-agent routing preserved, `SQLITE_DB_PATH` → `src/memory/user_profile.db`, `USE_SECRET_MANAGER` → `FALSE`, `LOG_LEVEL` → `INFO`, `OTEL_EXPORTER_GCP_TRACE` → `FALSE`, etc.).

4. **With vs. Without Spotify API Credentials & Succinct Key Creation Guide**
   * **Side-by-side behavior explanation:**
     * **Without Spotify API keys (unset or empty — default):** zero errors or degraded UX; `JsonDataProvider` automatically serves `data/jazz_vault.json` (80+ jazz legends, albums, lineages, bridges) and `data/sample_playlist.json` for playlist auditing.
     * **With Spotify API keys (`SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` set):** `SpotifyDataProvider` activates automatically, enriching album results with live cover art, popularity, and Spotify URLs, and auditing any user-created public Spotify playlist in real time (with automatic offline fallback if offline).
   * **Succinct 4-step guide to creating a Spotify Developer key:**
     1. Visit [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and click **Create app**.
     2. Enter an app name/description, add `http://127.0.0.1:8501/callback` under **Redirect URIs**, and click **Add** *(note: Spotify rejects `localhost`; always use `127.0.0.1` for local loopback)*.
     3. Check **Web API**, accept the terms, and click **Save**.
     4. Open **Settings** and copy **Client ID** (`SPOTIFY_CLIENT_ID`) and **Client Secret** (`SPOTIFY_CLIENT_SECRET`) into `.env`.

5. **Architecture & Multi-Agent Orchestration**
   * Mermaid / ASCII architecture diagram, Gemini 4 Strategic Model Routing breakdown, table of all 7 ADK tools, SQLite schema, and OpenTelemetry + JSON *Intent vs. Outcome* + PII redaction pipeline.

6. **Developer Workflows, Catalog Maintenance & `agents-cli` Integration**
   * Commands for `agents-cli` (`agents-cli info`, `agents-cli run`, `agents-cli playground`, `agents-cli eval`), `adk web`, and `scripts/enrich_vault_from_spotify.py` (`--validate-only` / `--enrich`).

7. **Comprehensive Deployment Guide (Private / Localhost Endpoints & Public Cloud Deployment)**
   * **Mode 1 — Localhost / Private Docker Endpoint (`127.0.0.1`):** running locally or building & binding Docker strictly to loopback (`127.0.0.1:8080`).
   * **Mode 2 — Private / IAM-Restricted Cloud Run Deployment (with HTTPS Proxy Forwarding):** for organizations disallowing public `allUsers` access; Terraform deployment with `allow_public_access = false`, IAM `roles/run.invoker` grant, and local authenticated HTTPS forwarding via `gcloud run services proxy --port=8080` or OIDC/IAP reverse-proxy.
   * **Mode 3 — Public Cloud Run Deployment:** full public deployment via Terraform (`-var="allow_public_access=true"`), `gcloud run deploy --allow-unauthenticated`, or `agents-cli deploy`, wired to Google Cloud Secret Manager (`USE_SECRET_MANAGER=TRUE`).

8. **Testing, Golden Dataset Evaluation, CI/CD, Troubleshooting & License**
   * Offline `pytest` suite, `ruff` linting, `tests/eval/` golden dataset harness, GitHub Actions workflow, FAQ (Spotify *Development Mode* `audio-features` fallback, model overrides, Cloud Run proxy), and repository license.

9. **Reproducible Spec-Driven Vibecoding Prompt**
   * Both `README.md` (and `README-fr.md`) must include a dedicated section (**"Reproducible Vibecoding Prompt (Google Antigravity & Gemini)"**) reproducing the exact English generation prompt below so any reader can regenerate and verify the entire project from [`spec.md`](./spec.md):

```text
Read @[spec.md] carefully — it is the authoritative, self-contained technical and functional specification for `jazz-concierge`. Generate the complete project end-to-end, strictly following every section and contract defined in `spec.md`.

Mandatory execution steps:
1. Preserve Local Environment & Repository Hygiene:
   - Keep the existing local `.env` file untouched if present (never overwrite it, and never copy or print real API keys or real GCP project IDs into any tracked file; `.env.example`, `infra/`, `README.md`, and `README-fr.md` must use generic placeholders such as `your-gcp-project-id`).
   - Keep `.gitignore` intact (ignoring `.env`, `*.pdf`, and `*.db`, while keeping `spec.md` tracked) and preserve any existing local `git config --local` author settings.

2. Build Data Catalogs & Maintenance Script:
   - Generate `data/jazz_vault.json` with at least 80 distinct jazz artists spanning all major jazz eras, all 3 ensemble formats (`solo`, `small_ensemble`, `big_band`), all 4 knowledge tiers (`beginner`, `novice`, `intermediate`, `connoisseur`), complete lineages (`influences`, `collaborators`, `disciples`), and at least 12 non-jazz stylistic bridges (`bridges[]`).
   - Generate `data/sample_playlist.json` (16–20 eclectic tracks across Rock, Soul, Funk, and Classical) and `scripts/enrich_vault_from_spotify.py` (`--validate-only` and `--enrich`).

3. Implement Full Google ADK-Python Architecture, Infra & UIs:
   - Implement the complete repository layout from `spec.md`: `src/config/` (`.env` + Google Cloud Secret Manager), `src/data_providers/` (`SpotifyDataProvider` using Client Credentials with resilient `403` `audio-features` fallback + `JsonDataProvider`), `src/memory/` (async `aiosqlite` + context compaction), `src/telemetry/` (structured JSON logs, Intent vs. Outcome capture, OpenTelemetry spans, `PIIRedactor` scrubbing PII and `?si=` tracking params), `src/tools/` (all 7 Pydantic-typed ADK tools with Guided Error Handling and Human-in-the-Loop confirmation on `reset_user_taste_profile`), `src/agents/` (`JazzProfilerAgent` on `gemini-4-hydrogen`, `JazzMusicologistAgent` on `gemini-4-neon`, `JazzCuratorAgent` on `gemini-4-carbon`, policy/guardrail plugin), `src/agent.py` (`root_agent` and `app`), `src/app.py` (Streamlit UI with Spotify playlist audit input & sample playlist button), `src/cli.py`, `infra/` (Terraform), `Dockerfile`, `pyproject.toml`, `requirements.txt`, `.env.example`, and `.github/workflows/ci.yml`.

4. Write Bilingual Documentation (`README.md` & `README-fr.md`):
   - Author `README.md` in English and `README-fr.md` as its complete French translation per Section 12 of `spec.md`, including top cross-links, neutral AI agent certification project framing (without naming any specific certification or employer), the Spec-Driven Vibecoding credit using Google Antigravity & Gemini with a link to `spec.md`, this exact generation prompt in a dedicated reproducible-build section, the `⚡ Fast Test` quickstart, the full user-configurable options & defaults table, With vs. Without Spotify keys + succinct Spotify Developer Dashboard setup (`127.0.0.1` redirect URI rule), architecture & `agents-cli` commands, and the 3-mode deployment guide (Localhost/Docker, Private IAM Cloud Run with HTTPS proxy, Public Cloud Run).

5. Offline Verification, Live End-to-End Testing & Interactive Connection Checks:
   - Set up the local Python environment (`.venv` via `uv` or `pip`) and run all offline checks until 100% pass with zero errors or warnings:
     a) `ruff check .`
     b) `python scripts/enrich_vault_from_spotify.py --validate-only`
     c) `pytest` (isolated from local `.env` so offline unit tests and `tests/eval/golden_dataset.json` pass deterministically without credentials).
   - Next, run live end-to-end integration smoke tests using `.env`:
     a) Check Google Cloud / Vertex AI authentication (Application Default Credentials) and execute a real end-to-end multi-agent turn across `JazzProfilerAgent` -> `JazzMusicologistAgent` -> `JazzCuratorAgent`. If `.env` is missing `GOOGLE_CLOUD_PROJECT` or if `gcloud` ADC credentials are not logged in / expired, pause and ask me to run the required `gcloud auth application-default login` commands (giving me the exact commands), then re-run the live test once I confirm.
     b) If Spotify credentials are present in `.env`, run a live `SpotifyDataProvider` catalog & playlist test; if missing or restricted, inform me and ask whether I want to configure them or proceed with verified offline fallback.

6. Security Audit & GitHub SSH Deployment:
   - Inspect `git status` and scan all tracked/staged files to guarantee zero secrets, `.env` values, `.pdf` files, corporate emails, or real GCP project IDs are staged.
   - Commit all project files cleanly to `main` (including `spec.md`, `README.md`, and `README-fr.md`) and push to `origin main` over SSH (running outside the sandbox to access the local SSH agent). If SSH authentication to GitHub fails (e.g., key not loaded in `ssh-agent`), ask me to authenticate (`ssh-add`) and retry the push once I confirm.
```
